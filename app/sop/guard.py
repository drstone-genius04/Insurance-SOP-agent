"""Check a drafted reply against the directive before the caller sees it.

Mirrors the finished harness: forbid leaks and false status, require mismatch and
human-offer statements when the controller set those flags, and check remaining-factor
counts. After verification, invented case ids and amounts are rejected.
"""

from __future__ import annotations

import re
from typing import Any

_FALSE_STATUS = [
    re.compile(p, re.I)
    for p in (
        r"\b(?:got|have|has) you verified\b",
        r"\byou(?:'re| are)(?: now)?(?: fully)? verified\b",
        r"\byou(?:'ve| have) been verified\b",
        r"\bverified now\b",
        r"\b(?:your )?identity (?:is|has been|was) (?:now )?(?:confirmed|verified)\b",
        r"\b(?:confirmed|verified) your identity\b",
        r"\b(?:looking|pulling|pulled|bringing) (?:at|up|into) your (?:account|records?|file|claim)\b",
        r"\b(?:got|have) your (?:claim|records?|file) (?:open|here|up|in front of me)\b",
        r"\b(?:finishing up|finalizing|finalising|processing|running|completing) (?:the |your )?verification\b",
        r"\b(?:have|got) (?:all (?:the |of the )?(?:information|details|info)|everything)(?: (?:I|we) need| now)\b",
    )
]
_NEGATION = re.compile(r"\b(not|until|once|before|after|haven'?t|have not|can'?t|cannot|without)\b", re.I)
_CLAUSE_BREAK = re.compile(r"[.!?]")
_CASE_ID = re.compile(r"\bCL-\d+\b", re.I)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d{2,5}\.\d{2}\b")
_EMPATHY = re.compile(r"\b(sorry|understand|frustrat|hear|worry|fair|uncomfortable|with you)\b", re.I)
_MISMATCH = re.compile(
    r"\b(did not match|didn't match|do not match|don't match|could not find an account|"
    r"couldn't find an account|details .+ match)\b",
    re.I,
)
_HUMAN_OFFER = re.compile(
    r"\b(transfer|specialist|human|claims representative|talk to a person|speak (?:to|with) a)\b",
    re.I,
)
_COUNT = re.compile(
    r"\b(?:still need|need|needs)\s+(\d+|one|two|three|four|five)\s+(?:more\s+)?(?:detail|details|factor|factors|piece|pieces)\b",
    re.I,
)
_WORD_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_SENT_CLAIM = re.compile(
    r"\b(I(?:'ve| have)? (?:already |just )?sent|(?:has|have|was|been) (?:already )?sent|"
    r"on its way|in your inbox|sending (?:it|that|the summary) now|I'll send (?:it|that|the summary) (?:right )?now)\b",
    re.I,
)
_STALE_GATE = re.compile(
    r"\b(still need to hear|verbal consent|before I can discuss|before we can go over|"
    r"put (?:her|him|them) on|cannot discuss|can't discuss|need (?:her|his|their) consent)\b",
    re.I,
)

CORRECTIONS = {
    "claim_leak": "Do not mention claim ids, denial reasons, documents, amounts, or other claim facts before verification.",
    "false_status": "Do not say the caller is verified or that you have their file open. Verification is not done yet.",
    "unstated_mismatch": "You must say that some of the details did not match our records (or that no account was found).",
    "missing_human_offer": "You must offer to transfer the caller to a human claims specialist.",
    "wrong_factor_count": "If you state how many details are still needed, the number must match the directive.",
    "ungrounded": "Only use case ids and amounts that appear in the directive facts.",
    "false_sent": "No summary has been sent. Do not say it was sent, is on its way, or is in the caller's inbox. Ask whether to send it or skip.",
    "stale_gate": "Verification or consent is already done. Answer from the claim. Do not ask for consent or identity again.",
}

# Soft fixes: applied without forcing a model regeneration.
SOFT = {"empathy_added", "ask_added"}


