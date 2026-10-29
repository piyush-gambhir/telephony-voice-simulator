"""Regression suites from the backend config directory; runs place real calls."""

from __future__ import annotations

from aiohttp import web

from ..evaluation import regression
from ..paths import RESULTS_DIR
from ..service import NotFoundError
from .common import (
    config_file,
    json_object,
    json_response,
    optional_string_field,
    service_of,
    start,
    string_field,
    string_list_field,
)

REPORTS = RESULTS_DIR / "regression"


def _report(name: str):
    path = REPORTS / f"{name}.json"
    if "/" in name or not path.is_file():
        raise NotFoundError(f"Unknown regression report: {name}")
    return path


async def plan(request: web.Request) -> web.Response:
    body = await json_object(request)
    suite = regression.load_suite(config_file(string_field(body, "suite")))
    return json_response([c["name"] for c in regression.select(
        suite, string_list_field(body, "cases") or None)])


async def run(request: web.Request) -> web.Response:
    body = await json_object(request)
    name = string_field(body, "suite")
    suite = config_file(name)
    cases = string_list_field(body, "cases") or None
    baseline = optional_string_field(body, "baseline")
    baseline_path = _report(baseline) if baseline else None
    regression.select(regression.load_suite(suite), cases)
    store = service_of(request).store
    return start(
        request, "regression", f"Regression: {name}",
        {"suite": name, "cases": cases, "baseline": baseline},
        lambda log: regression.run(suite_path=suite, cases=cases, store=store,
                                   baseline=baseline_path, out_dir=REPORTS, log=log),
    )


async def get_report(request: web.Request) -> web.Response:
    import json

    report = json.loads(_report(request.match_info["report_id"]).read_text())
    return json_response({**report, "markdown": regression.summary_table(report)})


def register(app: web.Application) -> None:
    app.router.add_post("/api/regression/plan", plan)
    app.router.add_post("/api/regression/runs", run)
    app.router.add_get("/api/regression/reports/{report_id}", get_report)
