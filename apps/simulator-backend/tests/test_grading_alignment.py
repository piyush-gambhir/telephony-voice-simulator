"""Timing graded from synthetic dual-channel call recordings: agent on 0, callee playback on 1."""

import numpy as np
import pytest
import soundfile as sf

from telephony_voice_simulator.domains.amd.machine import SAMPLE_RATE
from telephony_voice_simulator.grading.alignment import align
from telephony_voice_simulator.grading.analyze import analyze_call
from telephony_voice_simulator.telephony.twilio.compile import PSTN_RATE, compile_scenario

LEAD_S = 0.4
VOICEMAIL_EXPECT = {
    'message_start_after': {'mark': 'tone_end', 'min': 0.1, 'max': 3.0},
    'max_overlap_with_playback': {'asset': 'greeting.wav', 'max_s': 0.5},
    'max_overlap_between_marks': {'start': 'tone_start', 'end': 'tone_end', 'max_s': 0.05},
}


def _noise(seconds, seed):
    rng = np.random.default_rng(seed)
    return np.clip(rng.standard_normal(int(seconds * SAMPLE_RATE)) * 4000, -32767, 32767).astype(np.int16)


def _mulaw(samples):
    scaled = samples / 32768.0
    encoded = np.round(np.sign(scaled) * np.log1p(255 * np.abs(scaled)) / np.log1p(255) * 127) / 127
    return np.sign(encoded) * np.expm1(np.abs(encoded) * np.log1p(255)) / 255 * 32768


def _record_call(path, compiled_dir, plays, speech, total_s, channels=2, noise=0.0, swap=False,
                 corrupt=None):
    """Write a call recording: compiled audio played at the given times, agent tone bursts.

    A negative play time means the recording began that long after the playback did, and
    ``corrupt`` replaces one stretch of the playback channel with unrelated noise.
    """
    playback = np.zeros(int(total_s * PSTN_RATE))
    for audio_file, at, played_s, *skip in plays:
        audio, _ = sf.read(compiled_dir / audio_file, dtype='int16')
        audio = audio[: int(played_s * PSTN_RATE)] if played_s else audio
        audio = audio[skip[0]:] if skip else audio
        start = round(at * PSTN_RATE)
        audio, start = audio[max(0, -start):], max(0, start)
        count = min(len(audio), len(playback) - start)
        playback[start:start + count] += audio[:count]
    playback = 0.8 * _mulaw(playback) + np.random.default_rng(7).standard_normal(len(playback)) * noise
    if corrupt:
        lo, hi = (int(t * PSTN_RATE) for t in corrupt)
        playback[lo:hi] = np.random.default_rng(12).standard_normal(hi - lo) * 3000
    agent = np.zeros_like(playback)
    for begin, end in speech:
        index = np.arange(max(0, int(begin * PSTN_RATE)), int(end * PSTN_RATE))
        agent[index] = 6000 * np.sin(2 * np.pi * 220 * index / PSTN_RATE)
    pair = [playback, agent] if swap else [agent, playback]
    stereo = np.stack(pair, axis=1)[:, :channels]
    sf.write(path, np.clip(stereo, -32767, 32767).astype(np.int16), PSTN_RATE, subtype='PCM_16')
    return path


@pytest.fixture
def voicemail(tmp_path):
    sf.write(tmp_path / 'greeting.wav', _noise(2.0, 0), SAMPLE_RATE)
    raw = {'name': 'vm', 'machine': {'main': [
        {'wait': 0.3}, {'play': 'greeting.wav'}, {'wait': 0.5},
        {'tone': {'freq': 1000, 'duration': 0.5}}, {'hold': 10},
    ]}, 'expect': VOICEMAIL_EXPECT}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    return raw, compiled, tmp_path


