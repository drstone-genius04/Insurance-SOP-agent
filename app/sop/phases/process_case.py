"""PROCESS_CASE: grounded answers on the active claim."""

from __future__ import annotations

from typing import Any

from app.sop import phrases
from app.sop.directives import build_directive, empty_directive, path_forbidden
from app.sop.parse import Parsed
from app.tools.records import Records


def handle(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    records: Records,
) -> tuple[dict[str, Any], bool, str]:
    if parsed.conversation_done:
        session["phase"] = "POST_PROCESS"
        return empty_directive(), True, "Caller is wrapping up."
    hints = session["memory"]["case_hints"]
    if any(hints.values()):
        matched = records.filter_claims(session["party_id"], hints)
        if len(matched) == 1 and matched[0]["case_id"] != session["active_case_id"]:
            session["turn_flags"]["switched_from"] = session["active_case_id"]
            session["active_case_id"] = matched[0]["case_id"]
    claim = records.claim(session["active_case_id"])
    if claim is None:
        session["phase"] = "RESOLVE_INTENT"
        return empty_directive(), True, "No active claim."
    remembered = phrases.hint_phrase(hints) if session["memory"]["saved_before_verification"] else ""
    switched = session["turn_flags"].get("switched_from")
    if parsed.email_preference and not parsed.case_type and not parsed.intent_path:
        fallback = (
            "I've noted that you'd like a summary by email. I'll ask before I send anything. "
            "What else would you like to know about this claim?"
        )
    else:
        fallback = phrases.claim_reply(claim, message, remembered, switched, session.get("intent_path"))
    if session["turn_flags"].get("just_approved"):
        fallback = f"{session['policyholder_name']} approved the consent request. " + fallback
    elif session["turn_flags"].get("just_verified"):
        name = session["policyholder_name"]
        fallback = f"You're verified, {name}. " + fallback
    if session["memory"]["email_preference"] == "yes" and "email" in message.lower():
        fallback += " I've noted that you'd like this by email, and I'll ask before I send anything."
    path = session.get("intent_path") or "general_claim_question"
    allowed_claim = phrases.path_facts(claim, path)
    facts = {
        "verified": True,
        "workflow_path": path,
        "claim": allowed_claim,
        "guidance": (
            records.guidance_lines(claim, path, message)
            if path in {"document_submission", "next_steps", "appeal", "denial_question"}
            else []
        ),
        "caller_context": remembered,
    }
    if session.get("caller_role") == "representative":
        facts["consent_status"] = session.get("consent_status")
        facts["speaking_with"] = session.get("representative_name")
    if path == "status_inquiry":
        facts["field_descriptions"] = records.schema.get("field_descriptions") or {}
    goal = f"Answer only on the {path} path, using the claim fields in facts and nothing else."
    if session.get("consent_status") == "approved":
        goal += " Consent is already approved. Do not ask for it again."
    directive = build_directive(
        session,
        goal,
        facts,
        "What else would you like to know about this claim?",
        fallback,
        records,
        parsed,
    )
    directive["forbidden"] = path_forbidden(path)
    if session.get("consent_status") == "approved":
        directive["forbidden"] = [*directive["forbidden"], "asking for consent or identity again"]
    return directive, False, f"Answered {path} from {claim['case_id']}."
