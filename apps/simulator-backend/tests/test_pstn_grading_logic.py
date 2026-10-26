"""Required assertions must remain visible when PSTN evidence cannot prove them."""

import pytest
import soundfile as sf

from telephony_voice_simulator.machine import SAMPLE_RATE, synth_tone
from telephony_voice_simulator.pstn.analyze import analyze_call


@pytest.mark.parametrize('check,options', [
    ('first_agent_speech_after', {'mark': 'audio_path_ready', 'max': 5}),
    ('agent_speech_after', {'mark': 'play_end:hello.wav', 'max': 4}),
    ('max_overlap_with_playback', {'asset': 'greeting.wav', 'max_s': 0.1}),
    ('max_overlap_between_marks', {'start': 'tone_start', 'end': 'tone_end', 'max_s': 0.1}),
])
def test_unobserved_required_timing_cannot_disappear_into_a_pass(tmp_path, check, options):
    recording = tmp_path / 'speech.wav'
    sf.write(recording, synth_tone(300, 1), SAMPLE_RATE)
    analysis = analyze_call({'analysis_recording_files': [str(recording)]}, {
        'expect': {'agent_spoke': True, check: options},
    }, None)
    assert analysis['passed'] is None
    result = next(row for row in analysis['checks'] if row['check'] == check)
    assert result['passed'] is None
    assert result['detail']


def test_ambiguous_message_order_cannot_grade_arbitrary_file(tmp_path):
    recordings = []
    for name in ['first.wav', 'second.wav']:
        path = tmp_path / name
        sf.write(path, synth_tone(300, 1), SAMPLE_RATE)
        recordings.append(str(path))
    analysis = analyze_call({'analysis_recording_files': recordings}, {
        'expect': {'agent_spoke': True, 'message_start_after': {'mark': 'tone_end', 'max': 3}},
    }, None)
    assert analysis['passed'] is None
    assert next(row for row in analysis['checks'] if row['check'] == 'agent_spoke')['passed'] is True


def test_message_timing_includes_audio_after_requested_mark(tmp_path):
    recording = tmp_path / 'speech.wav'
    sf.write(recording, synth_tone(300, 1), SAMPLE_RATE)
    analysis = analyze_call({
        'analysis_recording_files': [str(recording)],
        'message_recording_window': {'audio_duration': 4, 'marks': {'tone_end': 1}},
    }, {'expect': {'message_start_after': {'mark': 'tone_end', 'max': 1}}}, None)
    assert analysis['passed'] is False
    assert 'delay_after_mark=3.00s' in analysis['checks'][0]['detail']
    assert 'record_offset=3.00s' in analysis['checks'][0]['detail']


def test_ambiguous_content_does_not_transcribe_arbitrary_message(tmp_path, monkeypatch):
    recordings = []
    for name in ['a.wav', 'b.wav']:
        path = tmp_path / name
        sf.write(path, synth_tone(300, 1), SAMPLE_RATE)
        recordings.append(str(path))

    def unexpected(_path):
        pytest.fail('unordered media must not be sent for transcription')

    monkeypatch.setattr('telephony_voice_simulator.pstn.analyze.transcribe_recording', unexpected)
    analysis = analyze_call({'analysis_recording_files': recordings}, {
        'expect': {'message_content': {'expected': 'Hello from the simulator'}},
    }, None)
    assert analysis['passed'] is None