def _analyze(root, raw, compiled, speech, total_s=14.0, channels=2, lead=LEAD_S, swap=False):
    recording = _record_call(root / 'full-call.wav', root / 'compiled',
                             [(compiled.sequences['main'][0].audio_file, lead, None)],
                             speech, total_s, channels, swap=swap)
    call = {
        'full_call_recording_file': str(recording),
        'compiled_audio_dir': str(root / 'compiled'),
        'segments': [{'seq': 'main', 'index': 0, 'terminal': 'record', 'gather': False, 't': 0.0}],
    }
    analysis = analyze_call(call, raw, compiled)
    return analysis, {row['check']: row for row in analysis['checks']}


def test_on_time_message_passes_every_timing_check(voicemail):
    raw, compiled, root = voicemail
    analysis, rows = _analyze(root, raw, compiled, [(4.5, 8.5)])
    assert analysis['passed'] is True
    assert 'delay_after_mark=0.80s' in rows['message_start_after']['detail']
    assert 'source=full-call' in rows['message_start_after']['detail']
    assert analysis['full_call_alignment']['segments_located'] == 1


def test_message_started_during_the_greeting_fails(voicemail):
    raw, compiled, root = voicemail
    analysis, rows = _analyze(root, raw, compiled, [(1.7, 7.7)])
    assert analysis['passed'] is False
    assert rows['message_start_after']['passed'] is False
    assert 'delay_after_mark=-2.00s' in rows['message_start_after']['detail']
    assert 'overlap=1.00s' in rows['max_overlap_with_playback']['detail']
    assert rows['max_overlap_between_marks']['passed'] is False


def test_talking_through_the_beep_fails_even_after_a_pause(voicemail):
    raw, compiled, root = voicemail
    _, rows = _analyze(root, raw, compiled, [(3.0, 4.2), (4.8, 8.0)])
    assert rows['message_start_after']['passed'] is False
    assert 'talking_over_mark' in rows['message_start_after']['detail']
    assert rows['max_overlap_between_marks']['passed'] is False


def test_speech_over_the_greeting_fails_only_the_overlap_check(voicemail):
    raw, compiled, root = voicemail
    _, rows = _analyze(root, raw, compiled, [(1.0, 1.8), (4.2, 8.0)])
    assert rows['message_start_after']['passed'] is True
    assert rows['max_overlap_with_playback']['passed'] is False
    assert 'overlap=0.80s' in rows['max_overlap_with_playback']['detail']
    assert rows['max_overlap_between_marks']['passed'] is True


def test_mono_full_call_falls_back_to_record_window_rules(voicemail):
    raw, compiled, root = voicemail
    analysis, rows = _analyze(root, raw, compiled, [(4.5, 8.5)], channels=1)
    assert 'need 2' in analysis['full_call_alignment']['error']
    assert rows['max_overlap_with_playback']['passed'] is None
    assert 'source=full-call' not in rows['message_start_after']['detail']


@pytest.mark.parametrize('speech,passed', [([(2.0, 3.0)], True), ([(0.9, 3.4)], False)])
def test_agent_reply_after_a_human_hello(tmp_path, speech, passed):
    sf.write(tmp_path / 'hello.wav', _noise(1.0, 1), SAMPLE_RATE)
    raw = {'name': 'hi', 'machine': {'main': [{'play': 'hello.wav'}, {'hold': 10}]}, 'expect': {
        'agent_speech_after': {'mark': 'play_end:hello.wav', 'min': 0.0, 'max': 4.0},
    }}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    _, rows = _analyze(tmp_path, raw, compiled, speech, total_s=8.0)
    assert rows['agent_speech_after']['passed'] is passed
    assert ('talking_over_mark' in rows['agent_speech_after']['detail']) is (not passed)


def test_repeated_prompts_are_placed_in_order(voicemail):
    _, compiled, root = voicemail
    audio_file = compiled.sequences['main'][0].audio_file
    recording = _record_call(root / 'full-call.wav', root / 'compiled',
                             [(audio_file, at, None) for at in (0.2, 4.0, 8.0)], [], 12.0)
    entry = {'seq': 'main', 'index': 0, 'terminal': 'record', 'gather': True}
    alignment = align(recording, [entry] * 3, compiled, root / 'compiled')
    ends = [t for name, t in alignment.marks if name == 'tone_end']
    assert alignment.located == 3
    assert ends == pytest.approx([3.5, 7.3, 11.3], abs=0.002)


