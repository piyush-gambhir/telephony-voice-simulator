from pathlib import Path

from telephony_voice_simulator.grading import personas as judge
from telephony_voice_simulator.grading import scribe


def _words(*spans):
    return [{"text": t, "start": a, "end": b} for t, a, b in spans]


def test_summary_times_the_far_party_and_the_reply_gaps():
    channels = [
        {"channel": 0, "words": _words(("Thanks", 1.2, 1.5), ("for", 1.6, 1.7), ("calling.", 1.8, 2.2),
                                       ("Sure,", 6.1, 6.4), ("one", 6.5, 6.6), ("moment.", 6.7, 7.0),
                                       ("pineapple", 12.0, 12.6))},
        {"channel": 1, "words": _words(("Hi,", 3.0, 3.2), ("I", 3.3, 3.4), ("need", 3.5, 3.7),
                                       ("service.", 3.8, 4.5), ("lighthouse", 10.0, 10.8))},
    ]
    summary = scribe.summarize(channels)
    assert summary["ttfw_s"] == 1.2
    assert summary["remote_text"] == "Thanks for calling. Sure, one moment. pineapple"
    assert summary["response_gaps"] == 2
    assert summary["response_gap_p50_s"] == 1.4 and summary["response_gap_p95_s"] == 1.6
    assert [t["channel"] for t in summary["turns"]] == [0, 1, 0, 1, 0]


def test_no_words_and_no_key_mean_no_evidence(tmp_path, monkeypatch):
    assert scribe.summarize([])["ttfw_s"] is None
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    assert scribe.transcribe(audio) is None
    assert scribe.transcribe(Path(tmp_path / "missing.wav"), key="k") is None


def test_checks_accept_a_phrase_from_either_transcript():
    persona = {"checks": {"must_say": ["pineapple"], "must_not_say": ["lighthouse"], "max_turns": None}}
    dialogue = [{"role": "user", "content": "the code word is pine apple"}]
    misheard = judge.run_checks(persona, dialogue)
    assert [c["passed"] for c in misheard] == [False, True]
    rescued = judge.run_checks(persona, dialogue, heard="The code word is pineapple.")
    assert rescued[0]["passed"] is True and rescued[0]["heard_in"] == ["recording"]
    caught = judge.run_checks(persona, dialogue, heard="lighthouse")
    assert caught[1]["passed"] is False and caught[1]["heard_in"] == ["recording"]
