"""Replace identity values in stored text. The extractor is the only reader of the raw turn."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}")
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_US = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_NAMED = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.I,
)


def redact_text(text: str, secrets: dict[str, str | None] | None = None) -> str:
    """Mask emails, phones, full dates, and a known last-4. Names and case ids stay."""
    cleaned = _EMAIL.sub("[EMAIL]", text)
    cleaned = _PHONE.sub("[PHONE]", cleaned)
    cleaned = _ISO.sub("[DATE]", cleaned)
    cleaned = _US.sub("[DATE]", cleaned)
    cleaned = _NAMED.sub("[DATE]", cleaned)
    last4 = (secrets or {}).get("id_last4")
    if last4 and re.fullmatch(r"\d{4}", last4):
        cleaned = re.sub(rf"\b{re.escape(last4)}\b", "[ID4]", cleaned)
    return cleaned


def mask_mapping(payload: dict) -> dict:
    """Copy a debug extraction with identity values removed and keys preserved."""
    hidden = {"dob", "phone", "email", "id_last4"}
    masked: dict = {}
    for key, value in payload.items():
        if key in hidden and value:
            masked[key] = "[redacted]"
        elif isinstance(value, dict):
            masked[key] = mask_mapping(value)
        else:
            masked[key] = value
    return masked
