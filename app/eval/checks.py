"""Reusable PASS/FAIL checks for offline tests and the live demo runner."""

from __future__ import annotations

import re
from typing import Any, Callable

Check = Callable[[dict[str, Any], str], tuple[str, bool, str]]


def _get(state: dict[str, Any], path: str) -> Any:
    value: Any = state
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _snippet(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def state_eq(path: str, expected: Any) -> Check:
    def check(state: dict[str, Any], _reply: str) -> tuple[str, bool, str]:
        actual = _get(state, path)
        return f"state.{path} == {expected!r}", actual == expected, f"got {actual!r}"

    return check


def state_in(path: str, options: list[Any]) -> Check:
    def check(state: dict[str, Any], _reply: str) -> tuple[str, bool, str]:
        actual = _get(state, path)
        return f"state.{path} in {options!r}", actual in options, f"got {actual!r}"

    return check


def state_contains(path: str, needle: str) -> Check:
    def check(state: dict[str, Any], _reply: str) -> tuple[str, bool, str]:
        actual = _get(state, path)
        haystacks = actual if isinstance(actual, list) else [actual]
        ok = any(isinstance(h, str) and needle.lower() in h.lower() for h in haystacks)
        return f"state.{path} contains {needle!r}", ok, _snippet(str(actual))

    return check


def outbox_len(expected: int) -> Check:
    def check(state: dict[str, Any], _reply: str) -> tuple[str, bool, str]:
        actual = len(state.get("outbox") or [])
        return f"len(state.outbox) == {expected}", actual == expected, f"got {actual}"

    return check


def outbox_mentions(index: int, needle: str) -> Check:
    def check(state: dict[str, Any], _reply: str) -> tuple[str, bool, str]:
        outbox = state.get("outbox") or []
        name = f"outbox[{index}] contains {needle!r}"
        if index >= len(outbox):
            return name, False, "no such outbox entry"
        entry = outbox[index]
        text = f"{entry.get('subject', '')} {entry.get('body', '')}"
        return name, needle.lower() in text.lower(), _snippet(str(entry.get("body", "")))

    return check


def reply_any(needles: list[str]) -> Check:
    def check(_state: dict[str, Any], reply: str) -> tuple[str, bool, str]:
        lowered = reply.lower()
        found = [n for n in needles if n.lower() in lowered]
        return f"reply mentions any of {needles!r}", bool(found), f"found {found!r}"

    return check


def reply_none(needles: list[str]) -> Check:
    def check(_state: dict[str, Any], reply: str) -> tuple[str, bool, str]:
        lowered = reply.lower()
        leaked = [n for n in needles if n.lower() in lowered]
        return f"reply contains none of {needles!r}", not leaked, f"leaked {leaked!r}" if leaked else "clean"

    return check


def reply_not_regex(pattern: str, label: str) -> Check:
    compiled = re.compile(pattern, re.IGNORECASE)

    def check(_state: dict[str, Any], reply: str) -> tuple[str, bool, str]:
        match = compiled.search(reply)
        return f"reply {label}", match is None, f"found {match.group(0)!r}" if match else "clean"

    return check


NO_REDACTION_TOKEN = reply_not_regex(r"\[(EMAIL|DATE|PHONE|ID4)\]", "contains no redaction token")
