from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.config import validate_serve_configuration


def test_validation_cli_reports_all_errors_without_creating_database(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    database = tmp_path / "unused.db"
    (tmp_path / "bad.yaml").write_text("name: bad\nmachine:\n  main:\n    - wait: -1\n")
    (tmp_path / "broken.yaml").write_text("machine: [")
    monkeypatch.setenv("SIMULATOR_DB_PATH", str(database))
    assert main(["scenarios", "validate", "--all", "--scenarios-dir", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert len(report["scenarios"]) == 2
    assert all(entry.get("error") for entry in report["scenarios"])
    assert not database.exists()


@pytest.mark.parametrize("selection", ["example", "file"])
def test_validation_cli_accepts_named_or_file_scenarios(
    tmp_path: Path, monkeypatch, capsys, selection: str,
) -> None:
    scenario = tmp_path / "custom.yaml"
    scenario.write_text("name: example\nexpect: {}\nmachine:\n  main:\n    - hangup: true\n")
    name = str(scenario) if selection == "file" else selection
    assert main(["scenarios", "validate", "--scenario", name, "--scenarios-dir", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["scenarios"][0]["name"] == "example"


def test_zero_setup_cli_runs_every_ivr_model_without_creating_a_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    database = tmp_path / "should-not-exist.db"
    monkeypatch.setenv("SIMULATOR_DB_PATH", str(database))
    assert main(["scenarios", "simulate"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert len(output) == 7
    assert {item["scenario_id"] for item in output} >= {
        "connect-1501",
        "retry-after-silence",
    }
    assert not database.exists()


def test_zero_setup_cli_accepts_named_pbx_scenario_alias_and_runtime_override(
    monkeypatch,
    capsys,
) -> None:
    argv = [
        "scenarios", "simulate", "pbx_sales_queue_answers", "--kind", "pbx",
        "--set", "sales-a=busy", "--set", "sales-b=offline",
    ]
    assert main(argv) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["scenario_id"] == "sales-mainline-open"
    assert output["outcome"] == "overflow"
    assert output["overrides"] == {"sales-a": "busy", "sales-b": "offline"}
    assert any(step["kind"] == "cdr" for step in output["timeline"])


@pytest.mark.parametrize(
    "public_url",
    ("", "not-a-url", "http://simulator.example.test", "ftp://simulator.example.test"),
)
def test_pstn_serve_rejects_missing_or_non_https_public_url(
    public_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", public_url)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)

    with pytest.raises(SystemExit, match="absolute HTTPS URL"):
        validate_serve_configuration("0.0.0.0")


def test_pstn_serve_accepts_https_with_signature_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://simulator.example.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)

    validate_serve_configuration("0.0.0.0")


def test_unsigned_webhooks_are_limited_to_loopback_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8978")
    monkeypatch.setenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "true")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    validate_serve_configuration("127.0.0.1")
    with pytest.raises(SystemExit, match="absolute HTTPS URL"):
        validate_serve_configuration("0.0.0.0")


def test_public_amd_mode_requires_acknowledgement_and_release_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://simulator.example.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
    monkeypatch.delenv("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO", raising=False)

    with pytest.raises(SystemExit, match="PSTN_ACKNOWLEDGE_PUBLIC_AUDIO"):
        validate_serve_configuration("0.0.0.0", include_amd=True)

    monkeypatch.setenv("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO", "true")
    with pytest.raises(SystemExit, match="not approved for public exposure"):
        validate_serve_configuration("0.0.0.0", include_amd=True)

    # IVR-only exposes no /assets route and therefore needs no audio approval.
    validate_serve_configuration("0.0.0.0", include_amd=False)


def test_console_script_points_to_unified_cli() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]

    assert scripts["telephony-voice-sim"] == "telephony_voice_simulator.cli.main:main"
    assert set(scripts) == {"telephony-voice-sim"}


@pytest.mark.parametrize("module", [
    "telephony_voice_simulator.telephony.twilio.webhooks",
    "telephony_voice_simulator.telephony.registry",
])
def test_carrier_entrypoints_import_in_a_fresh_process(module):
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; from telephony_voice_simulator.service import SimulatorService"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture()
def cli_db(tmp_path: Path, monkeypatch):
    from telephony_voice_simulator.cli import common

    monkeypatch.setenv("SIMULATOR_DB_PATH", str(tmp_path / "cli.db"))
    common.service.cache_clear()
    yield
    common.service.cache_clear()


def _json(capsys):
    return json.loads(capsys.readouterr().out)


def test_cli_manages_lines_end_to_end(cli_db, capsys) -> None:
    assert main(["providers", "add", "mock", "Local"]) == 0
    connection = _json(capsys)
    assert main(["lines", "add", connection["id"], "Desk", "1001", "--kind", "extension",
                 "--scenario", "ivr_connect_extension"]) == 0
    line = _json(capsys)

    assert main(["lines", "update", line["id"], "--name", "Front desk", "--enabled", "off"]) == 0
    updated = _json(capsys)
    assert updated["name"] == "Front desk"
    assert updated["enabled"] is False

    assert main(["lines", "delete", line["id"]]) == 0
    assert _json(capsys) == {"deleted": line["id"]}
    assert main(["lines", "list"]) == 0
    assert _json(capsys) == []


def test_cli_reports_service_errors_without_a_traceback(cli_db, capsys) -> None:
    assert main(["runs", "show", "run_missing"]) == 1
    assert "Unknown run: run_missing" in capsys.readouterr().err


def test_cli_pbx_suite_exit_code_follows_the_cases(capsys) -> None:
    code = main(["pbx", "suite"])
    report = _json(capsys)
    assert report["total"] == len(report["cases"]) > 0
    assert code == (0 if report["passed"] == report["total"] else 1)


def test_cli_pbx_capacity_compares_every_site_by_default(capsys) -> None:
    assert main(["pbx", "capacity"]) == 0
    assert len(_json(capsys)) >= 3
