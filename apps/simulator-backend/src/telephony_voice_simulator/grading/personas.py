"""Grading against customer personas, with ElevenLabs agent tests as the judge.

Two kinds of ElevenLabs simulation test are built here:

* Fidelity tests check the persona agent itself: ElevenLabs plays a dealership from the
  persona's scenario and judges whether the customer stayed in character.
* Judge tests grade a finished call. The call's transcript is replayed as chat history,
  the persona agent says one goodbye turn, and the evaluator judges one expectation about
  the agent under test (the "user" role in ElevenLabs terms). One test per expectation
  keeps each verdict independent.

Deterministic checks run locally on the transcript, and a call passes only when every
check and every expectation passes.
"""

from __future__ import annotations

import re
from typing import Any

# Realtime replies carry expressive tags such as "[friendly]"; they are not speech.
_TAG = re.compile(r"\[[A-Za-z][A-Za-z -]{0,30}\]\s*")
MAX_HISTORY = 200
# How a simulation ended, which ElevenLabs lists among the evaluator's reasons.
_RUN_NOTES = {
    "The simulated user ended the conversation.",
    "The conversation reached the maximum number of turns.",
}
FIDELITY_TURNS = 12
DEFAULT_SCENARIO = (
    "You are a service advisor at a car dealership, calling the customer about an open "
    "recall on their vehicle. Offer two appointment slots, one weekday morning and one "
    "weekday afternoon. If they accept one, read back the booking and say goodbye. If they "
    "refuse, acknowledge it politely and end the call."
)
FIDELITY_CONDITIONS = (
    "The customer stayed in character as the customer and never acted as the dealership.",
    "The customer never invented an appointment time, ID or confirmation the advisor did "
    "not offer.",
    "The customer's answers were consistent with their profile.",
)
REPLAY_SCENARIO = "The call already happened as shown in the history. Say a short goodbye."
# In ElevenLabs' history the persona is the "agent" role, but persona expectations say
# "the agent" for the agent under test, so the condition spells out who is who.
REPLAY_PREFIX = (
    "Judge only the earlier conversation in the history. In this conversation the user role "
    "is the dealership's agent, and the agent role is the customer. In the statement below, "
    '"the agent" always means the dealership\'s agent (the user role). Statement: '
)


def strip_tags(text: str) -> str:
    return _TAG.sub("", text).strip()


def chat_history(dialogue: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Our dialogue (user = agent under test, assistant = persona) as ElevenLabs history."""
    if len(dialogue) > MAX_HISTORY:
        raise ValueError(f"The transcript has {len(dialogue)} turns; judging allows {MAX_HISTORY}")
    return [
        {
            "role": "agent" if turn["role"] == "assistant" else "user",
            "message": turn["content"],
            "time_in_call_secs": index,
        }
        for index, turn in enumerate(dialogue)
    ]


def fidelity_test(name: str, persona: dict[str, Any], model: str) -> dict[str, Any]:
    fidelity = persona["fidelity"]
    return {
        "name": name,
        "type": "simulation",
        "dynamic_variables": {"customer_profile": persona["profile"]},
        "simulation_scenario": fidelity["scenario"] or DEFAULT_SCENARIO,
        "success_conditions": [*FIDELITY_CONDITIONS, *fidelity["conditions"]],
        "simulation_max_turns": FIDELITY_TURNS,
        "evaluation_model": model,
        "simulated_user_model": model,
    }


def judge_test(
    name: str, persona: dict[str, Any], dialogue: list[dict[str, str]], expectation: str, model: str
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "simulation",
        "dynamic_variables": {"customer_profile": persona["profile"]},
        "chat_history": chat_history(dialogue),
        "simulation_scenario": REPLAY_SCENARIO,
        "success_conditions": [REPLAY_PREFIX + expectation],
        "simulation_max_turns": 1,
        "evaluation_model": model,
        "simulated_user_model": model,
    }


def run_checks(persona: dict[str, Any], dialogue: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Deterministic checks on the agent under test's turns."""
    spoken = " ".join(t["content"] for t in dialogue if t["role"] == "user").lower()
    checks = persona["checks"]
    results = [
        {"check": f"says {phrase!r}", "passed": phrase.lower() in spoken}
        for phrase in checks["must_say"]
    ]
    results += [
        {"check": f"never says {phrase!r}", "passed": phrase.lower() not in spoken}
        for phrase in checks["must_not_say"]
    ]
    if checks["max_turns"] is not None:
        results.append({
            "check": f"at most {checks['max_turns']} turns",
            "passed": len(dialogue) <= checks["max_turns"],
        })
    return results


def finished(invocation: dict[str, Any]) -> bool:
    runs = invocation.get("test_runs") or []
    return bool(runs) and all(run.get("status") in {"passed", "failed"} for run in runs)


def run_results(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    """Each run's verdict, the evaluator's reasons and its credits."""
    results = []
    for run in invocation.get("test_runs") or []:
        rationale = (run.get("condition_result") or {}).get("rationale") or {}
        results.append({
            "test_id": run.get("test_id"),
            "test_name": run.get("test_name"),
            "status": run.get("status"),
            "passed": run.get("status") == "passed",
            "reasons": [m for m in rationale.get("messages") or [] if m not in _RUN_NOTES],
            "credits": run.get("credits_used"),
        })
    return results


def ran_as_saved(invocation: dict[str, Any], bodies: dict[str, dict[str, Any]]) -> bool:
    """Whether every run judged the conditions and history its test was saved with."""
    for run in invocation.get("test_runs") or []:
        body, info = bodies.get(run.get("test_id")), run.get("test_info") or {}
        if body is None:
            return False
        ran_history = [(t.get("role"), t.get("message")) for t in info.get("chat_history") or []]
        saved_history = [(t["role"], t["message"]) for t in body["chat_history"]]
        if info.get("success_conditions") != body["success_conditions"] or (
            ran_history != saved_history
        ):
            return False
    return True
