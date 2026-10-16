"""Check a drafted reply against the directive before the caller sees it."""

from __future__ import annotations

import re
from typing import Any

_FALSE_STATUS = re.compile(
    r"\b(you(?:'re| are) verified|i(?:'ve| have) verified you|your identity is confirmed|"
    r"i have your file open|i(?:'m| am) looking at your (?:account|claim|file))\b",
    re.I,
)
_NEGATION = re.compile(r"\b(not|until|once|before|after|haven'?t|have not|can'?t|cannot|without)\b", re.I)
_CASE_ID = re.compile(r"\bCL-\d+\b", re.I)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d{2,5}\.\d{2}\b")
_EMPATHY = re.compile(r"\b(sorry|understand|frustrat|hear|worry|fair|uncomfortable|with you)\b", re.I)


def review(reply: str, directive: dict[str, Any], verified: bool) -> tuple[str, list[str]]:
    """Return a safe reply and the guard flags that fired."""
    flags: list[str] = []
    text = reply.strip() or directive.get("fallback") or ""
    if not verified:
        leaked = _leak_hits(text, directive.get("leak_terms") or [])
        if leaked or _false_verified(text):
            flags.append("claim_or_status_blocked")
            text = directive.get("fallback") or text
    else:
        invented = _invented(text, directive.get("allowed_text") or "")
        if invented:
            flags.append("ungrounded")
            text = directive.get("fallback") or text
    empathy = directive.get("empathy") or ""
    if empathy and not _EMPATHY.search(text):
        text = f"{empathy} {text}"
        flags.append("empathy_added")
    ask = (directive.get("must_ask") or "").strip()
    if ask and ask not in text and "?" not in text:
        text = f"{text.rstrip()} {ask}"
        flags.append("ask_added")
    return text.strip(), flags


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
    hits = [term for term in terms if term.lower() in lowered]
    if _CASE_ID.search(reply):
        hits.append("case-id")
    return hits


def _false_verified(reply: str) -> bool:
    for match in _FALSE_STATUS.finditer(reply):
        window = reply[max(0, match.start() - 48) : match.end()]
        if _NEGATION.search(window):
            continue
        return True
    return False


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
