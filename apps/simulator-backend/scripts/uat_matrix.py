#!/usr/bin/env python3
"""Run a configurable, integration-neutral voice-agent UAT matrix.

The runner intentionally knows nothing about a particular company, agent API,
tenant, storage bucket, or customer payload. Integrations are expressed as
command or HTTP adapters in YAML. Secrets are loaded from environment
variables and are never written to the report.

Examples:
    python scripts/uat_matrix.py --config config/uat.example.yaml --check
    python scripts/uat_matrix.py --config config/uat.example.yaml --dry-run
    python scripts/uat_matrix.py --config config/uat.local.yaml
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Protocol
import urllib.error
import urllib.request
import uuid

import yaml


class ConfigError(ValueError):
    """Raised when the matrix configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class StepResult:
    adapter: str
    ok: bool
    status: str
    duration_s: float
    detail: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    adapter: Adapter
    attempts: int = 1
    interval_s: float = 0
    run_if: str = "success"
    capture_json: dict[str, str] | None = None
    report_output: bool = False


class Adapter(Protocol):
    def preview(self, context: dict[str, str]) -> str: ...

    def execute(self, context: dict[str, str]) -> StepResult: ...


def _render_string(value: str, context: dict[str, str]) -> str:
    try:
        return value.format_map(context)
    except KeyError as exc:
        raise ConfigError(f"unknown template variable: {exc.args[0]}") from exc


def _secret(value: Any) -> str:
    if not isinstance(value, dict) or set(value) != {"from_env"}:
        raise ConfigError("secret values must use {from_env: ENVIRONMENT_VARIABLE}")
    name = str(value["from_env"])
    resolved = os.environ.get(name)
    if not resolved:
        raise ConfigError(f"required environment variable is not set: {name}")
    return resolved


def _bounded(text: str | bytes, limit: int = 12_000) -> str:
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... output truncated ..."


class NoopAdapter:
    def __init__(self, spec: dict[str, Any], _base_dir: Path) -> None:
        self.message = str(spec.get("message") or "No external action configured.")

    def preview(self, context: dict[str, str]) -> str:
        return _render_string(self.message, context)

    def execute(self, context: dict[str, str]) -> StepResult:
        started = time.monotonic()
        message = self.preview(context)
        return StepResult("noop", True, "completed", time.monotonic() - started, message)


