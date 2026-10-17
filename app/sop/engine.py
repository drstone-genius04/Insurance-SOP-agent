"""Per-turn SOP loop.

Python owns phase order, the identity gate, claim selection, consent, scope, and escalation.
The model may only fill a structured extraction and phrase the directive. With no key
configured, the turn is blocked with a fixed notice and the call state does not change.
When a key is present, a failed or empty model draft falls back to the phase template.
"""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from typing import Any

from app.config import Settings
from app.llm.base import LLMClient, LLMError
from app.sop import phrases
from app.sop.directives import FREEDOM, NEGATIVE, ask, ensure_offer, handoff, handoff_directive
from app.sop.firewall import seal
from app.sop.guard import needs_regen, review
from app.sop.parse import Parsed, parse_message
from app.sop.phases import run_phases
from app.sop.prompts import EXTRACT_SYSTEM, REPLY_SYSTEM
from app.sop.redact import mask_mapping, redact_text
from app.store import SessionStore
from app.tools.records import Records

FACTORS = phrases.FACTORS
_ALLOWED_SCOPE = {"in_scope_claim", "in_scope_general", "out_of_scope", "small_talk"}
_ALLOWED_EMOTION = {"neutral", "frustrated", "angry", "anxious", "confused", "refusing"}
_ALLOWED_PATH = {
    "denial_question",
    "status_inquiry",
    "document_submission",
    "next_steps",
    "appeal",
    "general_claim_question",
}


