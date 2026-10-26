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


def test_hosted_grade_aligns_message_timing_to_default_mark(tmp_path, monkeypatch):
    import io

    import soundfile as sf

    from telephony_voice_simulator.machine import SAMPLE_RATE, synth_tone

    monkeypatch.setattr(grade_hosted, 'PSTN_RESULTS_DIR', tmp_path / 'results')
    monkeypatch.setattr(grade_hosted, 'ASSETS_DIR', tmp_path)
    monkeypatch.setattr(grade_hosted, '_load_scenario', lambda _name: {
        'name': 'beep', 'machine': {'main': [{'tone': {'duration': 1}}, {'hold': 5}]},
        'expect': {'message_start_after': {'max': 3}},
    })
    wav = io.BytesIO()
    sf.write(wav, synth_tone(300, 1), SAMPLE_RATE, format='WAV')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == 'sync.twilio.com':
            return httpx.Response(200, json={'data': {'scenario': 'beep', 'digits': []}})
        if request.url.path.endswith('.wav'):
            return httpx.Response(200, content=wav.getvalue())
        return httpx.Response(200, json={'recordings': [{
            'sid': 'RE1', 'source': 'RecordVerb', 'date_created': '1',
            'uri': '/2010-04-01/Accounts/AC1/Recordings/RE1.json',
        }]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    call = grade_hosted.grade_call_detail(client, 'AC1', 'CA1')
    check = next(c for c in call['analysis']['checks'] if c['check'] == 'message_start_after')
    assert check['passed'] is True
