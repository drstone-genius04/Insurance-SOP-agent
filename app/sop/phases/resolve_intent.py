"""RESOLVE_INTENT: pick exactly one claim from memory hints."""

from __future__ import annotations

from typing import Any

from app.sop import phrases
from app.sop.directives import build_directive, empty_directive
from app.tools.records import Records, claim_brief


def handle(session: dict[str, Any], records: Records) -> tuple[dict[str, Any], bool, str]:
    hints = session["memory"]["case_hints"]
    remembered = phrases.hint_phrase(hints)
    hinted = any(hints.values())
    matched = records.filter_claims(session["party_id"], hints) if hinted else records.claims_for(session["party_id"])
    if len(matched) == 1:
        session["active_case_id"] = matched[0]["case_id"]
        session["phase"] = "PROCESS_CASE"
        return empty_directive(), True, f"Selected {matched[0]['case_id']}."
    pool = matched if matched else records.claims_for(session["party_id"])
    briefs = [claim_brief(row) for row in pool]
    fallback = phrases.list_claims_reply(briefs, remembered, ambiguous=bool(matched) and hinted)
    facts = {"verified": True, "claims": briefs, "caller_context": remembered}
    forbidden = ["denial reasons", "document names", "dollar amounts"]
    directive = build_directive(
        session,
        "Choose exactly one claim.",
        facts,
        "Which claim should I open?",
        fallback,
        records,
        None,
    )
    directive["forbidden"] = forbidden
    return directive, False, "Need the caller to pick a claim."
