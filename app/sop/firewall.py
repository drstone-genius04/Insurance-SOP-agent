"""Seal the directive before the model sees it.

The reference harness hands the responder the full claim, then scans the reply for leaks.
That is too late: the model has already read amounts, denial text, and sibling files.
This firewall drops anything the current phase and workflow path are not allowed to discuss.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.sop.phrases import PATH_FIELDS

_MONEY_KEYS = {
    "expected_reimbursement_amount",
    "allowed_max_amount",
    "net_pay",
    "net_fee",
    "amount",
    "paid",
}
_CLAIMISH = {
    "claim",
    "claims",
    "denial_reason",
    "documents_needed",
    "case_id",
    "active_case_id",
    *_MONEY_KEYS,
}
_CASE_ID = re.compile(r"\bCL-\d+\b", re.I)
_MONEY = re.compile(r"\$\s?\d")


def seal(directive: dict[str, Any], verified: bool) -> list[str]:
    """Mutate ``directive`` in place. Return the names of fields that were removed."""
    facts = directive.get("facts")
    if not isinstance(facts, dict):
        return []
    dropped: list[str] = []
    if not verified:
        dropped.extend(_seal_unverified(facts))
    else:
        dropped.extend(_seal_verified(facts))
    if dropped:
        directive["facts"] = facts
        directive["allowed_text"] = "" if not verified else _json_safe(facts)
        directive["sealed"] = dropped
    return dropped


def _seal_unverified(facts: dict[str, Any]) -> list[str]:
    dropped: list[str] = []
    for key in list(facts):
        if key in _CLAIMISH or _looks_secret(facts[key]):
            facts.pop(key, None)
            dropped.append(key)
    return dropped


def _seal_verified(facts: dict[str, Any]) -> list[str]:
    dropped: list[str] = []
    path = str(facts.get("workflow_path") or "general_claim_question")
    allowed = set(PATH_FIELDS.get(path, PATH_FIELDS["general_claim_question"]))
    claim = facts.get("claim")
    if isinstance(claim, dict):
        for key in list(claim):
            if key not in allowed:
                claim.pop(key, None)
                dropped.append(f"claim.{key}")
        facts["claim"] = claim
    # A list of other claims is how the harness leaks sibling files into the prompt.
    if isinstance(facts.get("claims"), list) and facts.get("workflow_path"):
        facts.pop("claims", None)
        dropped.append("claims")
    guidance = facts.get("guidance")
    if path != "status_inquiry" and isinstance(guidance, list):
        kept = [line for line in guidance if isinstance(line, str) and not _MONEY.search(line)]
        if len(kept) != len(guidance):
            facts["guidance"] = kept
            dropped.append("guidance.amounts")
    return dropped


def _looks_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in _CLAIMISH or _looks_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_looks_secret(item) for item in value)
    if isinstance(value, str):
        return bool(_CASE_ID.search(value) or _MONEY.search(value))
    return False


def _json_safe(facts: dict[str, Any]) -> str:
    return json.dumps(facts)
