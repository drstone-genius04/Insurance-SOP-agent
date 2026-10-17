"""The model only sees facts the phase already allowed."""

from app.sop.engine import _merge_model
from app.sop.firewall import seal
from app.sop.parse import Parsed


def test_unverified_prompt_loses_claim_facts() -> None:
    directive = {
        "facts": {
            "verified": False,
            "claim": {"case_id": "CL-2048", "denial_reason": "missing report", "net_pay": 50},
            "factors_matched": 1,
        },
        "allowed_text": "CL-2048",
    }
    dropped = seal(directive, verified=False)
    assert "claim" not in directive["facts"]
    assert directive["facts"]["factors_matched"] == 1
    assert "claim" in dropped
    assert directive["allowed_text"] == ""


def test_denial_path_cannot_carry_payment_fields() -> None:
    directive = {
        "facts": {
            "verified": True,
            "workflow_path": "denial_question",
            "claim": {
                "case_id": "CL-2048",
                "status": "denied",
                "denial_reason": "missing report",
                "net_pay": 1200,
                "allowed_max_amount": 5000,
            },
            "guidance": ["Send the report.", "The paid amount is $1200."],
        }
    }
    seal(directive, verified=True)
    claim = directive["facts"]["claim"]
    assert claim["denial_reason"] == "missing report"
    assert "net_pay" not in claim
    assert "allowed_max_amount" not in claim
    assert directive["facts"]["guidance"] == ["Send the report."]


def test_status_path_keeps_payment_fields() -> None:
    directive = {
        "facts": {
            "workflow_path": "status_inquiry",
            "claim": {"case_id": "CL-2048", "status": "closed", "net_pay": 80},
        }
    }
    seal(directive, verified=True)
    assert directive["facts"]["claim"]["net_pay"] == 80


def test_model_cannot_widen_a_denial_turn_into_a_payment_path() -> None:
    parsed = Parsed()
    parsed.intent_path = "denial_question"
    _merge_model(parsed, {"intent_path": "status_inquiry"}, "PROCESS_CASE", "why was it denied")
    assert parsed.intent_path == "denial_question"


def test_model_status_path_requires_the_caller_to_ask() -> None:
    quiet = Parsed()
    _merge_model(quiet, {"intent_path": "status_inquiry"}, "PROCESS_CASE", "tell me about the file")
    assert quiet.intent_path is None
    asked = Parsed()
    _merge_model(asked, {"intent_path": "status_inquiry"}, "PROCESS_CASE", "what is the payment amount")
    assert asked.intent_path == "status_inquiry"