class Harness:
    """One insurance call. Sessions persist in SQLite."""

    def __init__(self, settings: Settings, records: Records, llm: LLMClient, store: SessionStore) -> None:
        self.settings = settings
        self.records = records
        self.llm = llm
        self.store = store

    def start(self, consent_scenario: str = "default") -> dict[str, Any]:
        """Open a call. The greeting is a template, not a model call."""
        if consent_scenario not in self.records.consent:
            known = ", ".join(sorted(self.records.consent))
            raise ValueError(f"consent_scenario must be one of: {known}")
        session = _blank(consent_scenario)
        reply = phrases.greeting()
        session["transcript"].append({"role": "agent", "text": reply})
        self.store.save(session)
        return {"session_id": session["id"], "reply": reply, "state": public_state(session), "debug": None}

    def message(
        self,
        session_id: str,
        text: str,
        llm: LLMClient | None = None,
        configured: bool | None = None,
    ) -> dict[str, Any]:
        """Take one caller turn. Return the reply, the public state, and an optional trace.

        `llm` and `configured` let one request use a visitor's key without changing the process client.
        """
        current = self.store.get(session_id)
        if current is None:
            raise KeyError(session_id)
        result, committed = self._turn(current, text, llm or self.llm, self.settings.llm_configured if configured is None else configured)
        if committed is not None:
            self.store.save(committed)
        return result

    def snapshot(self, session_id: str) -> dict[str, Any] | None:
        session = self.store.get(session_id)
        if session is None:
            return None
        return {
            "session_id": session["id"],
            "state": public_state(session),
            "transcript": list(session["transcript"]),
        }

    def _turn(
        self,
        session: dict[str, Any],
        text: str,
        llm: LLMClient | None = None,
        configured: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        use_model = self.settings.llm_configured if configured is None else configured
        if session["phase"] == "HUMAN_HANDOFF":
            redacted = redact_text(text, session["slots"])
            session["transcript"].append({"role": "user", "text": redacted})
            reply = phrases.queue_reply()
            session["transcript"].append({"role": "agent", "text": reply})
            return _result(session, reply, ["Already transferred."], None, self.settings), session

        # Match the reference harness: no key means a fixed notice and an unchanged call.
        if not use_model:
            redacted = redact_text(text, session["slots"])
            reply = phrases.not_configured_reply()
            return _result(session, reply, ["no LLM configured"], None, self.settings), None

        draft = deepcopy(session)
        draft["turn_flags"] = {"just_verified": False, "just_approved": False}
        try:
            model_extract = self._extract(draft, text, llm or self.llm, use_model)
        except LLMError as exc:
            if exc.config:
                reply = phrases.config_notice(exc.status or 0)
                return _result(session, reply, [f"model config HTTP {exc.status}"], None, self.settings), None
            reply = phrases.transport_notice(ask(session))
            return _result(session, reply, ["extractor unavailable"], None, self.settings), None

        parsed = parse_message(text, draft["phase"])
        _merge_model(parsed, model_extract, draft["phase"], text)
        redacted = redact_text(text, {**draft["slots"], **parsed.identity_values()})
        draft["transcript"].append({"role": "user", "text": redacted})
        draft["last_emotion"] = parsed.emotion
        _remember(draft, parsed)

        if parsed.wants_human:
            handoff(draft, "caller asked for a person")
            directive = handoff_directive(draft)
            decisions = ["Caller asked for a person."]
        else:
            off_topic = _apply_scope(draft, parsed, self.settings)
            if draft["phase"] == "HUMAN_HANDOFF":
                directive = handoff_directive(draft)
                decisions = ["Off-topic streak reached the transfer limit."]
            else:
                before_phase = draft["phase"]
                before_matched = sum(draft["matched"].values())
                directive, decisions = run_phases(
                    draft, parsed, text, off_topic, self.records, self.settings
                )
                _apply_emotion(
                    draft,
                    parsed,
                    before_phase,
                    before_matched,
                    off_topic,
                    self.settings,
                )
                if draft["phase"] == "HUMAN_HANDOFF" and before_phase != "HUMAN_HANDOFF":
                    directive = handoff_directive(draft)
                    decisions.append("Frustration streak reached the transfer limit.")
                elif draft["frustration_streak"] >= 2 and draft["phase"] == "VERIFY_ID":
                    directive["fallback"] = ensure_offer(directive["fallback"])
                    decisions.append("Offered a person after repeated frustration.")
        _log_topic(draft)
        reply, flags = self._phrase(draft, directive, redacted, llm or self.llm, use_model)
        if reply is None:
            notice = phrases.config_notice(0)
            return _result(session, notice, decisions + ["model config during phrasing"], None, self.settings), None
        draft["transcript"].append({"role": "agent", "text": reply})
        debug = _debug(draft, parsed, model_extract, decisions, directive, flags)
        return _result(draft, reply, decisions, debug, self.settings), draft

    def _extract(self, session: dict[str, Any], text: str, llm: LLMClient, configured: bool) -> dict[str, Any]:
        history = _history(session["transcript"], 6)
        user = (
            f"Phase: {session['phase']}\n"
            f"Known factor types: {', '.join(name for name in FACTORS if session['matched'].get(name)) or 'none'}\n"
            f"History:\n{history}\n\nCaller message:\n{text}"
        )
        return llm.complete_json(EXTRACT_SYSTEM, user, temperature=0.0)

    def _phrase(
        self,
        session: dict[str, Any],
        directive: dict[str, Any],
        redacted: str,
        llm: LLMClient,
        configured: bool,
    ) -> tuple[str | None, list[str]]:
        """Return None when the model host rejects the key, so the turn can be discarded."""
        drafted = ""
        temperature = 0.2 if directive.get("freedom") == "strict" else 0.5
        history = _history(session["transcript"][:-1], 8)
        sealed = seal(directive, bool(session["verified"]))
        if sealed:
            session.setdefault("turn_flags", {})["sealed"] = sealed
        visible = {
            "phase": directive["phase"],
            "freedom": directive["freedom"],
            "goal": directive["goal"],
            "facts": directive["facts"],
            "must_ask": directive["must_ask"],
            "instructions": directive["instructions"],
            "forbidden": directive["forbidden"],
            "tone": directive["tone"],
            "empathy": directive["empathy"],
        }
        base_user = (
            f"Directive:\n{json.dumps(visible, indent=2)}\n\n"
            f"History:\n{history}\n\nCaller just said:\n{redacted}"
        )
        if configured and session["phase"] != "HUMAN_HANDOFF":
            try:
                drafted = llm.complete_text(REPLY_SYSTEM, base_user, temperature=temperature)
            except LLMError as exc:
                if exc.config:
                    return None, ["phrasing_config"]
                drafted = ""
        reply, flags, corrections = review(drafted, directive, bool(session["verified"]))
        # One regeneration when a hard SOP rule fired, matching the finished harness tripwire.
        if (
            needs_regen(flags)
            and corrections
            and configured
            and session["phase"] != "HUMAN_HANDOFF"
            and drafted
        ):
            try:
                fix = "CRITICAL CORRECTION:\n- " + "\n- ".join(corrections)
                second = llm.complete_text(
                    REPLY_SYSTEM,
                    f"{base_user}\n\n{fix}\n\nRewrite the reply.",
                    temperature=0.1,
                )
                reply, flags, _ = review(second, directive, bool(session["verified"]))
                flags = [*flags, "regenerated"]
            except LLMError as exc:
                if exc.config:
                    return None, ["phrasing_config"]
                flags = [*flags, "regen_failed"]
        return reply, flags


def _blank(scenario: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:16],
        "phase": "VERIFY_ID",
        "slots": {name: None for name in (*FACTORS, "policy_number")},
        "matched": {name: False for name in FACTORS},
        "party_id": None,
        "verified": False,
        "account_located": False,
        "policyholder_name": None,
        "memory": {
            "intent_hints": [],
            "case_hints": {"case_type": None, "status": None, "month": None, "year": None, "case_id": None},
            "notes": [],
            "email_preference": None,
            "saved_before_verification": False,
        },
        "active_case_id": None,
        "intent_path": None,
        "verify_attempts": 0,
        "off_topic_streak": 0,
        "frustration_streak": 0,
        "email_unclear": 0,
        "last_emotion": "neutral",
        "transcript": [],
        "outbox": [],
        "handoff_note": None,
        "handoff_reason": None,
        "caller_role": "policyholder",
        "representative_name": None,
        "relationship": None,
        "on_behalf_of": None,
        "consent_status": None,
        "consent_checks": 0,
        "consent_scenario": scenario,
        "turn_flags": {"just_verified": False, "just_approved": False},
    }


