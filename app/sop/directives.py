"""Shared directive builders and session helpers used by every phase."""

from __future__ import annotations

import json
from typing import Any

from app.sop import phrases
from app.sop.parse import Parsed
from app.tools.records import Records

FACTORS = phrases.FACTORS
NEGATIVE = {"angry", "frustrated", "refusing"}
FREEDOM = {
    "VERIFY_ID": "strict",
    "RESOLVE_INTENT": "flexible",
    "PROCESS_CASE": "flexible",
    "POST_PROCESS": "consent",
    "CLOSED": "minimal",
    "HUMAN_HANDOFF": "terminal",
}


def locate(session: dict[str, Any], records: Records) -> dict[str, Any] | None:
    if session.get("party_id"):
        return records.by_id(session["party_id"])
    found = records.locate(session["slots"])
    if found:
        session["party_id"] = found["party_id"]
        session["account_located"] = True
    return found


def mark_verified(session: dict[str, Any], record: dict[str, Any]) -> None:
    session["verified"] = True
    session["party_id"] = record["party_id"]
    session["account_located"] = True
    session["policyholder_name"] = record["name"]
    session["phase"] = "RESOLVE_INTENT"
    session["turn_flags"]["just_verified"] = True


def handoff(session: dict[str, Any], reason: str) -> None:
    session["phase"] = "HUMAN_HANDOFF"
    session["handoff_reason"] = reason
    verified = "yes" if session["verified"] else "no"
    who = session["caller_role"]
    if who == "representative":
        who = (
            f"representative {session.get('representative_name') or ''} "
            f"({session.get('relationship') or 'unspecified'}) for {session.get('on_behalf_of') or 'unknown'}"
        )
    session["handoff_note"] = f"Reason: {reason}. Identity verified: {verified}. Caller: {who}."


def empty_directive() -> dict[str, Any]:
    return {
        "phase": "",
        "freedom": "",
        "goal": "",
        "facts": {},
        "must_ask": "",
        "instructions": [],
        "forbidden": [],
        "tone": "",
        "empathy": "",
        "fallback": "",
        "leak_terms": [],
        "allowed_text": "",
    }


def build_directive(
    session: dict[str, Any],
    goal: str,
    facts: dict[str, Any],
    must_ask: str,
    fallback: str,
    records: Records | None,
    parsed: Parsed | None,
) -> dict[str, Any]:
    emotion = parsed.emotion if parsed else session.get("last_emotion") or "neutral"
    verified = bool(session["verified"])
    allowed = json.dumps(facts) if verified else ""
    forbidden = []
    if not verified:
        forbidden = [
            "claim numbers",
            "denial reasons",
            "documents",
            "dollar amounts",
            "saying the caller is verified",
        ]
    return {
        "phase": session["phase"],
        "freedom": FREEDOM.get(session["phase"], "strict"),
        "goal": goal,
        "facts": facts,
        "must_ask": must_ask,
        "instructions": [
            "Answer only from facts.",
            (
                "Acknowledge emotion before the ask."
                if emotion in NEGATIVE or emotion in {"anxious", "confused"}
                else "Be warm and brief."
            ),
        ],
        "forbidden": forbidden,
        "tone": emotion,
        "empathy": phrases.empathy_line(emotion) if emotion != "neutral" else "",
        "fallback": fallback,
        "leak_terms": list(records.leak_terms) if records and not verified else [],
        "allowed_text": allowed,
    }


def handoff_directive(session: dict[str, Any]) -> dict[str, Any]:
    reason = session.get("handoff_reason") or "transfer"
    fallback = phrases.handoff_reply(reason, bool(session["verified"]))
    return build_directive(
        session,
        "Transfer and stop asking questions.",
        {"reason": reason, "verified": session["verified"]},
        "",
        fallback,
        None,
        None,
    )


def ask(session: dict[str, Any]) -> str:
    phase = session["phase"]
    if phase == "VERIFY_ID" and session["caller_role"] == "representative":
        return "I still need the policyholder's approval before I can open the claim."
    if phase == "VERIFY_ID" and not session["account_located"]:
        return "What full name and policy number should I use, or which phone number or email is on the policy?"
    if phase == "VERIFY_ID":
        return "Which remaining detail can you share?"
    if phase == "RESOLVE_INTENT":
        return "Which claim should I open?"
    if phase == "PROCESS_CASE":
        return "What else would you like to know about this claim?"
    if phase == "POST_PROCESS":
        return "Should I send the summary, or skip the email?"
    return "How else can I help with the claim?"


def ensure_offer(text: str) -> str:
    if "transfer" in text.lower() or "specialist" in text.lower():
        return text
    return text.rstrip() + " If you'd rather talk to a person, I can transfer you to a claims specialist."


def path_forbidden(path: str) -> list[str]:
    if path == "status_inquiry":
        return ["documents that are not on this claim", "a denial reason that is not in facts"]
    if path in {"denial_question", "appeal", "document_submission", "next_steps"}:
        return ["dollar amounts", "reimbursement figures", "claim ids other than the active one"]
    return ["dollar amounts", "denial reasons that are not in facts"]
