import threading
import time

from telephony_voice_simulator.evaluation import lanes


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


SUITE = {"persona_lines": ["+15550100001", "+15550100002"]}


def _case(name, kind="inbound", target=None, **extra):
    return {"name": name, "kind": kind, **({"target": {"line": target}} if target else {}), **extra}


def test_claims_a_free_persona_line_and_the_target_line():
    lines = lanes.Lines(pace=60, clock=Clock())
    first = lines.claim(_case("a", target="+15550100009"), SUITE)
    assert first == {"target": "+15550100009", "lane": "+15550100001"}
    # The target line is held, so a second case for it waits even with a lane free.
    assert lines.claim(_case("b", target="+15550100009"), SUITE) is None
    assert lines.claim(_case("c"), SUITE) == {"lane": "+15550100002"}
    assert lines.claim(_case("d"), SUITE) is None


def test_a_released_line_is_paced_before_reuse():
    clock = Clock()
    lines = lanes.Lines(pace=60, clock=clock)
    held = lines.claim(_case("a"), {"persona_lines": ["+15550100001"]})
    lines.release(held)
    assert lines.claim(_case("b"), {"persona_lines": ["+15550100001"]}) is None
    assert lines.next_ready() == 60
    clock.now = 60
    assert lines.claim(_case("b"), {"persona_lines": ["+15550100001"]}) == {"lane": "+15550100001"}


def test_amd_cases_pick_from_their_own_lines_and_agent_numbers_can_be_exclusive():
    lines = lanes.Lines(pace=0, clock=Clock())
    amd = _case("vm", kind="amd", line=["+15550100007", "+15550100008"])
    assert lines.claim(amd, SUITE) == {"lane": "+15550100007"}
    assert lines.claim(amd, SUITE) == {"lane": "+15550100008"}
    suite = {**SUITE, "exclusive_agent_numbers": True}
    assert lines.claim(_case("x", agent_number="+15550100005"), suite)["agent"] == "+15550100005"
    assert lines.claim(_case("y", agent_number="+15550100005"), suite) is None


def test_groups_follow_hook_pairs_in_first_appearance_order():
    cases = [_case("a"), _case("b", before="x"), _case("c"), _case("d", before="x")]
    assert lanes.groups(cases) == [("", "", [0, 2]), ("x", "", [1, 3])]


def test_run_overlaps_cases_without_sharing_a_line_and_keeps_case_order():
    cases = [_case(f"c{i}", target="+15550100009" if i < 2 else None) for i in range(5)]
    suite = {"persona_lines": ["+15550100001", "+15550100002", "+15550100003"]}
    active, peak, seen, lock = set(), [0], [], threading.Lock()

    def run_one(case, view):
        lane = view["persona_line"]
        with lock:
            # Never two calls on one persona line, and only one on the shared target line.
            assert lane not in active and not ({"T"} & active and case.get("target"))
            active.add(lane)
            if case.get("target"):
                active.add("T")
            peak[0] = max(peak[0], len(active - {"T"}))
        time.sleep(0.05)
        with lock:
            active.discard(lane)
            active.discard("T")
        return {"case": case["name"], "passed": True, "failed_checks": []}

    hooks = []
    rows = lanes.run(cases, suite, lanes=3, pace=0, run_one=run_one,
                     hook=lambda c: hooks.append(c), failed=lambda c, e: {"case": c["name"], "error": e},
                     on_row=lambda i, r: seen.append(i), log=lambda _m: None, poll=0.01)
    assert [r["case"] for r in rows] == [f"c{i}" for i in range(5)]
    assert all(r["lane"].startswith("+1555") for r in rows)
    assert peak[0] >= 2 and sorted(seen) == list(range(5)) and hooks == []


def test_a_failing_group_hook_fails_its_cases_without_calls_and_still_runs_after():
    cases = [_case("a", before="setup", after="reset"), _case("b")]
    hooks, calls = [], []

    def hook(command):
        hooks.append(command)
        return "boom" if command == "setup" else None

    rows = lanes.run(cases, SUITE, lanes=2, pace=0,
                     run_one=lambda c, v: calls.append(c["name"]) or {"case": c["name"]},
                     hook=hook, failed=lambda c, e: {"case": c["name"], "error": e},
                     on_row=lambda i, r: None, log=lambda _m: None, poll=0.01)
    assert rows[0]["error"].startswith("before command failed") and calls == ["b"]
    assert hooks == ["setup", "reset"]
