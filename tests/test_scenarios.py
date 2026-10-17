"""Offline scenario suite (shared definitions with scripts/live_demo.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.eval.scenarios import NOT_CONFIGURED_MARKER, SCENARIOS
from app.llm.base import NullLLM
from tests.conftest import FakeLLM, say


def _run(client: TestClient, name: str) -> None:
    scenario = SCENARIOS[name]
    opened = client.post("/api/session", json=scenario.session_body).json()
    session_id = opened["session_id"]
    failures: list[str] = []
    for turn in scenario.turns:
        result = say(client, session_id, turn.text)
        reply, state = result["reply"], result["state"]
        for check in turn.checks:
            label, ok, detail = check(state, reply)
            if not ok:
                failures.append(f"{name}: {label} ({detail}) reply={reply!r}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "name",
    [name for name in SCENARIOS if name != "unconfigured_notice"],
)
def test_scenario_offline(client: TestClient, name: str) -> None:
    _run(client, name)


def test_unconfigured_scenario(make_client) -> None:
    bare = make_client(NullLLM(), llm_api_key="")
    scenario = SCENARIOS["unconfigured_notice"]
    opened = bare.post("/api/session", json={}).json()
    session_id = opened["session_id"]
    result = say(bare, session_id, scenario.turns[0].text)
    assert NOT_CONFIGURED_MARKER in result["reply"].lower()
    assert result["state"]["phase"] == "VERIFY_ID"
    assert result["state"]["verified"] is False


def test_fake_llm_injects_extractor_script(make_client) -> None:
    fake = FakeLLM(
        scripts={
            "policy pol-9921": {
                "name": "Margaret Chen",
                "dob": "1985-03-15",
                "id_last4": "4472",
                "policy_number": "POL-9921",
                "case_hints": {"case_type": "healthcare", "status": "denied", "month": 1},
                "intent_path": "denial_question",
            }
        }
    )
    client = make_client(fake)
    opened = client.post("/api/session", json={}).json()
    # Utterance repeats the values so _said_this_turn accepts the FakeLLM extract.
    result = say(
        client,
        opened["session_id"],
        "I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
        "I'm calling about my denied healthcare claim from January. "
        "DOB is 1985-03-15, SSN last four is 4472.",
    )
    assert fake.json_calls
    assert result["state"]["phase"] == "PROCESS_CASE"
    assert result["state"]["active_case_id"] == "CL-2048"
