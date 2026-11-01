from types import SimpleNamespace

from telephony_voice_simulator.evaluation import preflight


def test_suite_lines_maps_each_line_to_its_webhook():
    suite = {"persona_lines": ["+15550100001", "+15550100002"]}
    cases = [{"kind": "inbound", "target": {"line": "+15550100003", "persona": "staff-warm-accept"}},
             {"kind": "amd", "line": ["+15550100007", "+15550100008"]}]
    assert preflight.suite_lines(suite, cases) == {
        "+15550100001": "/twilio/customer", "+15550100002": "/twilio/customer",
        "+15550100003": "/twilio/voice", "+15550100007": "/twilio/voice",
        "+15550100008": "/twilio/voice"}


def test_signed_probe_tells_a_stale_server_from_a_good_one(monkeypatch):
    sent = {}

    def post(url, data, headers, timeout):
        sent.update(url=url, signature=headers["X-Twilio-Signature"], data=data)
        return SimpleNamespace(status_code=status[0])

    status = [200]
    monkeypatch.setattr(preflight.httpx, "post", post)
    assert preflight.signed_probe("https://sim.example", "token", "AC1") == (True, "HTTP 200")
    assert sent["url"] == "https://sim.example/twilio/customer" and sent["signature"]
    assert sent["data"]["To"] == preflight.PROBE_LINE
    status[0] = 403
    ok, detail = preflight.signed_probe("https://sim.example", "token", "AC1")
    assert ok is False and "another public URL" in detail


def test_leftovers_are_reported_or_cleared():
    queue = {"+15550100003": [{"id": "p1"}]}
    runs = {"+15550100003": [{"run_id": "r1"}]}
    cancelled = []
    store = SimpleNamespace(
        list_customer_persona_queue=lambda line: queue.get(line, []),
        list_provider_queue=lambda provider, line: runs.get(line, []),
        cancel_customer_persona=lambda i: cancelled.append(i),
        cancel_queued_run=lambda i: cancelled.append(i))
    lines = {"+15550100003": "/twilio/voice", "+15550100004": "/twilio/voice"}
    assert preflight._leftovers(store, lines, clear=False) == {"+15550100003": 2}
    assert preflight._leftovers(store, lines, clear=True) == {} and cancelled == ["p1", "r1"]
