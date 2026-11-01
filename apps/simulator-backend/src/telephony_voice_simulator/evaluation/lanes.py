"""Parallel lanes for the regression runner.

A lane is one call in flight. Each case claims the lines it touches for the length of its
call: a persona line from the suite's pool (``persona_lines``), its transfer target's line,
and an AMD line (a case's ``line`` may list several to pick from). A line is reused only
``pace_seconds`` after it was released, because a carrier or agent may still count the
previous call as active and answer busy. Two cases therefore never share a target line or a
persona line at the same time; with ``exclusive_agent_numbers`` they never call the same
agent number at once either.

``before``/``after`` hooks change state the whole account shares (hours, transfer type), so
cases are grouped by their hook pair. Groups run one after another, each hook runs once
around its group, and only the cases inside a group overlap.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

Row = dict[str, Any]
RunOne = Callable[[dict[str, Any], dict[str, Any]], Row]
Hook = Callable[[str], str | None]


def _choices(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def groups(cases: list[dict[str, Any]]) -> list[tuple[str, str, list[int]]]:
    """Case indexes grouped by (before, after), in order of first appearance."""
    order: dict[tuple[str, str], list[int]] = {}
    for index, case in enumerate(cases):
        order.setdefault((case.get("before") or "", case.get("after") or ""), []).append(index)
    return [(before, after, indexes) for (before, after), indexes in order.items()]


class Lines:
    """Which lines are busy, and when each released line may be used again."""

    def __init__(self, pace: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.pace, self.clock = pace, clock
        self.busy: set[str] = set()
        self.ready_at: dict[str, float] = {}

    def free(self, line: str) -> bool:
        return line not in self.busy and self.ready_at.get(line, 0.0) <= self.clock()

    def claim(self, case: dict[str, Any], suite: dict[str, Any]) -> dict[str, str] | None:
        """The lines this case would hold now, or None when one it needs is taken."""
        held: dict[str, str] = {}
        fixed = []
        if case.get("target"):
            fixed.append(("target", str(case["target"]["line"])))
        if suite.get("exclusive_agent_numbers") and case.get("agent_number"):
            fixed.append(("agent", str(case["agent_number"])))
        for role, line in fixed:
            if not self.free(line):
                return None
            held[role] = line
        if case["kind"] == "amd":
            pool = _choices(case.get("line"))
        else:
            pool = _choices(suite.get("persona_lines")) or _choices(suite.get("persona_line"))
        chosen = next((line for line in pool if line not in held.values() and self.free(line)), None)
        if chosen is None:
            return None
        held["lane"] = chosen
        self.busy.update(held.values())
        return held

    def release(self, held: dict[str, str]) -> None:
        now = self.clock()
        for line in held.values():
            self.busy.discard(line)
            self.ready_at[line] = now + self.pace

    def next_ready(self) -> float:
        waiting = [t for line, t in self.ready_at.items() if line not in self.busy]
        return max(0.0, min(waiting, default=0.0) - self.clock())


def _bound(case: dict[str, Any], suite: dict[str, Any], held: dict[str, str]
           ) -> tuple[dict[str, Any], dict[str, Any]]:
    """The case and suite as one lane sees them: its own persona or AMD line."""
    if case["kind"] == "amd":
        return {**case, "line": held["lane"]}, suite
    return case, {**suite, "persona_line": held["lane"]}


def run(cases: list[dict[str, Any]], suite: dict[str, Any], *, lanes: int, pace: float,
        run_one: RunOne, hook: Hook, failed: Callable[[dict[str, Any], str], Row],
        on_row: Callable[[int, Row], None], log: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic, poll: float = 5.0) -> list[Row]:
    """Run ``cases`` over ``lanes`` concurrent calls; rows come back in case order."""
    rows: list[Row | None] = [None] * len(cases)
    lines = Lines(pace, clock)
    for before, after, indexes in groups(cases):
        if before and (error := hook(before)):
            for index in indexes:
                rows[index] = failed(cases[index], f"before command failed: {error}")
                on_row(index, rows[index])
            if after:
                hook(after)
            continue
        pending = list(indexes)
        running: dict[Future, tuple[int, dict[str, str]]] = {}
        with ThreadPoolExecutor(max_workers=lanes) as pool:
            while pending or running:
                started = True
                while started and len(running) < lanes:
                    started = False
                    for index in pending:
                        held = lines.claim(cases[index], suite)
                        if held is None:
                            continue
                        case, view = _bound(cases[index], suite, held)
                        log(f"[lane {held['lane']}] {case['name']} ({case['kind']})")
                        running[pool.submit(run_one, case, view)] = (index, held)
                        pending.remove(index)
                        started = True
                        break
                if not running:
                    if hopeless := [i for i in pending if not _needed(cases[i], suite)]:
                        # A case with no line to call from can never start.
                        for index in hopeless:
                            rows[index] = failed(cases[index], "no line available for this case")
                            on_row(index, rows[index])
                            pending.remove(index)
                        continue
                    # Every needed line is pacing: wait for the first one to come back.
                    time.sleep(min(poll, lines.next_ready()) or poll / 5)
                    continue
                done, _ = wait(list(running), timeout=poll, return_when=FIRST_COMPLETED)
                for future in done:
                    index, held = running.pop(future)
                    lines.release(held)
                    try:
                        rows[index] = {**future.result(), "lane": held["lane"]}
                    except Exception as exc:  # one broken case must not lose the others
                        rows[index] = failed(cases[index], f"{type(exc).__name__}: {exc}")
                    on_row(index, rows[index])
        if after and (error := hook(after)):
            log(f"after command failed: {error}")
    return [row for row in rows if row is not None]


def _needed(case: dict[str, Any], suite: dict[str, Any]) -> list[str]:
    if case["kind"] == "amd":
        return _choices(case.get("line"))
    return _choices(suite.get("persona_lines")) or _choices(suite.get("persona_line"))
