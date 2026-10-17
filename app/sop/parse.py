"""Deterministic reading of a caller turn.

The model may add what this misses. It cannot override a value this parser found,
and it cannot verify anyone. Identity comparison happens later, against the fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.tools.records import MONTHS, normalize_date, normalize_email, normalize_last4, normalize_phone, normalize_policy

_NAME = r"([A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,3})"
# The prefix is case-insensitive. The captured name stays case-sensitive so "I'm the policyholder" is not a name.
_NAME_RE = re.compile(rf"(?i:my name is|this is|i am|i'm)\s+{_NAME}")
_BEHALF_RE = re.compile(rf"(?i:\b(?:on behalf of|calling for))\s+{_NAME}")
_RELATION_RE = re.compile(
    r"\b(?:my|her|his|their)\s+(son|daughter|spouse|mother|father|wife|husband|partner|parent)\b",
    re.I,
)
_POSSESSIVE_RELATION_RE = re.compile(
    rf"{_NAME}'s\s+(son|daughter|spouse|mother|father|wife|husband|partner|parent)\b"
)
_POLICYHOLDER_RE = re.compile(r"\bi(?:'m| am) the policyholder\b", re.I)
_REPRESENTATIVE_RE = re.compile(r"\b(authorized representative|on behalf of|calling for)\b", re.I)
_DOB_LABELED = re.compile(
    r"\b(?:dob|d\.o\.b\.|date of birth|born)\b[^A-Za-z0-9]{0,12}([A-Za-z0-9,/-]{6,32})",
    re.I,
)
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_NAMED_DATE = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})\b",
    re.I,
)
_LAST4 = re.compile(
    r"\b(?:ssn|social(?:\s+security)?|government\s+id|national\s+id|id)?"
    r"[^.\n]{0,20}?\blast\s*(?:four|4)\s*(?:digits?)?\s*(?:is|are|:)?\s*(\d{4})\b",
    re.I,
)
_EMAIL = re.compile(r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}")
_CASE = re.compile(r"\b(CL-\d+)\b", re.I)
_HUMAN = re.compile(
    r"\b(talk to (?:a )?(?:human|person|someone|representative|agent)|speak to (?:a )?(?:human|person|someone)|"
    r"real person|transfer me|human representative)\b",
    re.I,
)
_DONE = re.compile(
    r"\b(that(?:'s| is) all|that(?:'s| is) everything|nothing else|we(?:'re| are) done|"
    r"i(?:'m| am) done|goodbye|no more questions)\b",
    re.I,
)
_QUESTION = re.compile(r"\b(what|why|how|when|where|which|can you|could you)\b|\?", re.I)

_OFF_TOPIC = re.compile(
    r"\b(what is rl|reinforcement learning|weather|recipe|capital of|who won|write (?:me )?code|"
    r"javascript|python tutorial|sports score|tell me a joke)\b",
    re.I,
)
_INSURANCE = re.compile(
    r"\b(claims?|polic(?:y|ies)|coverage|deductible|premium|appeal|denial|denied|benefit|member|"
    r"insurance|documents?|reimburse\w*|pathology|dob|ssn|e-?mail|summary|verification|"
    r"status|paid|payment|amount)\b",
    re.I,
)
_SMALL = re.compile(
    r"^\s*(hi|hello|hey|good (?:morning|afternoon|evening)|thanks|thank you|ok|okay)[.! ]*\s*$",
    re.I,
)
_ANGRY = re.compile(r"\b(ridiculous|insane|furious|unacceptable|angry|outrageous)\b", re.I)
_FRUSTRATED = re.compile(r"\b(frustrat\w*|already told you|sick of|annoying|come on|hurry up)\b", re.I)
_ANXIOUS = re.compile(r"\b(anxious|worried|scared|nervous|afraid)\b", re.I)
_CONFUSED = re.compile(r"\b(confused|don'?t understand|do not understand|what do you mean|i'?m lost)\b", re.I)
_REFUSING = re.compile(
    r"\b(i won'?t|i will not|not giving you|none of your business|refuse to|i refuse)\b",
    re.I,
)


@dataclass
class Parsed:
    """What a single caller message contributes. Empty fields mean 'not said'."""

    name: str | None = None
    dob: str | None = None
    phone: str | None = None
    email: str | None = None
    id_last4: str | None = None
    policy_number: str | None = None
    intent_hints: list[str] = field(default_factory=list)
    intent_path: str | None = None
    case_type: str | None = None
    status: str | None = None
    month: int | None = None
    year: int | None = None
    case_id: str | None = None
    scope: str = "in_scope_claim"
    scope_locked: bool = False
    emotion: str = "neutral"
    wants_human: bool = False
    conversation_done: bool = False
    email_consent: str | None = None
    email_preference: str | None = None
    caller_role: str | None = None
    representative_name: str | None = None
    relationship: str | None = None
    on_behalf_of_name: str | None = None

    def identity_values(self) -> dict[str, str]:
        values = {
            "name": self.name,
            "dob": self.dob,
            "phone": self.phone,
            "email": self.email,
            "id_last4": self.id_last4,
            "policy_number": self.policy_number,
        }
        return {key: value for key, value in values.items() if value}


def clean_text(text: str) -> str:
    """Normalize curly quotes so patterns see the words the caller typed."""
    return (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .strip()
    )


def parse_message(text: str, phase: str) -> Parsed:
    """Read identity, hints, emotion, and scope without calling a model."""
    raw = clean_text(text)
    parsed = Parsed()
    _read_role(raw, parsed)
    _read_identity(raw, parsed)
    _read_case(raw, parsed)
    _read_emotion(raw, parsed)
    _read_scope(raw, parsed)
    _read_controls(raw, parsed, phase)
    return parsed


def _read_role(text: str, parsed: Parsed) -> None:
    if _POLICYHOLDER_RE.search(text) and not _BEHALF_RE.search(text):
        parsed.caller_role = "policyholder"
        return
    behalf = _BEHALF_RE.search(text)
    relation = _RELATION_RE.search(text)
    possessive = _POSSESSIVE_RELATION_RE.search(text)
    if behalf or relation or possessive or _REPRESENTATIVE_RE.search(text):
        parsed.caller_role = "representative"
        if behalf:
            parsed.on_behalf_of_name = behalf.group(1)
        if possessive:
            parsed.on_behalf_of_name = parsed.on_behalf_of_name or possessive.group(1)
            parsed.relationship = possessive.group(2).lower()
        if relation:
            parsed.relationship = relation.group(1).lower()
        named = _NAME_RE.search(text)
        if named and fold_differs(named.group(1), parsed.on_behalf_of_name):
            parsed.representative_name = named.group(1)


def fold_differs(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    return left.lower() != right.lower()


def _read_identity(text: str, parsed: Parsed) -> None:
    parsed.policy_number = normalize_policy(text)
    parsed.email = normalize_email(_EMAIL.search(text).group(1) if _EMAIL.search(text) else None)
    phone = _PHONE.search(text)
    parsed.phone = normalize_phone(phone.group(0) if phone else None)
    last4 = _LAST4.search(text)
    parsed.id_last4 = normalize_last4(last4.group(1) if last4 else None)
    parsed.dob = _find_dob(text)
    if parsed.caller_role == "representative":
        return
    named = _NAME_RE.search(text)
    if named and named.group(1).lower() not in {"the policyholder"}:
        parsed.name = named.group(1)


def _find_dob(text: str) -> str | None:
    labeled = _DOB_LABELED.search(text)
    if labeled:
        normalized = normalize_date(labeled.group(1).strip(" .,"))
        if normalized:
            return normalized
    for pattern in (_ISO, _US_DATE, _NAMED_DATE):
        match = pattern.search(text)
        if match:
            normalized = normalize_date(match.group(1))
            if normalized:
                return normalized
    return None


def _read_case(text: str, parsed: Parsed) -> None:
    lowered = text.lower()
    if re.search(r"\b(healthcare|medical|health)\b", lowered):
        parsed.case_type = "healthcare"
    elif re.search(r"\bdental\b", lowered):
        parsed.case_type = "dental"
    elif re.search(r"\b(auto|car|vehicle)\b", lowered):
        parsed.case_type = "auto"
    if re.search(r"\b(denied|denial)\b", lowered):
        parsed.status = "denied"
    elif re.search(r"\b(settled|completed|closed)\b", lowered):
        parsed.status = "closed"
    elif re.search(r"\b(in progress|open claim|still open)\b", lowered):
        parsed.status = "open"
    for name, number in MONTHS.items():
        if re.search(rf"\b{name}\b", lowered):
            parsed.month = number
            break
    for item in re.findall(r"\b(20\d{2})\b", text):
        if parsed.dob and item in parsed.dob:
            continue
        parsed.year = int(item)
        break
    case = _CASE.search(text)
    if case:
        parsed.case_id = case.group(1).upper()
    if re.search(r"\bappeal\b", lowered):
        parsed.intent_path = "appeal"
    elif re.search(r"\b(documents?|submit|upload|paperwork)\b", lowered):
        parsed.intent_path = "document_submission"
    elif parsed.status == "denied":
        parsed.intent_path = "denial_question"
    elif re.search(r"\b(status|where is|update on)\b", lowered):
        parsed.intent_path = "status_inquiry"
    elif re.search(r"\b(next step|what do i need|what should i)\b", lowered):
        parsed.intent_path = "next_steps"
    elif parsed.case_type or parsed.case_id:
        parsed.intent_path = "general_claim_question"
    hint = _hint_phrase(parsed)
    if hint:
        parsed.intent_hints.append(hint)


def _hint_phrase(parsed: Parsed) -> str:
    bits = [bit for bit in (parsed.status, parsed.case_type) if bit]
    if parsed.month:
        month = next(name for name, number in MONTHS.items() if number == parsed.month)
        when = month.title()
        if parsed.year:
            when = f"{when} {parsed.year}"
        bits.append(f"from {when}")
    if parsed.case_id:
        bits.append(parsed.case_id)
    if not bits:
        return ""
    return " ".join(bits)


def _read_emotion(text: str, parsed: Parsed) -> None:
    if _ANGRY.search(text):
        parsed.emotion = "angry"
    elif _REFUSING.search(text):
        parsed.emotion = "refusing"
    elif _FRUSTRATED.search(text):
        parsed.emotion = "frustrated"
    elif _ANXIOUS.search(text):
        parsed.emotion = "anxious"
    elif _CONFUSED.search(text):
        parsed.emotion = "confused"


def _read_scope(text: str, parsed: Parsed) -> None:
    if parsed.identity_values() or _INSURANCE.search(text) or parsed.case_type or parsed.case_id:
        parsed.scope = "in_scope_claim"
        parsed.scope_locked = True
        return
    if _SMALL.search(text):
        parsed.scope = "small_talk"
        parsed.scope_locked = True
        return
    if _OFF_TOPIC.search(text) or _QUESTION.search(text):
        parsed.scope = "out_of_scope"
        parsed.scope_locked = True
        return
    parsed.scope = "in_scope_claim"
    parsed.scope_locked = False


def _read_controls(text: str, parsed: Parsed, phase: str) -> None:
    parsed.wants_human = bool(_HUMAN.search(text))
    parsed.conversation_done = _is_wrap_up(text)
    yes = bool(re.search(r"\b(yes|please send|send it|go ahead|sure)\b", text, re.I))
    no = bool(re.search(r"\b(no|skip|don'?t|do not)\b", text, re.I))
    if phase == "POST_PROCESS":
        if yes and not no:
            parsed.email_consent = "yes"
        elif no and not yes:
            parsed.email_consent = "no"
        else:
            parsed.email_consent = "unclear"
        return
    if re.search(r"\b(no email|don'?t email|do not email|skip the email)\b", text, re.I):
        parsed.email_preference = "no"
    elif re.search(
        r"\b(email me|send (?:me )?(?:an )?email|send (?:me )?a summary|emailed summary)\b",
        text,
        re.I,
    ):
        parsed.email_preference = "yes"
        if parsed.conversation_done:
            parsed.email_consent = "yes"


def _is_wrap_up(text: str) -> bool:
    """True when the caller is ending the call, not when they mention a future ending."""
    if re.search(r"\bwhen we(?:'re| are) done\b", text, re.I):
        stripped = re.sub(r"\bwhen we(?:'re| are) done\b", " ", text, flags=re.I)
    else:
        stripped = text
    if not _DONE.search(stripped):
        return False
    remainder = _DONE.sub(" ", stripped)
    return not bool(_QUESTION.search(remainder))
