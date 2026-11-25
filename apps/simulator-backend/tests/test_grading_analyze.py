"""Required assertions must remain visible when PSTN evidence cannot prove them."""

import pytest
import soundfile as sf

from telephony_voice_simulator.domains.amd.machine import SAMPLE_RATE, synth_tone
from telephony_voice_simulator.grading.analyze import analyze_call


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

    monkeypatch.setattr('telephony_voice_simulator.grading.analyze.transcribe_recording', unexpected)
    analysis = analyze_call({'analysis_recording_files': recordings}, {
        'expect': {'message_content': {'expected': 'Hello from the simulator'}},
    }, None)
    assert analysis['passed'] is None


def _spoken(tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / name
        sf.write(path, synth_tone(300, 1), SAMPLE_RATE)
        paths.append(str(path))
    return paths


def test_transcript_checks_read_every_recording_in_order(tmp_path, monkeypatch):
    heard = {'a.wav': 'Hi, I have Taylor on the line.', 'b.wav': 'My word is lighthouse.'}
    monkeypatch.setattr('telephony_voice_simulator.grading.analyze.transcribe_recording',
                        lambda path: heard[path.name])
    call = {'analysis_recording_files': _spoken(tmp_path, 'a.wav', 'b.wav')}
    analysis = analyze_call(call, {'expect': {
        'transcript_contains': {'phrases': ['on the line', 'lighthouse', 'pineapple'], 'min_hits': 2},
        'transcript_excludes': {'phrases': ['available to take the transfer']},
    }}, None)
    assert analysis['passed'] is True
    assert call['recordings_transcript'] == 'Hi, I have Taylor on the line. My word is lighthouse.'


def test_transcript_excludes_fails_on_a_forbidden_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr('telephony_voice_simulator.grading.analyze.transcribe_recording',
                        lambda _path: 'Hi, I have a customer on the line.')
    analysis = analyze_call({'analysis_recording_files': _spoken(tmp_path, 'a.wav')}, {
        'expect': {'transcript_excludes': {'phrases': ['I have a customer on the line']}},
    }, None)
    assert analysis['passed'] is False


def test_untranscribed_speech_leaves_transcript_checks_indeterminate(tmp_path, monkeypatch):
    monkeypatch.setattr('telephony_voice_simulator.grading.analyze.transcribe_recording',
                        lambda _path: None)
    analysis = analyze_call({'analysis_recording_files': _spoken(tmp_path, 'a.wav')}, {
        'expect': {'transcript_excludes': {'phrases': ['anything']}},
    }, None)
    assert analysis['passed'] is None


@pytest.mark.parametrize('status,passed', [('busy', True), ('no-answer', True), ('completed', False)])
def test_reject_scenario_passes_only_when_the_call_never_answered(tmp_path, status, passed):
    from telephony_voice_simulator.telephony.twilio.compile import compile_scenario

    compiled = compile_scenario({'name': 'busy', 'machine': {'main': [{'reject': 'busy'}]}, 'expect': {}},
                                assets_dir=tmp_path, out_dir=tmp_path / 'c')
    analysis = analyze_call({'status': status}, {'expect': {}}, compiled)
    assert analysis['passed'] is passed


def _speech_after(tmp_path, name, silence_s):
    import numpy as np

    path = tmp_path / name
    quiet = np.zeros(int(silence_s * SAMPLE_RATE), dtype=np.int16)
    sf.write(path, np.concatenate([quiet, synth_tone(300, 1)]), SAMPLE_RATE)
    return str(path)


@pytest.mark.parametrize('audio_duration,passed', [(3, True), (10, False)])
def test_agent_speech_after_is_graded_from_the_record_window(tmp_path, audio_duration, passed):
    analysis = analyze_call({
        'analysis_recording_files': [_speech_after(tmp_path, 'reply.wav', 0.5)],
        'message_recording_window': {'audio_duration': audio_duration,
                                     'marks': {'play_end:hello.wav': 2}},
    }, {'expect': {'agent_speech_after': {'mark': 'play_end:hello.wav', 'max': 4}}}, None)
    check = analysis['checks'][0]
    assert (check['check'], check['passed']) == ('agent_speech_after', passed)
    assert f'delay_after_mark={audio_duration - 2 + 0.5:.2f}s' in check['detail']


def test_compiled_human_and_no_beep_marks_resolve_on_the_phone_path(tmp_path):
    from telephony_voice_simulator.telephony.twilio.compile import compile_scenario

    sf.write(tmp_path / 'hello.wav', synth_tone(300, 0.5), SAMPLE_RATE)
    compiled = compile_scenario({
        'name': 'hi', 'machine': {'main': [{'wait': 0.8}, {'play': 'hello.wav'}, {'hold': 5}]},
        'expect': {},
    }, assets_dir=tmp_path, out_dir=tmp_path / 'c')
    recording = _speech_after(tmp_path, 'reply.wav', 0.4)
    analysis = analyze_call({'analysis_recording_files': [recording]}, {'expect': {
        'agent_speech_after': {'mark': 'play_end:hello.wav', 'max': 4},
        'message_start_after': {'mark': 'play_end:hello.wav', 'min': 0.1, 'max': 3},
    }}, compiled)
    assert [c['passed'] for c in analysis['checks']] == [True, True]


@pytest.mark.parametrize('silence_s,passed', [(0, None), (0.05, None), (0.5, True)])
def test_message_already_under_way_at_record_start_is_unproven(tmp_path, silence_s, passed):
    analysis = analyze_call({
        'analysis_recording_files': [_speech_after(tmp_path, 'message.wav', silence_s)],
        'message_recording_window': {'audio_duration': 4, 'marks': {'tone_end': 4}},
    }, {'expect': {'message_start_after': {'mark': 'tone_end', 'min': 0.1, 'max': 3}}}, None)
    check = analysis['checks'][0]
    assert check['passed'] is passed
    assert ('already_speaking_at_record_start' in check['detail']) is (passed is None)


@pytest.mark.parametrize('silence_s,passed', [(0, None), (0.5, True)])
def test_agent_talking_over_the_last_line_is_unproven(tmp_path, silence_s, passed):
    analysis = analyze_call({
        'analysis_recording_files': [_speech_after(tmp_path, 'reply.wav', silence_s)],
        'message_recording_window': {'audio_duration': 2, 'marks': {'play_end:hello.wav': 2}},
    }, {'expect': {'agent_speech_after': {'mark': 'play_end:hello.wav', 'max': 4}}}, None)
    assert analysis['checks'][0]['passed'] is passed


def test_silent_first_window_does_not_disprove_a_later_mark(tmp_path):
    import numpy as np

    path = tmp_path / 'quiet.wav'
    sf.write(path, np.zeros(SAMPLE_RATE, dtype=np.int16), SAMPLE_RATE)
    analysis = analyze_call({
        'analysis_recording_files': [str(path)],
        'message_recording_window': {'audio_duration': 1, 'marks': {'play_end:first.wav': 1}},
    }, {'expect': {'agent_speech_after': {'mark': 'play_end:later.wav', 'max': 4}}}, None)
    assert analysis['checks'][0]['passed'] is None
