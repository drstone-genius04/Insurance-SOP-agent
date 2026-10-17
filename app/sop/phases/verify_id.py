"""VERIFY_ID and representative consent gate."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.sop import phrases
from app.sop.directives import (
    FACTORS,
    ask,
    build_directive,
    empty_directive,
    handoff,
    handoff_directive,
    locate,
    mark_verified,
)
from app.sop.parse import Parsed
from app.tools.records import Records


def handle(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], bool, str]:
    if session["caller_role"] == "representative":
        return handle_representative(session, parsed, off_topic, records, settings)
    if off_topic or parsed.scope == "small_talk":
        need = ask(session)
        fallback = (
            phrases.scope_reply(need, session["off_topic_streak"] >= 2, parsed.emotion)
            if off_topic
            else phrases.small_talk_reply(need)
        )
        return (
            build_directive(session, "Stay on identity verification.", {}, need, fallback, records, parsed),
            False,
            "Off-topic during verification." if off_topic else "Small talk during verification.",
        )

    record = locate(session, records)
    mismatch = False
    not_found = False
    if record is None:
        if any(session["slots"].values()):
            not_found = True
            mismatch = True
    else:
        for factor in FACTORS:
            value = session["slots"].get(factor)
            if not value:
                continue
            if records.factor_matches(record, factor, value):
                session["matched"][factor] = True
            else:
                session["matched"][factor] = False
                session["slots"][factor] = None
                mismatch = True
    if mismatch:
        session["verify_attempts"] += 1
        if session["verify_attempts"] > settings.max_verify_attempts:
            handoff(session, f"identity not verified after {session['verify_attempts']} attempts")
            return handoff_directive(session), False, "Identity attempts exhausted."
    if sum(session["matched"].values()) >= phrases.NEEDED and record is not None:
        mark_verified(session, record)
        return empty_directive(), True, f"Verified {sum(session['matched'].values())} factors."

    offer = session["verify_attempts"] >= settings.max_verify_attempts and mismatch
    remembered = phrases.hint_phrase(session["memory"]["case_hints"])
    fallback = phrases.verify_reply(
        located=record is not None,
        matched=session["matched"],
        mismatch=mismatch and not not_found,
        not_found=not_found,
        offer_human=offer,
        emotion=parsed.emotion,
        remembered=remembered,
        policyholder=record["name"] if record and session["matched"].get("name") else None,
    )
    facts = {
        "verified": False,
        "account_located": record is not None,
        "factors_matched": sum(session["matched"].values()),
        "factors_still_needed": max(0, phrases.NEEDED - sum(session["matched"].values())),
        "acceptable_factors": [phrases.FACTOR_LABELS[name] for name in FACTORS if not session["matched"].get(name)],
        "mismatch_this_turn": mismatch,
        "offer_human": offer,
        "caller_context": remembered,
    }
    need = "What detail can you share so I can verify the policy?"
    decision = "Identity mismatch." if mismatch else "Waiting for more identity details."
    return (
        build_directive(session, "Verify identity before opening any claim.", facts, need, fallback, records, parsed),
        False,
        decision,
    )


def handle_representative(
    session: dict[str, Any],
    parsed: Parsed,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], bool, str]:
    row = records.lookup_representative(session.get("representative_name"), session.get("on_behalf_of"))
    remembered = phrases.hint_phrase(session["memory"]["case_hints"])
    if row is None:
        if session.get("representative_name") or session.get("on_behalf_of"):
            session["verify_attempts"] += 1
            if session["verify_attempts"] > settings.max_verify_attempts:
                handoff(session, f"identity not verified after {session['verify_attempts']} attempts")
                return handoff_directive(session), False, "Representative not found. Attempts exhausted."
        offer = session["verify_attempts"] >= settings.max_verify_attempts
        fallback = phrases.representative_reply(
            found=False,
            status="pending",
            rep_name=session.get("representative_name"),
            buyer_name=session.get("on_behalf_of"),
            offer_human=offer,
            emotion=parsed.emotion,
            remembered=remembered,
        )
        return (
            build_directive(
                session,
                "Find an authorization before opening a claim.",
                {"verified": False},
                "Who are you calling for?",
                fallback,
                records,
                parsed,
            ),
            False,
            "Representative not on file.",
        )

    session["consent_checks"] += 1
    status = records.consent_status(session["consent_scenario"], session["consent_checks"])
    session["consent_status"] = status
    buyer = records.by_id(row.get("buyer_party_id"))
    buyer_name = buyer["name"] if buyer else row.get("buyer_name")
    if status == "timed_out":
        handoff(session, "policyholder consent not received")
        return handoff_directive(session), False, "Consent timed out."
    if status == "approved" and buyer:
        mark_verified(session, buyer)
        session["turn_flags"]["just_approved"] = True
        session["relationship"] = session.get("relationship") or row.get("relationship")
        return empty_directive(), True, "Policyholder approved consent."
    offer = off_topic and session["off_topic_streak"] >= 2
    fallback = phrases.representative_reply(
        found=True,
        status="pending",
        rep_name=session.get("representative_name"),
        buyer_name=buyer_name,
        offer_human=offer,
        emotion=parsed.emotion,
        remembered=remembered,
    )
    if off_topic:
        fallback = phrases.scope_reply(
            "I still need the policyholder's approval before I can open the claim.",
            offer,
            parsed.emotion,
        )
    facts = {"verified": False, "consent_status": "pending", "caller_context": remembered}
    return (
        build_directive(
            session,
            "Wait for policyholder consent. Do not verify with their ID.",
            facts,
            "I can stay on the line while they respond.",
            fallback,
            records,
            parsed,
        ),
        False,
        "Consent pending.",
    )
