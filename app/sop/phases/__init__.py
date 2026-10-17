"""Phase handlers. The controller loops these until a handler needs caller input."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.sop import phrases
from app.sop.directives import ask, build_directive, handoff_directive
from app.sop.parse import Parsed
from app.sop.phases import post_process, process_case, resolve_intent, verify_id
from app.tools.records import Records

HANDLERS = {
    "VERIFY_ID": verify_id.handle,
    "RESOLVE_INTENT": resolve_intent.handle,
    "PROCESS_CASE": process_case.handle,
    "POST_PROCESS": post_process.handle,
    "CLOSED": post_process.handle_closed,
}


def run_phases(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    if off_topic and session["phase"] != "VERIFY_ID":
        need = ask(session)
        fallback = phrases.scope_reply(need, session["off_topic_streak"] >= 2, parsed.emotion)
        directive = build_directive(
            session,
            "Decline the off-topic question and return to the step.",
            {},
            need,
            fallback,
            records,
            parsed,
        )
        return directive, ["Declined an off-topic question."]
    # CLOSED / POST_PROCESS own their short replies; do not bounce "ok"/"thanks" back into the case ask.
    if parsed.scope == "small_talk" and session["phase"] not in {
        "VERIFY_ID",
        "HUMAN_HANDOFF",
        "POST_PROCESS",
        "CLOSED",
    }:
        need = ask(session)
        fallback = phrases.small_talk_reply(need)
        directive = build_directive(
            session,
            "Answer briefly and return to the step.",
            {},
            need,
            fallback,
            records,
            parsed,
        )
        return directive, ["Small talk."]

    decisions: list[str] = []
    directive: dict[str, Any] | None = None
    for _ in range(5):
        phase = session["phase"]
        if phase == "HUMAN_HANDOFF":
            directive, cont, decision = handoff_directive(session), False, "Transferred."
        else:
            handler = HANDLERS[phase]
            if phase == "VERIFY_ID":
                directive, cont, decision = handler(session, parsed, message, off_topic, records, settings)
            elif phase == "RESOLVE_INTENT":
                directive, cont, decision = handler(session, records)
            elif phase == "PROCESS_CASE":
                directive, cont, decision = handler(session, parsed, message, records)
            elif phase == "POST_PROCESS":
                directive, cont, decision = handler(session, parsed, records)
            else:
                directive, cont, decision = handler(session, parsed, message)
        decisions.append(decision)
        if not cont:
            break
    assert directive is not None
    return directive, decisions
