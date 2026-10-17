"""Output guard rules ported from the finished harness patterns."""

from app.sop.guard import needs_regen, review


def _directive(**facts):
    return {
        "facts": facts,
        "leak_terms": [
            "pathology report",
            "the review file did not include the pathology report and the treating provider office note",
            "2026-03-18",
        ],
        "fallback": "Some of the details you gave did not match our records. Which remaining detail can you share?",
        "must_ask": "Which remaining detail can you share?",
        "empathy": "",
        "allowed_text": "",
    }


def test_false_status_and_leak_fall_back() -> None:
    reply, flags, corrections = review(
        "Perfect, you're verified now. CL-2048 was denied for a missing pathology report.",
        _directive(account_located=True, factors_still_needed=1),
        verified=False,
    )
    assert "claim_leak" in flags
    assert "false_status" in flags
    assert "pathology" not in reply.lower()
    assert "verified now" not in reply.lower()
    assert needs_regen(flags)
    assert corrections


def test_conditional_verified_passes() -> None:
    reply, flags, _ = review(
        "Once you're verified I can open the claim. Which remaining detail can you share?",
        _directive(account_located=True, factors_still_needed=1),
        verified=False,
    )
    assert "false_status" not in flags
    assert "once you're verified" in reply.lower()


def test_unstated_mismatch_and_human_offer() -> None:
    reply, flags, _ = review(
        "Thanks Margaret. Which remaining detail can you share?",
        _directive(
            account_located=True,
            factors_still_needed=1,
            mismatch_this_turn=True,
            offer_human=True,
        ),
        verified=False,
    )
    assert "unstated_mismatch" in flags
    assert "missing_human_offer" in flags
    assert "did not match" in reply.lower()
    assert "specialist" in reply.lower() or "transfer" in reply.lower()


def test_wrong_factor_count() -> None:
    reply, flags, _ = review(
        "I still need 1 more detail. Which remaining detail can you share?",
        _directive(account_located=True, factors_still_needed=2, mismatch_this_turn=False),
        verified=False,
    )
    assert "wrong_factor_count" in flags
    assert "did not match" in reply.lower() or "remaining" in reply.lower()


def test_empathy_does_not_force_regeneration() -> None:
    reply, flags, _ = review(
        "I know this is frustrating. I still need 2 more details before I can open the claim.",
        _directive(account_located=True, factors_still_needed=2, empathy="I know this is frustrating."),
        verified=False,
    )
    assert "empathy_added" not in flags or not needs_regen(flags)
    assert "pathology" not in reply.lower()
    assert "frustrating" in reply.lower()


def test_path_facts_hide_money_on_denial() -> None:
    from app.sop.phrases import path_facts

    claim = {
        "case_id": "CL-2048",
        "status": "denied",
        "denial_reason": "missing pathology report",
        "allowed_max_amount": "1450.00",
        "net_pay": "0.00",
        "documents_needed": ["pathology report"],
    }
    denial = path_facts(claim, "denial_question")
    status = path_facts(claim, "status_inquiry")
    assert "allowed_max_amount" not in denial
    assert "net_pay" not in denial
    assert denial["denial_reason"] == "missing pathology report"
    assert status["allowed_max_amount"] == "1450.00"
    assert "denial_reason" not in status


def test_approved_consent_cannot_ask_again() -> None:
    directive = {
        "facts": {"verified": True, "consent_status": "approved", "claim": {"case_id": "CL-2048"}},
        "allowed_text": '{"case_id": "CL-2048"}',
        "fallback": "Margaret Chen approved the consent request. CL-2048 is denied because the pathology report is missing.",
        "must_ask": "What else would you like to know about this claim?",
        "empathy": "",
        "leak_terms": [],
    }
    reply, flags, corrections = review(
        "I still need to hear from Margaret directly before we can go over her claim details.",
        directive,
        verified=True,
    )
    assert "stale_gate" in flags
    assert "pathology" in reply.lower()
    assert "still need to hear" not in reply.lower()
    assert corrections


def test_grounding_after_verify() -> None:
    directive = {
        "facts": {"claim": {"case_id": "CL-2048"}},
        "allowed_text": '{"case_id": "CL-2048", "net_pay": "0.00"}',
        "fallback": "CL-2048 is denied because the pathology report is missing.",
        "must_ask": "",
        "empathy": "",
        "leak_terms": [],
    }
    reply, flags, _ = review("Claim CL-9999 paid $9999.00.", directive, verified=True)
    assert "ungrounded" in flags
    assert "CL-9999" not in reply
    assert "pathology" in reply.lower()
