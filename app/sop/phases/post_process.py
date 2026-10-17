"""POST_PROCESS email consent gate and CLOSED reopen rules."""

from __future__ import annotations

from typing import Any

from app.sop import phrases
from app.sop.directives import build_directive, empty_directive
from app.sop.guard import contains_secret
from app.sop.parse import Parsed
from app.tools.records import Records, mask_email


def handle(session: dict[str, Any], parsed: Parsed, records: Records) -> tuple[dict[str, Any], bool, str]:
    record = records.by_id(session["party_id"]) or {}
    on_file = record.get("email")
    masked = mask_email(on_file)
    claim = records.claim(session["active_case_id"])
    if not on_file:
        session["phase"] = "CLOSED"
        fallback = "There is no email on file, so I will close the call without sending a summary."
        return (
            build_directive(session, "Close without an email.", {"verified": True, "sent": False}, "", fallback, records, parsed),
            False,
            "No email on file.",
        )
    supplied = parsed.email
    if supplied and supplied != str(on_file).lower():
        fallback = phrases.email_other_address(masked)
        return (
            build_directive(
                session,
                "Decline any address that is not on file.",
                {"masked_email": masked, "sent": False},
                "Should I send it to the address on file, or skip the email?",
                fallback,
                records,
                parsed,
            ),
            False,
            "Declined a different email address.",
        )
    consent = parsed.email_consent
    if consent is None:
        fallback = phrases.email_offer(masked, session["memory"]["email_preference"], claim, parsed.emotion)
        facts = {
            "masked_email": masked,
            "email_preference": session["memory"]["email_preference"],
            "active_case_id": session.get("active_case_id"),
            "sent": False,
        }
        return (
            build_directive(
                session,
                "Ask before sending the summary. Do not send yet.",
                facts,
                "Should I send the summary, or skip the email?",
                fallback,
                records,
                parsed,
            ),
            False,
            "Offered the email summary.",
        )
    if consent == "yes":
        subject, body = phrases.summary_email(session, claim)
        if contains_secret(body, session["slots"]):
            subject, body = (
                "Summary of your claims call",
                "What we discussed:\n- A claim on your policy.\n\nClaim status:\n- See the call for the outcome on file.\n\nNext steps:\n- Call back if you need the next step repeated.\n",
            )
        session["outbox"].append({"to_masked": masked, "subject": subject, "body": body})
        session["phase"] = "CLOSED"
        fallback = phrases.email_sent(masked)
        return (
            build_directive(
                session,
                "Confirm the summary was sent.",
                {"masked_email": masked, "sent": True},
                "",
                fallback,
                records,
                parsed,
            ),
            False,
            "Email summary sent.",
        )
    if consent == "no":
        session["phase"] = "CLOSED"
        fallback = phrases.email_skipped()
        return (
            build_directive(session, "Close without sending.", {"sent": False}, "", fallback, records, parsed),
            False,
            "Caller declined the email.",
        )
    session["email_unclear"] += 1
    if session["email_unclear"] >= 2:
        session["phase"] = "CLOSED"
        fallback = phrases.email_unclear_close()
        return (
            build_directive(
                session,
                "Close because email consent stayed unclear.",
                {"sent": False},
                "",
                fallback,
                records,
                parsed,
            ),
            False,
            "Email consent unclear twice.",
        )
    fallback = phrases.email_offer(masked, session["memory"]["email_preference"], claim, parsed.emotion)
    facts = {
        "masked_email": masked,
        "email_preference": session["memory"]["email_preference"],
        "active_case_id": session.get("active_case_id"),
        "sent": False,
    }
    return (
        build_directive(
            session,
            "Ask again before sending. Do not send yet.",
            facts,
            "Should I send the summary, or skip the email?",
            fallback,
            records,
            parsed,
        ),
        False,
        "Email consent unclear.",
    )


def handle_closed(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
) -> tuple[dict[str, Any], bool, str]:
    asked_something = bool(parsed.intent_path or parsed.case_type or parsed.case_id or "?" in message)
    reopen = parsed.scope == "in_scope_claim" and not parsed.conversation_done and asked_something
    if reopen and parsed.scope != "small_talk":
        session["phase"] = "PROCESS_CASE" if session.get("active_case_id") else "RESOLVE_INTENT"
        return empty_directive(), True, "Reopened the file."
    fallback = phrases.closed_reply()
    facts = {"verified": session["verified"], "sent": bool(session["outbox"])}
    return (
        build_directive(session, "Close warmly.", facts, "", fallback, None, parsed),
        False,
        "Call already closed.",
    )