def test_prompt_cut_short_cannot_vouch_for_later_marks(voicemail):
    raw, compiled, root = voicemail
    audio_file = compiled.sequences['main'][0].audio_file
    recording = _record_call(root / 'full-call.wav', root / 'compiled',
                             [(audio_file, LEAD_S, 1.5)], [(5.0, 8.0)], 10.0)
    entry = {'seq': 'main', 'index': 0, 'terminal': 'record', 'gather': True}
    alignment = align(recording, [entry], compiled, root / 'compiled')
    marks = dict(alignment.marks)
    assert marks['play_start:greeting.wav'] == pytest.approx(LEAD_S + 0.3, abs=0.002)
    assert marks['play_end:greeting.wav'] is None
    assert marks['tone_end'] is None
    start, confirmed, possible, unplaced = alignment.playbacks['greeting.wav'][0]
    assert confirmed - start == pytest.approx(1.2, abs=0.1)
    assert (possible, unplaced) == (confirmed, 0.0)
    call = {'full_call_recording_file': str(recording), 'compiled_audio_dir': str(root / 'compiled'),
            'segments': [entry]}
    analysis = analyze_call(call, raw, compiled)
    assert 'not confirmed' in analysis['full_call_alignment']['unresolved']['message_start_after']


def test_a_mark_from_a_sequence_that_never_played_fails(tmp_path):
    sf.write(tmp_path / 'greeting.wav', _noise(1.5, 2), SAMPLE_RATE)
    raw = {'name': 'gate', 'machine': {
        'main': [{'play': 'greeting.wav'}, {'wait': 1.0}],
        'on_dtmf': {'digits': ['1'], 'switch': 'vm'},
        'vm': [{'tone': {'freq': 1000, 'duration': 0.5}}, {'hold': 10}],
    }, 'expect': {'message_start_after': {'mark': 'tone_end', 'min': 0.1, 'max': 3.0}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    _, rows = _analyze(tmp_path, raw, compiled, [(3.0, 5.0)], total_s=8.0)
    assert rows['message_start_after']['passed'] is False
    assert 'never played' in rows['message_start_after']['detail']


def test_playback_before_a_late_recording_cannot_pass_overlap_checks(voicemail):
    raw, compiled, root = voicemail
    raw = {**raw, 'expect': {**VOICEMAIL_EXPECT, 'first_agent_speech_after': {
        'mark': 'play_end:greeting.wav', 'min': 0.0, 'max': 4.0}}}
    _, rows = _analyze(root, raw, compiled, [(-0.6, 0.5), (2.3, 6.0)], lead=-1.5)
    overlap = rows['max_overlap_with_playback']
    assert overlap['passed'] is None
    assert 'unseen_before_recording=1.20s' in overlap['detail']
    assert rows['first_agent_speech_after']['passed'] is False
    assert rows['message_start_after']['passed'] is True


def test_channel_order_is_detected_from_the_playback(voicemail):
    raw, compiled, root = voicemail
    analysis, rows = _analyze(root, raw, compiled, [(4.5, 8.5)], swap=True)
    assert analysis['passed'] is True
    assert 'delay_after_mark=0.80s' in rows['message_start_after']['detail']


def test_keypress_in_a_silent_tail_still_finds_the_branch_it_switched_to(tmp_path):
    sf.write(tmp_path / 'prompt.wav', _noise(1.0, 4), SAMPLE_RATE)
    sf.write(tmp_path / 'hello.wav', _noise(1.0, 5), SAMPLE_RATE)
    raw = {'name': 'gate', 'machine': {
        'main': [{'play': 'prompt.wav'}, {'wait': 6.0}],
        'on_dtmf': {'digits': ['1'], 'switch': 'connected'},
        'connected': [{'wait': 1.0}, {'play': 'hello.wav'}, {'hold': 10}],
    }, 'expect': {'agent_speech_after': {'mark': 'play_end:hello.wav', 'min': 0.0, 'max': 4.0}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    recording = _record_call(tmp_path / 'full-call.wav', tmp_path / 'compiled', [
        (compiled.sequences['main'][0].audio_file, 0.3, 2.0),
        (compiled.sequences['connected'][0].audio_file, 2.5, None),
    ], [(5.0, 6.0)], 12.0)
    for hinted in (True, False):
        entries = [{'seq': 'main', 'index': 0, 'gather': True},
                   {'seq': 'connected', 'index': 0, 'gather': False}]
        if hinted:
            entries[0]['t'], entries[1]['t'] = 0.0, 2.2
        call = {'full_call_recording_file': str(recording),
                'compiled_audio_dir': str(tmp_path / 'compiled'), 'segments': entries}
        row = analyze_call(call, raw, compiled)['checks'][0]
        assert row['passed'] is True, (hinted, row)
        assert 'delay_after_mark=0.50s' in row['detail']


def test_noisy_repeated_prompts_stay_in_order_with_serve_times(voicemail):
    _, compiled, root = voicemail
    audio_file = compiled.sequences['main'][0].audio_file
    recording = _record_call(root / 'full-call.wav', root / 'compiled',
                             [(audio_file, at, None) for at in (0.5, 13.5, 26.5)], [], 31.0,
                             noise=30.0)
    entries = [{'seq': 'main', 'index': 0, 'gather': True, 't': t} for t in (0.0, 13.0, 26.0)]
    alignment = align(recording, entries, compiled, root / 'compiled')
    ends = [t for name, t in alignment.marks if name == 'tone_end']
    assert ends == pytest.approx([3.8, 16.8, 29.8], abs=0.002)


def test_a_segment_served_after_hangup_never_played(tmp_path):
    sf.write(tmp_path / 'greeting.wav', _noise(2.0, 6), SAMPLE_RATE)
    raw = {'name': 'late', 'machine': {'main': [
        {'play': 'greeting.wav'}, {'hold': 5}, {'tone': {'freq': 1000, 'duration': 0.5}}, {'hold': 10},
    ]}, 'expect': {'message_start_after': {'mark': 'tone_end', 'min': 0.1, 'max': 3.0}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    recording = _record_call(tmp_path / 'full-call.wav', tmp_path / 'compiled',
                             [(compiled.sequences['main'][0].audio_file, LEAD_S, None)],
                             [(3.0, 5.0)], 7.0)
    call = {'full_call_recording_file': str(recording), 'compiled_audio_dir': str(tmp_path / 'compiled'),
            'segments': [{'seq': 'main', 'index': 0, 't': 0.0}, {'seq': 'main', 'index': 1, 't': 7.2}]}
    row = analyze_call(call, raw, compiled)['checks'][0]
    assert row['passed'] is False
    assert 'never played' in row['detail']


@pytest.mark.parametrize('replay_heard_s', [1.5, 2.0, 2.8])
def test_a_cut_prompt_replayed_at_once_keeps_its_own_overlap(tmp_path, replay_heard_s):
    sf.write(tmp_path / 'prompt.wav', _noise(3.0, 8), SAMPLE_RATE)
    raw = {'name': 'gate', 'machine': {
        'main': [{'wait': 0.8}, {'play': 'prompt.wav'}, {'wait': 6.0}], 'on_dtmf': 'ignore',
    }, 'expect': {'max_overlap_with_playback': {'asset': 'prompt.wav', 'max_s': 0.5}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    audio_file = compiled.sequences['main'][0].audio_file
    replay = 0.5 + 2.5 + 0.3
    recording = _record_call(tmp_path / 'full-call.wav', tmp_path / 'compiled',
                             [(audio_file, 0.5, 2.5), (audio_file, replay, None)],
                             [(1.5, 3.0)], replay + 0.8 + replay_heard_s)
    call = {'full_call_recording_file': str(recording), 'compiled_audio_dir': str(tmp_path / 'compiled'),
            'segments': [{'seq': 'main', 'index': 0, 'gather': True, 't': 0.0},
                         {'seq': 'main', 'index': 0, 'gather': True, 't': 2.8}]}
    row = analyze_call(call, raw, compiled)['checks'][0]
    assert row['passed'] is False, row
    assert 'overlap=1.50s' in row['detail']


@pytest.mark.parametrize('speech,passed', [((2.0, 2.7), None), ((0.8, 1.8), False), ((2.4, 2.7), True)])
def test_a_mismatch_with_sound_after_it_leaves_the_rest_unconfirmed(voicemail, speech, passed):
    raw, compiled, root = voicemail
    audio_file = compiled.sequences['main'][0].audio_file
    sf.write(root / 'compiled' / 'other.wav', _noise(0.5, 11), PSTN_RATE)
    recording = _record_call(root / 'full-call.wav', root / 'compiled', [
        (audio_file, LEAD_S, 1.5), ('other.wav', LEAD_S + 1.5, None),
    ], [speech, (4.5, 8.0)], 12.0)
    call = {'full_call_recording_file': str(recording), 'compiled_audio_dir': str(root / 'compiled'),
            'segments': [{'seq': 'main', 'index': 0, 't': 0.0}]}
    rows = {r['check']: r for r in analyze_call(call, raw, compiled)['checks']}
    assert rows['max_overlap_with_playback']['passed'] is passed, rows['max_overlap_with_playback']


def test_a_mismatch_before_a_pause_is_not_taken_for_a_cut(tmp_path):
    greeting = np.concatenate([_noise(1.0, 13), np.zeros(int(0.3 * SAMPLE_RATE), np.int16),
                               _noise(1.0, 14)])
    sf.write(tmp_path / 'greeting.wav', greeting, SAMPLE_RATE)
    raw = {'name': 'pause', 'machine': {'main': [{'play': 'greeting.wav'}, {'hold': 5}]},
           'expect': {'max_overlap_with_playback': {'asset': 'greeting.wav', 'max_s': 0.5}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    recording = _record_call(tmp_path / 'full-call.wav', tmp_path / 'compiled',
                             [(compiled.sequences['main'][0].audio_file, LEAD_S, None)],
                             [(LEAD_S + 1.6, LEAD_S + 2.2)], 8.0,
                             corrupt=(LEAD_S + 0.95, LEAD_S + 1.0))
    call = {'full_call_recording_file': str(recording), 'compiled_audio_dir': str(tmp_path / 'compiled'),
            'segments': [{'seq': 'main', 'index': 0, 't': 0.0}]}
    row = analyze_call(call, raw, compiled)['checks'][0]
    assert row['passed'] is None, row


def test_an_asset_played_twice_in_one_segment_is_not_measured(tmp_path):
    sf.write(tmp_path / 'greeting.wav', _noise(1.0, 9), SAMPLE_RATE)
    raw = {'name': 'twice', 'machine': {'main': [
        {'play': 'greeting.wav'}, {'wait': 1.0}, {'play': 'greeting.wav'}, {'hold': 5},
    ]}, 'expect': {'max_overlap_with_playback': {'asset': 'greeting.wav', 'max_s': 0.5}}}
    compiled = compile_scenario(raw, assets_dir=tmp_path, out_dir=tmp_path / 'compiled')
    assert 'play_start:greeting.wav' not in compiled.sequences['main'][0].marks
    _, rows = _analyze(tmp_path, raw, compiled, [(0.6, 1.4)], total_s=8.0)
    assert rows['max_overlap_with_playback']['passed'] is None