class CommandAdapter:
    def __init__(self, spec: dict[str, Any], base_dir: Path) -> None:
        argv = spec.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise ConfigError("command adapter requires a non-empty string list in argv")
        self.argv = argv
        self.timeout_s = float(spec.get("timeout_seconds", 420))
        if not 1 <= self.timeout_s <= 3600:
            raise ConfigError("command timeout_seconds must be between 1 and 3600")
        configured_cwd = Path(str(spec.get("cwd") or "."))
        self.cwd = configured_cwd if configured_cwd.is_absolute() else base_dir / configured_cwd
        raw_env = spec.get("env") or {}
        if not isinstance(raw_env, dict):
            raise ConfigError("command env must be a mapping")
        self.env = raw_env

    def _argv(self, context: dict[str, str]) -> list[str]:
        return [_render_string(item, context) for item in self.argv]

    def preview(self, context: dict[str, str]) -> str:
        return json.dumps({"argv": self._argv(context), "cwd": str(self.cwd.resolve())})

    def execute(self, context: dict[str, str]) -> StepResult:
        started = time.monotonic()
        env = os.environ.copy()
        env.update(
            {
                "UAT_SCENARIO": context["scenario"],
                "UAT_RUN_ID": context["run_id"],
            }
        )
        for key, value in context.items():
            if key not in {"scenario", "run_id"}:
                env[f"UAT_{key.upper()}"] = value
        for key, value in self.env.items():
            env[str(key)] = (
                _secret(value)
                if isinstance(value, dict)
                else _render_string(str(value), context)
            )
        try:
            completed = subprocess.run(
                self._argv(context),
                cwd=self.cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return StepResult(
                "command",
                False,
                "timeout",
                time.monotonic() - started,
                self.preview(context),
                _bounded(exc.stdout or ""),
                _bounded(exc.stderr or ""),
            )
        return StepResult(
            "command",
            completed.returncode == 0,
            f"exit_{completed.returncode}",
            time.monotonic() - started,
            self.preview(context),
            _bounded(completed.stdout),
            _bounded(completed.stderr),
        )


class HttpAdapter:
    def __init__(self, spec: dict[str, Any], _base_dir: Path) -> None:
        self.url = str(spec.get("url") or "")
        if not self.url:
            raise ConfigError("http adapter requires url")
        self.method = str(spec.get("method") or "POST").upper()
        if self.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ConfigError(f"unsupported HTTP method: {self.method}")
        self.timeout_s = float(spec.get("timeout_seconds", 60))
        self.body = spec.get("json")
        self.headers = spec.get("headers") or {}
        if not isinstance(self.headers, dict):
            raise ConfigError("http headers must be a mapping")

    def _render_json(self, value: Any, context: dict[str, str]) -> Any:
        if isinstance(value, str):
            return _render_string(value, context)
        if isinstance(value, list):
            return [self._render_json(item, context) for item in value]
        if isinstance(value, dict):
            if set(value) == {"from_env"}:
                return _secret(value)
            return {str(key): self._render_json(item, context) for key, item in value.items()}
        return value

    def preview(self, context: dict[str, str]) -> str:
        return json.dumps(
            {
                "method": self.method,
                "url": _render_string(self.url, context),
                "header_names": sorted(str(key) for key in self.headers),
            }
        )

    def execute(self, context: dict[str, str]) -> StepResult:
        started = time.monotonic()
        url = _render_string(self.url, context)
        headers = {
            str(key): (
                _secret(value)
                if isinstance(value, dict)
                else _render_string(str(value), context)
            )
            for key, value in self.headers.items()
        }
        data = None
        if self.body is not None:
            data = json.dumps(self._render_json(self.body, context)).encode()
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=self.method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response_text = response.read().decode(errors="replace")
                return StepResult(
                    "http",
                    200 <= response.status < 300,
                    f"http_{response.status}",
                    time.monotonic() - started,
                    self.preview(context),
                    _bounded(response_text),
                )
        except urllib.error.HTTPError as exc:
            return StepResult(
                "http",
                False,
                f"http_{exc.code}",
                time.monotonic() - started,
                self.preview(context),
                _bounded(exc.read().decode(errors="replace")),
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            return StepResult(
                "http",
                False,
                "request_failed",
                time.monotonic() - started,
                f"{self.preview(context)}: {exc}",
            )


ADAPTERS: dict[str, type[NoopAdapter | CommandAdapter | HttpAdapter]] = {
    "noop": NoopAdapter,
    "command": CommandAdapter,
    "http": HttpAdapter,
}


def _adapter(spec: Any, base_dir: Path) -> Adapter:
    if spec is None:
        return NoopAdapter({}, base_dir)
    if not isinstance(spec, dict):
        raise ConfigError("adapter configuration must be a mapping")
    kind = str(spec.get("type") or "")
    adapter_type = ADAPTERS.get(kind)
    if adapter_type is None:
        raise ConfigError(f"unknown adapter type: {kind!r}")
    return adapter_type(spec, base_dir)


def _workflow(config: dict[str, Any], base_dir: Path) -> list[WorkflowStep]:
    """Build an ordered workflow, preserving the version-1 trigger/verifier form."""

    raw_steps = config.get("steps")
    if raw_steps is None:
        if "trigger" not in config:
            raise ConfigError("configuration needs trigger or steps")
        raw_steps = [{"name": "trigger", "adapter": config["trigger"]}]
        if config.get("verifier"):
            raw_steps.append({"name": "verify", "adapter": config["verifier"]})
    elif "trigger" in config or "verifier" in config:
        raise ConfigError("use either steps or the legacy trigger/verifier keys, not both")

    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigError("steps must be a non-empty list")

    steps: list[WorkflowStep] = []
    names: set[str] = set()
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ConfigError("every workflow step must be a mapping")
        name = str(raw.get("name") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ConfigError(
                "step names must start with a lowercase letter and contain only "
                "lowercase letters, digits, underscores, or hyphens"
            )
        if name in names:
            raise ConfigError(f"duplicate workflow step name: {name}")
        names.add(name)

        attempts = int(raw.get("attempts", 1))
        if not 1 <= attempts <= 120:
            raise ConfigError(f"{name}: attempts must be between 1 and 120")
        interval_s = float(raw.get("interval_seconds", 0))
        if not 0 <= interval_s <= 300:
            raise ConfigError(f"{name}: interval_seconds must be between 0 and 300")
        run_if = str(raw.get("run_if") or "success")
        if run_if not in {"success", "always"}:
            raise ConfigError(f"{name}: run_if must be success or always")
        capture_json = raw.get("capture_json")
        if capture_json is not None:
            if not isinstance(capture_json, dict) or not capture_json:
                raise ConfigError(f"{name}: capture_json must be a non-empty mapping")
            for key, path in capture_json.items():
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(key)):
                    raise ConfigError(f"{name}: invalid captured variable name: {key}")
                if not isinstance(path, str) or not path:
                    raise ConfigError(f"{name}: capture_json paths must be strings")

        report_output = raw.get("report_output", False)
        if not isinstance(report_output, bool):
            raise ConfigError(f"{name}: report_output must be true or false")

        steps.append(
            WorkflowStep(
                name=name,
                adapter=_adapter(raw.get("adapter"), base_dir),
                attempts=attempts,
                interval_s=interval_s,
                run_if=run_if,
                capture_json={
                    str(key): str(path) for key, path in (capture_json or {}).items()
                }
                or None,
                report_output=report_output,
            )
        )
    return steps


def _json_path(value: Any, path: str) -> Any:
    """Resolve a dotted JSON object/list path such as ``data.calls.0.id``."""

    current = value
    for component in path.split("."):
        if isinstance(current, dict) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit():
            index = int(component)
            if index >= len(current):
                raise ConfigError(f"JSON path index is out of range: {path}")
            current = current[index]
        else:
            raise ConfigError(f"JSON path was not found: {path}")
    if isinstance(current, (dict, list)):
        return json.dumps(current, separators=(",", ":"))
    if current is None:
        return ""
    return str(current)


def _capture(step: WorkflowStep, result: StepResult, context: dict[str, str]) -> StepResult:
    if not step.capture_json or not result.ok:
        return result
    try:
        payload = json.loads(result.stdout)
        captured = {
            key: _json_path(payload, path) for key, path in step.capture_json.items()
        }
        context.update(captured)
    except (ConfigError, json.JSONDecodeError) as exc:
        return replace(
            result,
            ok=False,
            status="capture_failed",
            detail=f"{result.detail}; {exc}",
        )
    return result


def _execute_step(
    step: WorkflowStep, context: dict[str, str]
) -> tuple[StepResult, int]:
    started = time.monotonic()
    result: StepResult | None = None
    for attempt in range(1, step.attempts + 1):
        result = _capture(step, step.adapter.execute(context), context)
        if result.ok or attempt == step.attempts:
            return replace(result, duration_s=time.monotonic() - started), attempt
        if step.interval_s:
            time.sleep(step.interval_s)
    raise AssertionError("workflow attempt loop did not return")


def _report_result(step: WorkflowStep, result: StepResult) -> dict[str, Any]:
    reported = asdict(result)
    reported["output_redacted"] = not step.report_output
    if not step.report_output:
        # Bodies, process output, rendered URLs, and arguments can contain
        # customer data even when credentials came only from the environment.
        reported["detail"] = ""
        reported["stdout"] = ""
        reported["stderr"] = ""
    return reported


def _run_workflow(
    workflow: list[WorkflowStep], context: dict[str, str]
) -> tuple[bool, list[dict[str, Any]]]:
    workflow_ok = True
    step_reports: list[dict[str, Any]] = []
    for step in workflow:
        if not workflow_ok and step.run_if != "always":
            step_reports.append(
                {
                    "name": step.name,
                    "run_if": step.run_if,
                    "skipped": "prior_step_failed",
                    "attempts": 0,
                    "captured_keys": [],
                    "result": None,
                }
            )
            continue
        result, attempts = _execute_step(step, context)
        if not result.ok:
            workflow_ok = False
        step_reports.append(
            {
                "name": step.name,
                "run_if": step.run_if,
                "skipped": None,
                "attempts": attempts,
                "captured_keys": sorted((step.capture_json or {}).keys())
                if result.ok
                else [],
                "result": _report_result(step, result),
            }
        )
    return workflow_ok, step_reports


def _cases(config: dict[str, Any], selected: set[str]) -> list[dict[str, str]]:
    raw_cases = config.get("matrix")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ConfigError("matrix must contain at least one scenario")
    cases: list[dict[str, str]] = []
    for raw in raw_cases:
        if isinstance(raw, str):
            scenario, variables = raw, {}
        elif isinstance(raw, dict):
            scenario = str(raw.get("scenario") or "")
            variables = raw.get("variables") or {}
            if not isinstance(variables, dict):
                raise ConfigError(f"variables for {scenario!r} must be a mapping")
        else:
            raise ConfigError("matrix entries must be scenario strings or mappings")
        if not scenario:
            raise ConfigError("every matrix entry needs a scenario")
        if selected and scenario not in selected:
            continue
        context = {"scenario": scenario, **{str(k): str(v) for k, v in variables.items()}}
        cases.append(context)
    if not cases:
        raise ConfigError("no matrix scenarios matched --scenario")
    return cases


def load_config(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")
    if raw.get("version") != 1:
        raise ConfigError("configuration version must be 1")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scenario", action="append", default=[], help="run only this scenario")
    parser.add_argument("--check", action="store_true", help="validate configuration only")
    parser.add_argument("--dry-run", action="store_true", help="render actions without executing")
    parser.add_argument("--output", type=Path, help="JSON report path")
    args = parser.parse_args()

    try:
        config_path = args.config.resolve()
        config = load_config(config_path)
        cases = _cases(config, set(args.scenario))
        workflow = _workflow(config, config_path.parent)
    except (ConfigError, OSError, yaml.YAMLError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"valid: {config_path} ({len(cases)} scenarios)")
        return 0

    delay_s = float(config.get("delay_seconds", 0))
    continue_on_failure = bool(config.get("continue_on_failure", True))
    configured_artifacts_dir = Path(
        str(config.get("artifacts_dir") or "results/uat/artifacts")
    )
    reports: list[dict[str, Any]] = []
    for index, initial_context in enumerate(cases):
        context = {**initial_context, "run_id": uuid.uuid4().hex}
        artifact_dir = configured_artifacts_dir / context["run_id"]
        context["artifact_dir"] = str(artifact_dir.resolve())
        print(f"[{index + 1}/{len(cases)}] {context['scenario']}")
        if args.dry_run:
            for step in workflow:
                print(f"  {step.name}: {step.adapter.preview(context)}")
                for key in step.capture_json or {}:
                    context[key] = f"<captured:{key}>"
            continue

        artifact_dir.mkdir(parents=True, exist_ok=True)
        passed, step_reports = _run_workflow(workflow, context)
        reports.append(
            {
                "scenario": context["scenario"],
                "run_id": context["run_id"],
                "passed": passed,
                "artifact_dir": context["artifact_dir"],
                "steps": step_reports,
            }
        )
        statuses = ", ".join(
            f"{step['name']}="
            f"{step['result']['status'] if step['result'] else 'skipped'}"
            for step in step_reports
        )
        print(f"  {'PASS' if passed else 'FAIL'} ({statuses})")
        if not passed and not continue_on_failure:
            break
        if delay_s > 0 and index + 1 < len(cases):
            time.sleep(delay_s)

    if args.dry_run:
        return 0

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("results") / "uat" / f"matrix-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "config": str(config_path),
        "total": len(reports),
        "passed": sum(1 for report in reports if report["passed"]),
        "results": reports,
    }
    output.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{summary['passed']}/{summary['total']} passed -> {output}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