def review(reply: str, directive: dict[str, Any], verified: bool) -> tuple[str, list[str], list[str]]:
    """Return (safe_reply, flags, correction_lines). Corrections are for one regeneration."""
    flags: list[str] = []
    corrections: list[str] = []
    text = reply.strip() or directive.get("fallback") or ""
    facts = directive.get("facts") or {}

    if not verified:
        if _leak_hits(text, directive.get("leak_terms") or []):
            flags.append("claim_leak")
            corrections.append(CORRECTIONS["claim_leak"])
        if _false_verified(text):
            flags.append("false_status")
            corrections.append(CORRECTIONS["false_status"])
        if facts.get("mismatch_this_turn") and not _MISMATCH.search(text):
            flags.append("unstated_mismatch")
            corrections.append(CORRECTIONS["unstated_mismatch"])
        if facts.get("offer_human") and not _HUMAN_OFFER.search(text):
            flags.append("missing_human_offer")
            corrections.append(CORRECTIONS["missing_human_offer"])
        if facts.get("account_located") and _wrong_count(text, facts.get("factors_still_needed")):
            flags.append("wrong_factor_count")
            corrections.append(CORRECTIONS["wrong_factor_count"])
        if flags:
            text = directive.get("fallback") or text
            # Fallbacks from VERIFY_ID already carry mismatch wording; ensure the human offer sticks.
            if facts.get("offer_human") and not _HUMAN_OFFER.search(text):
                text = (
                    text.rstrip()
                    + " If you'd rather talk to a person, I can transfer you to a claims specialist."
                )
            if facts.get("mismatch_this_turn") and not _MISMATCH.search(text):
                text = "Some of the details you gave did not match our records. " + text
    else:
        invented = _invented(text, directive.get("allowed_text") or "")
        if invented:
            flags.append("ungrounded")
            corrections.append(CORRECTIONS["ungrounded"])
            text = directive.get("fallback") or text
        if facts.get("claim") and _STALE_GATE.search(text):
            flags.append("stale_gate")
            corrections.append(CORRECTIONS["stale_gate"])
            text = directive.get("fallback") or text

    if facts.get("sent") is False and _SENT_CLAIM.search(text):
        flags.append("false_sent")
        corrections.append(CORRECTIONS["false_sent"])
        text = directive.get("fallback") or text

    empathy = directive.get("empathy") or ""
    if empathy and not _EMPATHY.search(text):
        text = f"{empathy} {text}"
        flags.append("empathy_added")
    ask = (directive.get("must_ask") or "").strip()
    if ask and ask not in text and "?" not in text:
        text = f"{text.rstrip()} {ask}"
        flags.append("ask_added")
    return text.strip(), flags, corrections


def needs_regen(flags: list[str]) -> bool:
    """True when a hard SOP rule fired and the model should try once more."""
    return any(flag not in SOFT for flag in flags)


def contains_secret(text: str, slots: dict[str, str | None]) -> bool:
    """True when a draft repeats a raw identity value the caller already gave."""
    for key in ("dob", "email", "id_last4", "phone"):
        value = slots.get(key)
        if value and value in text:
            return True
        if key == "phone" and value:
            digits = re.sub(r"\D", "", value)
            if len(digits) >= 10 and digits[-10:] in re.sub(r"\D", "", text):
                return True
    return False


def _leak_hits(reply: str, terms: list[str]) -> list[str]:
    lowered = reply.lower()
    hits = [term for term in terms if len(term) >= 4 and term.lower() in lowered]
    if _CASE_ID.search(reply):
        hits.append("case-id")
    if _MONEY.search(reply):
        hits.append("amount")
    return hits


def _false_verified(reply: str) -> bool:
    for pattern in _FALSE_STATUS:
        for match in pattern.finditer(reply):
            start = match.start()
            clause_start = max(0, start - 60)
            earlier = reply[clause_start:start]
            break_at = None
            for found in _CLAUSE_BREAK.finditer(earlier):
                break_at = found.end()
            window = earlier[break_at:] if break_at is not None else earlier
            if _NEGATION.search(window):
                continue
            return True
    return False


def _wrong_count(reply: str, needed: Any) -> bool:
    if needed is None:
        return False
    try:
        expected = int(needed)
    except (TypeError, ValueError):
        return False
    match = _COUNT.search(reply)
    if not match:
        return False
    raw = match.group(1).lower()
    stated = _WORD_COUNTS.get(raw)
    if stated is None:
        try:
            stated = int(raw)
        except ValueError:
            return False
    return stated != expected


def _invented(reply: str, allowed: str) -> list[str]:
    """Case ids and amounts in the reply must already appear in the directive facts."""
    allowed_lower = allowed.lower()
    hits: list[str] = []
    for case_id in _CASE_ID.findall(reply):
        if case_id.lower() not in allowed_lower:
            hits.append(case_id)
    for amount in _MONEY.findall(reply):
        token = amount.replace("$", "").replace(" ", "").replace(",", "")
        if token not in allowed and amount.replace(" ", "") not in allowed:
            hits.append(amount)
    return hits
