"""Hosted grading must not pass media expectations without downloaded message audio."""

import httpx

from telephony_voice_simulator.pstn import grade_hosted


def test_hosted_grade_without_message_audio_is_indeterminate(tmp_path, monkeypatch):
    monkeypatch.setattr(grade_hosted, 'PSTN_RESULTS_DIR', tmp_path)
    monkeypatch.setattr(grade_hosted, '_load_scenario', lambda _name: {
        'name': 'quiet', 'machine': {'main': [{'hold': 5}]}, 'expect': {'agent_spoke': False},
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'sync.twilio.com':
            return httpx.Response(200, json={'data': {'scenario': 'quiet', 'digits': []}})
        return httpx.Response(200, json={'recordings': []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    call = grade_hosted.grade_call_detail(client, 'AC1', 'CA1')
    check = next(c for c in call['analysis']['checks'] if c['check'] == 'agent_spoke')
    assert check['passed'] is None
    assert call['analysis']['passed'] is None