def _merge_model(parsed: Parsed, model: dict[str, Any], phase: str, text: str = "") -> None:
    """Let the model fill gaps. Never let it erase a value the parser already found."""
    if not model:
        return
    for field in ("name", "dob", "phone", "email", "id_last4", "policy_number"):
        if getattr(parsed, field) or not model.get(field):
            continue
        value = str(model[field]).strip()
        if not _said_this_turn(field, value, text):
            continue
        setattr(parsed, field, value)
    if phase in {"POST_PROCESS", "CLOSED"}:
        # Wrap-up turns are short; the extractor tends to re-read intent from history.
        return
    hints = model.get("case_hints") if isinstance(model.get("case_hints"), dict) else {}
    if not parsed.case_type and hints.get("case_type"):
        parsed.case_type = str(hints["case_type"]).lower()
    if not parsed.status and hints.get("status"):
        parsed.status = str(hints["status"]).lower()
    if not parsed.month and hints.get("month"):
        try:
            parsed.month = int(hints["month"])
        except (TypeError, ValueError):
            parsed.month = None
    if not parsed.year and hints.get("year"):
        try:
            parsed.year = int(hints["year"])
        except (TypeError, ValueError):
            parsed.year = None
    if not parsed.case_id and hints.get("case_id"):
        parsed.case_id = str(hints["case_id"]).upper()
    for hint in model.get("intent_hints") or []:
        if isinstance(hint, str) and hint and hint not in parsed.intent_hints:
            parsed.intent_hints.append(hint)
    path = str(model.get("intent_path") or "")
    if not parsed.intent_path and path in _ALLOWED_PATH and _path_allowed(path, text):
        parsed.intent_path = path
    emotion = str(model.get("emotion") or "")
    if parsed.emotion == "neutral" and emotion in _ALLOWED_EMOTION and phase not in {"POST_PROCESS", "CLOSED"}:
        parsed.emotion = emotion
    scope = str(model.get("scope") or "")
    if not parsed.scope_locked and scope in _ALLOWED_SCOPE and phase not in {"POST_PROCESS", "PROCESS_CASE"}:
        parsed.scope = scope
        parsed.scope_locked = True
    if parsed.caller_role != "policyholder" and model.get("caller_role") == "representative":
        if model.get("on_behalf_of_name") or parsed.on_behalf_of_name:
            parsed.caller_role = "representative"
            parsed.representative_name = parsed.representative_name or _clean(model.get("representative_name"))
            parsed.on_behalf_of_name = parsed.on_behalf_of_name or _clean(model.get("on_behalf_of_name"))
            parsed.relationship = parsed.relationship or _clean(model.get("relationship"))


