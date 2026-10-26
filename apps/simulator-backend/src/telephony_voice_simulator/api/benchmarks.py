"""AMD benchmark: suites, plans, runs (which place real calls) and finished reports."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..service import ValidationError, benchmarks
from .common import (
    bool_field,
    config_file,
    json_object,
    json_response,
    optional_int_field,
    optional_string_field,
    start,
    string_list_field,
)


def _plan_options(body: dict[str, object]) -> dict[str, Any]:
    suite = optional_string_field(body, "suite")
    pace = body.get("pace")
    if pace is not None and (isinstance(pace, bool) or not isinstance(pace, (int, float))):
        raise ValidationError("pace must be a number of seconds")
    return {
        "suite": str(config_file(suite)) if suite else None,
        "scenarios": string_list_field(body, "scenarios") or None,
        "repeat": optional_int_field(body, "repeat"),
        "numbers": string_list_field(body, "numbers") or None,
        "concurrency": optional_int_field(body, "concurrency"),
        "pace": float(pace) if pace is not None else None,
    }


async def list_suites(_request: web.Request) -> web.Response:
    return json_response(benchmarks.list_suites())


async def plan(request: web.Request) -> web.Response:
    return json_response(benchmarks.plan(**_plan_options(await json_object(request))))


async def run(request: web.Request) -> web.Response:
    body = await json_object(request)
    options = _plan_options(body)
    audio = bool_field(body, "audio", True)
    # Validate the plan before queueing, so a bad request fails here and not in the log.
    benchmarks.plan(**options)
    return start(
        request,
        "benchmark",
        f"Benchmark: {body.get('suite') or ', '.join(options['scenarios'] or [])}",
        {**options, "audio": audio},
        lambda log: benchmarks.run(**options, audio=audio, log=log),
    )


async def list_reports(_request: web.Request) -> web.Response:
    return json_response(benchmarks.list_reports())


async def get_report(request: web.Request) -> web.Response:
    report_id = request.match_info["report_id"]
    markdown = request.query.get("format") == "markdown"
    report = benchmarks.get_report(_report(report_id), markdown=markdown)
    return json_response({**report, "report_id": report_id} if markdown else report)


def _report(report_id: str) -> str:
    # The service also takes paths for the CLI; browser input resolves by id alone.
    return str(benchmarks.report_path(report_id))


async def compare(request: web.Request) -> web.Response:
    body = await json_object(request)
    reports = []
    for item in string_list_field(body, "reports"):
        label, separator, report = item.rpartition("=")
        reports.append(f"{label}={_report(report)}" if separator else _report(item))
    return json_response(benchmarks.compare(reports=reports))


async def rescore(request: web.Request) -> web.Response:
    return json_response(benchmarks.rescore(report=_report(request.match_info["report_id"])))


async def preflight(request: web.Request) -> web.Response:
    return json_response(
        benchmarks.preflight(report=_report(request.match_info["report_id"]))
    )


def register(app: web.Application) -> None:
    app.router.add_get("/api/benchmarks/suites", list_suites)
    app.router.add_post("/api/benchmarks/plan", plan)
    app.router.add_post("/api/benchmarks/runs", run)
    app.router.add_get("/api/benchmarks/reports", list_reports)
    app.router.add_post("/api/benchmarks/compare", compare)
    app.router.add_get("/api/benchmarks/reports/{report_id}", get_report)
    app.router.add_post("/api/benchmarks/reports/{report_id}/rescore", rescore)
    app.router.add_get("/api/benchmarks/reports/{report_id}/preflight", preflight)
