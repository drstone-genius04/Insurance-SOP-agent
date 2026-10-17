"""Per-turn SOP loop.

Python owns phase order, the identity gate, claim selection, consent, scope, and escalation.
The model may only fill a structured extraction and phrase the directive. A failed or empty
model call still produces the templated reply, so the procedure does not depend on it.
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
from app.sop.guard import contains_secret, needs_regen, review
from app.sop.parse import Parsed, parse_message
from app.sop.prompts import EXTRACT_SYSTEM, REPLY_SYSTEM
from app.sop.redact import mask_mapping, redact_text
from app.store import SessionStore
from app.tools.records import Records, claim_brief, mask_email

FACTORS = phrases.FACTORS
NEGATIVE = {"angry", "frustrated", "refusing"}
FREEDOM = {
    "VERIFY_ID": "strict",
    "RESOLVE_INTENT": "flexible",
    "PROCESS_CASE": "flexible",
    "POST_PROCESS": "consent",
    "CLOSED": "minimal",
    "HUMAN_HANDOFF": "terminal",
}
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
        if session["phase"] == "HUMAN_HANDOFF":
            redacted = redact_text(text, session["slots"])
            session["transcript"].append({"role": "user", "text": redacted})
            reply = phrases.queue_reply()
            session["transcript"].append({"role": "agent", "text": reply})
            return _result(session, reply, ["Already transferred."], None, self.settings), session

        draft = deepcopy(session)
        draft["turn_flags"] = {"just_verified": False, "just_approved": False}
        try:
            model_extract = self._extract(draft, text, llm or self.llm, self.settings.llm_configured if configured is None else configured)
        except LLMError as exc:
            if exc.config:
                reply = phrases.config_notice(exc.status or 0)
                return _result(session, reply, [f"model config HTTP {exc.status}"], None, self.settings), None
            reply = phrases.transport_notice(_ask(session))
            return _result(session, reply, ["extractor unavailable"], None, self.settings), None

        parsed = parse_message(text, draft["phase"])
        _merge_model(parsed, model_extract, draft["phase"], text)
        redacted = redact_text(text, {**draft["slots"], **parsed.identity_values()})
        draft["transcript"].append({"role": "user", "text": redacted})
        draft["last_emotion"] = parsed.emotion
        _remember(draft, parsed)

        if parsed.wants_human:
            _handoff(draft, "caller asked for a person")
            directive = _handoff_directive(draft)
            decisions = ["Caller asked for a person."]
        else:
            off_topic = _apply_scope(draft, parsed, self.settings)
            if draft["phase"] == "HUMAN_HANDOFF":
                directive = _handoff_directive(draft)
                decisions = ["Off-topic streak reached the transfer limit."]
            else:
                before_phase = draft["phase"]
                before_matched = sum(draft["matched"].values())
                directive, decisions = _run_phases(
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
                    directive = _handoff_directive(draft)
                    decisions.append("Frustration streak reached the transfer limit.")
                elif draft["frustration_streak"] >= 2 and draft["phase"] == "VERIFY_ID":
                    directive["fallback"] = _ensure_offer(directive["fallback"])
                    decisions.append("Offered a person after repeated frustration.")
        _log_topic(draft)
        reply, flags = self._phrase(draft, directive, redacted, llm or self.llm, self.settings.llm_configured if configured is None else configured)
        if reply is None:
            notice = phrases.config_notice(0)
            return _result(session, notice, decisions + ["model config during phrasing"], None, self.settings), None
        draft["transcript"].append({"role": "agent", "text": reply})
        debug = _debug(draft, parsed, model_extract, decisions, directive, flags)
        return _result(draft, reply, decisions, debug, self.settings), draft

    def _extract(self, session: dict[str, Any], text: str, llm: LLMClient, configured: bool) -> dict[str, Any]:
        if not configured:
            return {}
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
    if not parsed.intent_path and path in _ALLOWED_PATH:
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
            _handoff(session, "repeated questions outside claims support")
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
        _handoff(session, "frustration without progress")


def _run_phases(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    if off_topic and session["phase"] != "VERIFY_ID":
        ask = _ask(session)
        fallback = phrases.scope_reply(ask, session["off_topic_streak"] >= 2, parsed.emotion)
        directive = _directive(session, "Decline the off-topic question and return to the step.", {}, ask, fallback, records, parsed)
        return directive, ["Declined an off-topic question."]
    if parsed.scope == "small_talk" and session["phase"] not in {"VERIFY_ID", "HUMAN_HANDOFF"}:
        ask = _ask(session)
        fallback = phrases.small_talk_reply(ask)
        directive = _directive(session, "Answer briefly and return to the step.", {}, ask, fallback, records, parsed)
        return directive, ["Small talk."]
    decisions: list[str] = []
    directive: dict[str, Any] | None = None
    for _ in range(5):
        phase = session["phase"]
        if phase == "VERIFY_ID":
            directive, cont, decision = _verify(session, parsed, message, off_topic, records, settings)
        elif phase == "RESOLVE_INTENT":
            directive, cont, decision = _resolve(session, records)
        elif phase == "PROCESS_CASE":
            directive, cont, decision = _process(session, parsed, message, records)
        elif phase == "POST_PROCESS":
            directive, cont, decision = _post(session, parsed, records)
        elif phase == "CLOSED":
            directive, cont, decision = _closed(session, parsed, message)
        else:
            directive, cont, decision = _handoff_directive(session), False, "Transferred."
        decisions.append(decision)
        if not cont:
            break
    assert directive is not None
    return directive, decisions


def _verify(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], bool, str]:
    if session["caller_role"] == "representative":
        return _representative(session, parsed, off_topic, records, settings)
    if off_topic or parsed.scope == "small_talk":
        ask = _ask(session)
        fallback = phrases.scope_reply(ask, session["off_topic_streak"] >= 2, parsed.emotion) if off_topic else phrases.small_talk_reply(ask)
        return _directive(session, "Stay on identity verification.", {}, ask, fallback, records, parsed), False, "Off-topic during verification." if off_topic else "Small talk during verification."

    record = _locate(session, records)
    mismatch = False
    not_found = False
    if record is None:
        if any(session["slots"].values()):
            not_found = True
            mismatch = True
    else:
        for factor in FACTORS:
            value = session["slots"].get(factor)
            if not value:
                continue
            if records.factor_matches(record, factor, value):
                session["matched"][factor] = True
            else:
                session["matched"][factor] = False
                session["slots"][factor] = None
                mismatch = True
    if mismatch:
        session["verify_attempts"] += 1
        if session["verify_attempts"] > settings.max_verify_attempts:
            _handoff(session, f"identity not verified after {session['verify_attempts']} attempts")
            return _handoff_directive(session), False, "Identity attempts exhausted."
    if sum(session["matched"].values()) >= phrases.NEEDED and record is not None:
        _mark_verified(session, record)
        return _empty_directive(), True, f"Verified {sum(session['matched'].values())} factors."

    offer = session["verify_attempts"] >= settings.max_verify_attempts and mismatch
    remembered = phrases.hint_phrase(session["memory"]["case_hints"])
    fallback = phrases.verify_reply(
        located=record is not None,
        matched=session["matched"],
        mismatch=mismatch and not not_found,
        not_found=not_found,
        offer_human=offer,
        emotion=parsed.emotion,
        remembered=remembered,
        policyholder=record["name"] if record and session["matched"].get("name") else None,
    )
    facts = {
        "verified": False,
        "account_located": record is not None,
        "factors_matched": sum(session["matched"].values()),
        "factors_still_needed": max(0, phrases.NEEDED - sum(session["matched"].values())),
        "acceptable_factors": [phrases.FACTOR_LABELS[name] for name in FACTORS if not session["matched"].get(name)],
        "mismatch_this_turn": mismatch,
        "offer_human": offer,
        "caller_context": remembered,
    }
    ask = "What detail can you share so I can verify the policy?"
    decision = "Identity mismatch." if mismatch else "Waiting for more identity details."
    return _directive(session, "Verify identity before opening any claim.", facts, ask, fallback, records, parsed), False, decision


def _representative(
    session: dict[str, Any],
    parsed: Parsed,
    off_topic: bool,
    records: Records,
    settings: Settings,
) -> tuple[dict[str, Any], bool, str]:
    row = records.lookup_representative(session.get("representative_name"), session.get("on_behalf_of"))
    remembered = phrases.hint_phrase(session["memory"]["case_hints"])
    if row is None:
        if session.get("representative_name") or session.get("on_behalf_of"):
            session["verify_attempts"] += 1
            if session["verify_attempts"] > settings.max_verify_attempts:
                _handoff(session, f"identity not verified after {session['verify_attempts']} attempts")
                return _handoff_directive(session), False, "Representative not found. Attempts exhausted."
        offer = session["verify_attempts"] >= settings.max_verify_attempts
        fallback = phrases.representative_reply(
            found=False,
            status="pending",
            rep_name=session.get("representative_name"),
            buyer_name=session.get("on_behalf_of"),
            offer_human=offer,
            emotion=parsed.emotion,
            remembered=remembered,
        )
        return _directive(session, "Find an authorization before opening a claim.", {"verified": False}, "Who are you calling for?", fallback, records, parsed), False, "Representative not on file."

    session["consent_checks"] += 1
    status = records.consent_status(session["consent_scenario"], session["consent_checks"])
    session["consent_status"] = status
    buyer = records.by_id(row.get("buyer_party_id"))
    buyer_name = buyer["name"] if buyer else row.get("buyer_name")
    if status == "timed_out":
        _handoff(session, "policyholder consent not received")
        return _handoff_directive(session), False, "Consent timed out."
    if status == "approved" and buyer:
        _mark_verified(session, buyer)
        session["turn_flags"]["just_approved"] = True
        session["relationship"] = session.get("relationship") or row.get("relationship")
        return _empty_directive(), True, "Policyholder approved consent."
    offer = off_topic and session["off_topic_streak"] >= 2
    fallback = phrases.representative_reply(
        found=True,
        status="pending",
        rep_name=session.get("representative_name"),
        buyer_name=buyer_name,
        offer_human=offer,
        emotion=parsed.emotion,
        remembered=remembered,
    )
    if off_topic:
        fallback = phrases.scope_reply("I still need the policyholder's approval before I can open the claim.", offer, parsed.emotion)
    facts = {"verified": False, "consent_status": "pending", "caller_context": remembered}
    return _directive(session, "Wait for policyholder consent. Do not verify with their ID.", facts, "I can stay on the line while they respond.", fallback, records, parsed), False, "Consent pending."


def _resolve(session: dict[str, Any], records: Records) -> tuple[dict[str, Any], bool, str]:
    hints = session["memory"]["case_hints"]
    remembered = phrases.hint_phrase(hints)
    hinted = any(hints.values())
    matched = records.filter_claims(session["party_id"], hints) if hinted else records.claims_for(session["party_id"])
    if len(matched) == 1:
        session["active_case_id"] = matched[0]["case_id"]
        session["phase"] = "PROCESS_CASE"
        return _empty_directive(), True, f"Selected {matched[0]['case_id']}."
    pool = matched if matched else records.claims_for(session["party_id"])
    briefs = [claim_brief(row) for row in pool]
    fallback = phrases.list_claims_reply(briefs, remembered, ambiguous=bool(matched) and hinted)
    facts = {"verified": True, "claims": briefs, "caller_context": remembered}
    forbidden = ["denial reasons", "document names", "dollar amounts"]
    directive = _directive(session, "Choose exactly one claim.", facts, "Which claim should I open?", fallback, records, None)
    directive["forbidden"] = forbidden
    return directive, False, "Need the caller to pick a claim."


def _process(
    session: dict[str, Any],
    parsed: Parsed,
    message: str,
    records: Records,
) -> tuple[dict[str, Any], bool, str]:
    if parsed.conversation_done:
        session["phase"] = "POST_PROCESS"
        return _empty_directive(), True, "Caller is wrapping up."
    hints = session["memory"]["case_hints"]
    if any(hints.values()):
        matched = records.filter_claims(session["party_id"], hints)
        if len(matched) == 1 and matched[0]["case_id"] != session["active_case_id"]:
            session["turn_flags"]["switched_from"] = session["active_case_id"]
            session["active_case_id"] = matched[0]["case_id"]
    claim = records.claim(session["active_case_id"])
    if claim is None:
        session["phase"] = "RESOLVE_INTENT"
        return _empty_directive(), True, "No active claim."
    remembered = phrases.hint_phrase(hints) if session["memory"]["saved_before_verification"] else ""
    switched = session["turn_flags"].get("switched_from")
    if parsed.email_preference and not parsed.case_type and not parsed.intent_path:
        fallback = (
            "I've noted that you'd like a summary by email. I'll ask before I send anything. "
            "What else would you like to know about this claim?"
        )
    else:
        fallback = phrases.claim_reply(claim, message, remembered, switched, session.get("intent_path"))
    if session["turn_flags"].get("just_approved"):
        fallback = f"{session['policyholder_name']} approved the consent request. " + fallback
    elif session["turn_flags"].get("just_verified"):
        name = session["policyholder_name"]
        fallback = f"You're verified, {name}. " + fallback
    if session["memory"]["email_preference"] == "yes" and "email" in message.lower():
        fallback += " I've noted that you'd like this by email, and I'll ask before I send anything."
    path = session.get("intent_path") or "general_claim_question"
    allowed_claim = phrases.path_facts(claim, path)
    facts = {
        "verified": True,
        "workflow_path": path,
        "claim": allowed_claim,
        "guidance": records.guidance_lines(claim, path, message) if path in {"document_submission", "next_steps", "appeal", "denial_question"} else [],
        "caller_context": remembered,
    }
    if session.get("caller_role") == "representative":
        facts["consent_status"] = session.get("consent_status")
        facts["speaking_with"] = session.get("representative_name")
    if path == "status_inquiry":
        facts["field_descriptions"] = records.schema.get("field_descriptions") or {}
    goal = f"Answer only on the {path} path, using the claim fields in facts and nothing else."
    if session.get("consent_status") == "approved":
        goal += " Consent is already approved. Do not ask for it again."
    directive = _directive(
        session,
        goal,
        facts,
        "What else would you like to know about this claim?",
        fallback,
        records,
        parsed,
    )
    directive["forbidden"] = _path_forbidden(path)
    if session.get("consent_status") == "approved":
        directive["forbidden"] = [*directive["forbidden"], "asking for consent or identity again"]
    return directive, False, f"Answered {path} from {claim['case_id']}."


def _post(session: dict[str, Any], parsed: Parsed, records: Records) -> tuple[dict[str, Any], bool, str]:
    record = records.by_id(session["party_id"]) or {}
    on_file = record.get("email")
    masked = mask_email(on_file)
    claim = records.claim(session["active_case_id"])
    if not on_file:
        session["phase"] = "CLOSED"
        fallback = "There is no email on file, so I will close the call without sending a summary."
        return _directive(session, "Close without an email.", {"verified": True, "sent": False}, "", fallback, records, parsed), False, "No email on file."
    supplied = parsed.email
    if supplied and supplied != str(on_file).lower():
        fallback = phrases.email_other_address(masked)
        return _directive(session, "Decline any address that is not on file.", {"masked_email": masked, "sent": False}, "Should I send it to the address on file, or skip the email?", fallback, records, parsed), False, "Declined a different email address."
    consent = parsed.email_consent
    if consent is None:
        fallback = phrases.email_offer(masked, session["memory"]["email_preference"], claim, parsed.emotion)
        facts = {
            "masked_email": masked,
            "email_preference": session["memory"]["email_preference"],
            "active_case_id": session.get("active_case_id"),
            "sent": False,
        }
        return _directive(session, "Ask before sending the summary. Do not send yet.", facts, "Should I send the summary, or skip the email?", fallback, records, parsed), False, "Offered the email summary."
    if consent == "yes":
        subject, body = phrases.summary_email(session, claim)
        if contains_secret(body, session["slots"]):
            subject, body = "Summary of your claims call", "What we discussed:\n- A claim on your policy.\n\nClaim status:\n- See the call for the outcome on file.\n\nNext steps:\n- Call back if you need the next step repeated.\n"
        session["outbox"].append({"to_masked": masked, "subject": subject, "body": body})
        session["phase"] = "CLOSED"
        fallback = phrases.email_sent(masked)
        return _directive(session, "Confirm the summary was sent.", {"masked_email": masked, "sent": True}, "", fallback, records, parsed), False, "Email summary sent."
    if consent == "no":
        session["phase"] = "CLOSED"
        fallback = phrases.email_skipped()
        return _directive(session, "Close without sending.", {"sent": False}, "", fallback, records, parsed), False, "Caller declined the email."
    session["email_unclear"] += 1
    if session["email_unclear"] >= 2:
        session["phase"] = "CLOSED"
        fallback = phrases.email_unclear_close()
        return _directive(session, "Close because email consent stayed unclear.", {"sent": False}, "", fallback, records, parsed), False, "Email consent unclear twice."
    fallback = phrases.email_offer(masked, session["memory"]["email_preference"], claim, parsed.emotion)
    facts = {"masked_email": masked, "email_preference": session["memory"]["email_preference"], "active_case_id": session.get("active_case_id"), "sent": False}
    return _directive(session, "Ask again before sending. Do not send yet.", facts, "Should I send the summary, or skip the email?", fallback, records, parsed), False, "Email consent unclear."


def _closed(session: dict[str, Any], parsed: Parsed, message: str) -> tuple[dict[str, Any], bool, str]:
    asked_something = bool(parsed.intent_path or parsed.case_type or parsed.case_id or "?" in message)
    reopen = parsed.scope == "in_scope_claim" and not parsed.conversation_done and asked_something
    if reopen and parsed.scope != "small_talk":
        session["phase"] = "PROCESS_CASE" if session.get("active_case_id") else "RESOLVE_INTENT"
        return _empty_directive(), True, "Reopened the file."
    fallback = phrases.closed_reply()
    facts = {"verified": session["verified"], "sent": bool(session["outbox"])}
    return _directive(session, "Close warmly.", facts, "", fallback, None, parsed), False, "Call already closed."


def _path_forbidden(path: str) -> list[str]:
    if path == "status_inquiry":
        return ["documents that are not on this claim", "a denial reason that is not in facts"]
    if path in {"denial_question", "appeal", "document_submission", "next_steps"}:
        return ["dollar amounts", "reimbursement figures", "claim ids other than the active one"]
    return ["dollar amounts", "denial reasons that are not in facts"]


def _locate(session: dict[str, Any], records: Records) -> dict[str, Any] | None:
    if session.get("party_id"):
        return records.by_id(session["party_id"])
    found = records.locate(session["slots"])
    if found:
        session["party_id"] = found["party_id"]
        session["account_located"] = True
    return found


def _mark_verified(session: dict[str, Any], record: dict[str, Any]) -> None:
    session["verified"] = True
    session["party_id"] = record["party_id"]
    session["account_located"] = True
    session["policyholder_name"] = record["name"]
    session["phase"] = "RESOLVE_INTENT"
    session["turn_flags"]["just_verified"] = True


def _handoff(session: dict[str, Any], reason: str) -> None:
    session["phase"] = "HUMAN_HANDOFF"
    session["handoff_reason"] = reason
    verified = "yes" if session["verified"] else "no"
    who = session["caller_role"]
    if who == "representative":
        who = f"representative {session.get('representative_name') or ''} ({session.get('relationship') or 'unspecified'}) for {session.get('on_behalf_of') or 'unknown'}"
    session["handoff_note"] = f"Reason: {reason}. Identity verified: {verified}. Caller: {who}."


def _handoff_directive(session: dict[str, Any]) -> dict[str, Any]:
    reason = session.get("handoff_reason") or "transfer"
    fallback = phrases.handoff_reply(reason, bool(session["verified"]))
    return _directive(session, "Transfer and stop asking questions.", {"reason": reason, "verified": session["verified"]}, "", fallback, None, None)


def _empty_directive() -> dict[str, Any]:
    return {
        "phase": "",
        "freedom": "",
        "goal": "",
        "facts": {},
        "must_ask": "",
        "instructions": [],
        "forbidden": [],
        "tone": "",
        "empathy": "",
        "fallback": "",
        "leak_terms": [],
        "allowed_text": "",
    }


def _directive(
    session: dict[str, Any],
    goal: str,
    facts: dict[str, Any],
    must_ask: str,
    fallback: str,
    records: Records | None,
    parsed: Parsed | None,
) -> dict[str, Any]:
    emotion = parsed.emotion if parsed else session.get("last_emotion") or "neutral"
    verified = bool(session["verified"])
    allowed = json.dumps(facts) if verified else ""
    forbidden = []
    if not verified:
        forbidden = ["claim numbers", "denial reasons", "documents", "dollar amounts", "saying the caller is verified"]
    return {
        "phase": session["phase"],
        "freedom": FREEDOM.get(session["phase"], "strict"),
        "goal": goal,
        "facts": facts,
        "must_ask": must_ask,
        "instructions": [
            "Answer only from facts.",
            "Acknowledge emotion before the ask." if emotion in NEGATIVE or emotion in {"anxious", "confused"} else "Be warm and brief.",
        ],
        "forbidden": forbidden,
        "tone": emotion,
        "empathy": phrases.empathy_line(emotion) if emotion != "neutral" else "",
        "fallback": fallback,
        "leak_terms": list(records.leak_terms) if records and not verified else [],
        "allowed_text": allowed,
    }


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


def _ask(session: dict[str, Any]) -> str:
    phase = session["phase"]
    if phase == "VERIFY_ID" and session["caller_role"] == "representative":
        return "I still need the policyholder's approval before I can open the claim."
    if phase == "VERIFY_ID" and not session["account_located"]:
        return "What full name and policy number should I use, or which phone number or email is on the policy?"
    if phase == "VERIFY_ID":
        return "Which remaining detail can you share?"
    if phase == "RESOLVE_INTENT":
        return "Which claim should I open?"
    if phase == "PROCESS_CASE":
        return "What else would you like to know about this claim?"
    if phase == "POST_PROCESS":
        return "Should I send the summary, or skip the email?"
    return "How else can I help with the claim?"


def _ensure_offer(text: str) -> str:
    if "transfer" in text.lower() or "specialist" in text.lower():
        return text
    return text.rstrip() + " If you'd rather talk to a person, I can transfer you to a claims specialist."


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