def _path_allowed(path: str, text: str) -> bool:
    """The extractor must not widen a turn onto dollar amounts the caller did not ask about.

    Status inquiries are the only path that includes payment figures. A model that labels
    every turn ``status_inquiry`` would pull those figures into the prompt.
    """
    if path != "status_inquiry":
        return True
    return bool(re.search(r"\b(status|paid|payment|amount|reimburse|how much|balance)\b", text, re.I))


def _said_this_turn(field: str, value: str, text: str) -> bool:
    """The extractor sees history, so an identity value only counts if the caller typed it now."""
    if "*" in value:
        return False
    lowered = text.lower()
    digits = re.sub(r"\D", "", text)
    if field == "email":
        return value.lower() in lowered
    if field in {"phone", "id_last4"}:
        tail = re.sub(r"\D", "", value)[-4:]
        return bool(tail) and tail in digits
    if field == "dob":
        year = re.sub(r"\D", "", value)[:4]
        return bool(year) and year in digits
    if field == "name":
        return value.split()[-1].lower() in lowered if value.split() else False
    return re.sub(r"\W", "", value).lower() in re.sub(r"\W", "", lowered)


def _remember(session: dict[str, Any], parsed: Parsed) -> None:
    memory = session["memory"]
    for hint in parsed.intent_hints:
        if hint not in memory["intent_hints"]:
            memory["intent_hints"].append(hint)
    for key in ("case_type", "status", "month", "year", "case_id"):
        value = getattr(parsed, key)
        if value:
            memory["case_hints"][key] = value
    if any(memory["case_hints"].values()) and not session["verified"]:
        memory["saved_before_verification"] = True
    if parsed.intent_path:
        session["intent_path"] = parsed.intent_path
    if parsed.email_preference:
        memory["email_preference"] = parsed.email_preference
        note = (
            "Caller asked for an emailed summary."
            if parsed.email_preference == "yes"
            else "Caller asked not to be emailed."
        )
        memory["notes"] = [item for item in memory["notes"] if "email" not in item.lower()]
        memory["notes"].append(note)
    _apply_role(session, parsed)
    if session["caller_role"] == "representative":
        return
    for key, value in parsed.identity_values().items():
        if value:
            session["slots"][key] = value


def _apply_role(session: dict[str, Any], parsed: Parsed) -> None:
    if parsed.caller_role == "policyholder":
        session["caller_role"] = "policyholder"
        session["representative_name"] = None
        session["relationship"] = None
        session["on_behalf_of"] = None
        session["consent_status"] = None
        session["consent_checks"] = 0
        return
    if parsed.caller_role == "representative" or session["caller_role"] == "representative":
        if parsed.caller_role == "representative":
            session["caller_role"] = "representative"
        if parsed.representative_name:
            session["representative_name"] = parsed.representative_name
        if parsed.on_behalf_of_name:
            session["on_behalf_of"] = parsed.on_behalf_of_name
        if parsed.relationship:
            session["relationship"] = parsed.relationship


def _apply_scope(session: dict[str, Any], parsed: Parsed, settings: Settings) -> bool:
    if parsed.scope == "out_of_scope":
        session["off_topic_streak"] += 1
        if session["off_topic_streak"] >= settings.max_off_topic:
            handoff(session, "repeated questions outside claims support")
        return True
    if parsed.scope == "in_scope_claim":
        session["off_topic_streak"] = 0
    return False


def _apply_emotion(
    session: dict[str, Any],
    parsed: Parsed,
    before_phase: str,
    before_matched: int,
    off_topic: bool,
    settings: Settings,
) -> None:
    phase_changed = session["phase"] != before_phase
    matched_now = sum(session["matched"].values())
    substantive = bool(parsed.intent_path or parsed.case_type or parsed.case_id)
    progress = phase_changed or matched_now > before_matched
    if session["phase"] == "PROCESS_CASE" and not off_topic and substantive:
        progress = True
    if progress:
        session["frustration_streak"] = 0
    elif parsed.emotion in NEGATIVE:
        session["frustration_streak"] += 1
    if session["frustration_streak"] >= settings.max_frustration_turns and session["phase"] != "HUMAN_HANDOFF":
        handoff(session, "frustration without progress")


def _log_topic(session: dict[str, Any]) -> None:
    if not session["verified"]:
        return
    path = session.get("intent_path")
    if path:
        note = f"Asked about {path.replace('_', ' ')}."
        if note not in session["memory"]["notes"]:
            session["memory"]["notes"].append(note)
    case_id = session.get("active_case_id")
    if case_id:
        note = f"Opened {case_id}."
        if note not in session["memory"]["notes"]:
            session["memory"]["notes"].append(note)


def _history(transcript: list[dict[str, str]], limit: int) -> str:
    window = transcript[-limit:]
    if not window:
        return "(none)"
    return "\n".join(f"{turn['role'].upper()}: {turn['text']}" for turn in window)


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _debug(
    session: dict[str, Any],
    parsed: Parsed,
    model: dict[str, Any],
    decisions: list[str],
    directive: dict[str, Any],
    flags: list[str],
) -> dict[str, Any]:
    extraction = mask_mapping(
        {
            "name": parsed.name,
            "dob": parsed.dob,
            "phone": parsed.phone,
            "email": parsed.email,
            "id_last4": parsed.id_last4,
            "policy_number": parsed.policy_number,
            "scope": parsed.scope,
            "emotion": parsed.emotion,
            "intent_path": parsed.intent_path,
            "model_used": bool(model),
        }
    )
    return {
        "decisions": decisions,
        "extraction": extraction,
        "guard": flags,
        "directive": {
            "phase": directive.get("phase"),
            "freedom": directive.get("freedom"),
            "goal": directive.get("goal"),
            "must_ask": directive.get("must_ask"),
            "fact_keys": sorted((directive.get("facts") or {}).keys()),
        },
    }


def _result(
    session: dict[str, Any],
    reply: str,
    decisions: list[str],
    debug: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any]:
    return {
        "reply": reply,
        "state": public_state(session),
        "debug": debug if settings.trace_enabled else None,
        "decisions": decisions,
    }


def public_state(session: dict[str, Any]) -> dict[str, Any]:
    """Caller-visible state. Raw identity values are never included."""
    memory = session["memory"]
    hints = {key: value for key, value in memory["case_hints"].items() if value}
    matched = sum(1 for value in session["matched"].values() if value)
    state = {
        "phase": session["phase"],
        "freedom": FREEDOM.get(session["phase"], "strict"),
        "verified": session["verified"],
        "factors": dict(session["matched"]),
        "factors_matched": matched,
        "factors_still_needed": max(0, phrases.NEEDED - matched),
        "account_located": session["account_located"],
        "policyholder_name": session["policyholder_name"] if session["verified"] else None,
        "memory": {
            "intent_hints": list(memory["intent_hints"]),
            "case_hints": hints,
            "notes": list(memory["notes"]),
            "email_preference": memory["email_preference"],
            "saved_before_verification": memory["saved_before_verification"],
        },
        "active_case_id": session["active_case_id"] if session["verified"] else None,
        "intent_path": session["intent_path"],
        "verify_attempts": session["verify_attempts"],
        "off_topic_streak": session["off_topic_streak"],
        "frustration_streak": session["frustration_streak"],
        "last_emotion": session["last_emotion"],
        "outbox": [
            {"to_masked": item["to_masked"], "subject": item["subject"], "body": item["body"]}
            for item in session["outbox"]
        ],
        "handoff_note": session["handoff_note"],
        "caller_role": session["caller_role"],
        "representative_name": session["representative_name"],
        "representative_relationship": session["relationship"],
        "consent_status": session["consent_status"],
    }
    return state
