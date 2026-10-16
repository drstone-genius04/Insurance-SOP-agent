"""Templated replies. These are the safety net when the model is absent or rejected."""

from __future__ import annotations

from typing import Any

from app.tools.records import MONTH_NAME, claim_brief

FACTOR_LABELS = {
    "name": "full name",
    "dob": "date of birth",
    "phone": "phone number on file",
    "email": "email address on file",
    "id_last4": "last 4 of the SSN or government ID",
}
FACTORS = ("name", "dob", "phone", "email", "id_last4")
NEEDED = 3

EMPATHY = {
    "angry": "I understand this is frustrating, and I'm sorry this feels like a hurdle.",
    "frustrated": "I understand this is frustrating, and I'm sorry for the extra step.",
    "anxious": "I know this can feel worrying, and I'll stay with you through it.",
    "confused": "That's a fair question, and I'll keep this simple.",
    "refusing": "You don't have to share anything you're uncomfortable with.",
}


def empathy_line(emotion: str) -> str:
    return EMPATHY.get(emotion, "")


def greeting() -> str:
    return (
        "Hello, I'm Avery at Harborline Mutual. I can help with a claim, and I'll keep the file "
        "sealed until three details match the policy. You can start with your full name and policy "
        "number, or the phone number or email we have on file."
    )


def hint_phrase(hints: dict[str, Any]) -> str:
    """Turn remembered case hints into the caller's own wording."""
    core = " ".join(bit for bit in (hints.get("status"), hints.get("case_type")) if bit)
    phrase = f"a {core} claim" if core else "a claim"
    month = MONTH_NAME.get(int(hints["month"])) if hints.get("month") else ""
    if month:
        when = f"{month} {hints['year']}" if hints.get("year") else month
        phrase += f" from {when}"
    if hints.get("case_id"):
        phrase += f" ({hints['case_id']})"
    if phrase == "a claim" and not hints.get("case_id") and not month:
        return ""
    return phrase


def verify_reply(
    *,
    located: bool,
    matched: dict[str, bool],
    mismatch: bool,
    not_found: bool,
    offer_human: bool,
    emotion: str,
    remembered: str,
    policyholder: str | None = None,
) -> str:
    """Ask for identity without mentioning any claim fact."""
    parts: list[str] = []
    if line := empathy_line(emotion):
        parts.append(line)
    if emotion in {"angry", "frustrated", "refusing"}:
        parts.append(
            "Claim details stay sealed until I verify the policyholder, so I don't share them with the wrong person."
        )
    elif emotion == "confused":
        parts.append("I need three details that match the policy before I can open a claim.")
    if not_found:
        parts.append("I could not find an account matching those details.")
    elif mismatch:
        parts.append("Some of the details you gave did not match our records.")
    remaining = [FACTOR_LABELS[name] for name in FACTORS if not matched.get(name)]
    have = sum(1 for name in FACTORS if matched.get(name))
    still = max(0, NEEDED - have)
    if not located:
        parts.append(
            "What full name and policy number should I use, or which phone number or email is on the policy?"
        )
    elif still > 0:
        who = f"Thanks{', ' + policyholder if policyholder and matched.get('name') else ''}."
        parts.append(
            f"{who} I have {have} matching detail{'s' if have != 1 else ''}. "
            f"I still need {still} more. Which of these can you share: {choose(remaining)}?"
        )
    else:
        parts.append("I still need to confirm the policyholder before I open the file. Which detail should we use?")
    if remembered and emotion in {"angry", "frustrated", "anxious"}:
        parts.append(f"I'll hold onto what you said about {remembered} and come back to it after verification.")
    elif remembered:
        parts.append(f"I'll remember {remembered} and pick it up once you're verified.")
    if offer_human:
        parts.append("If you'd rather not continue here, I can transfer you to a claims specialist.")
    return " ".join(part.strip() for part in parts if part.strip())


def representative_reply(
    *,
    found: bool,
    status: str,
    rep_name: str | None,
    buyer_name: str | None,
    offer_human: bool,
    emotion: str,
    remembered: str,
) -> str:
    parts: list[str] = []
    if line := empathy_line(emotion):
        parts.append(line)
    if not found:
        parts.append(
            "I could not find an authorization on file for that. "
            "Please tell me your name and the policyholder's full name."
        )
    elif status == "pending":
        who = buyer_name or "the policyholder"
        parts.append(
            f"I sent {who} a consent request. I can open the claim once they approve it. "
            "You can stay on the line."
        )
        parts.append("I won't use the policyholder's date of birth or ID from you as verification.")
    elif status == "approved":
        parts.append(f"{buyer_name or 'The policyholder'} approved the request, so I can open the file with you.")
    if remembered and status != "approved":
        parts.append(f"I'll remember {remembered} and use it after consent.")
    if offer_human:
        parts.append("If you'd rather speak with a person, I can transfer you.")
    text = " ".join(part.strip() for part in parts if part and part.strip())
    if rep_name and found and text:
        return f"{rep_name} — {text}"
    return text


def list_claims_reply(claims: list[dict[str, Any]], remembered: str, ambiguous: bool) -> str:
    briefs = [f"{row['case_id']} ({row['case_type']}, opened {row['created_at']}, {row['status']})" for row in claims]
    if ambiguous:
        lead = "I found more than one claim that fits what you described: "
    elif remembered:
        lead = f"You mentioned {remembered}. I don't have a single match, so here is what is on the policy: "
    else:
        lead = "Here are the claims on this policy: "
    return lead + join(briefs) + ". Which one should I open?"


def claim_reply(claim: dict[str, Any], message: str, remembered: str, switched: str | None) -> str:
    """Answer from the claim record only. Wording follows the caller's question."""
    lowered = message.lower()
    case_id = claim["case_id"]
    docs = [str(item) for item in claim.get("documents_needed") or []]
    parts: list[str] = []
    if switched:
        parts.append(f"I'll switch from {switched} to {case_id}.")
    elif remembered:
        parts.append(f"You mentioned {remembered}. That matches {case_id}.")
    documents_question = any(word in lowered for word in ("document", "send", "submit", "by when", "when do", "what do i need"))
    timing_question = any(word in lowered for word in ("how long", "processing", "how soon"))
    if documents_question and docs:
        parts.append(f"For {case_id}, please send {join(docs)} within a week.")
        if claim.get("appeal_deadline"):
            parts.append(f"The appeal deadline on file is {claim['appeal_deadline']}.")
    elif claim.get("denial_reason") and (claim.get("status") == "denied" or "den" in lowered or not documents_question):
        parts.append(f"{case_id} is {claim['status']} because {claim['denial_reason']}.")
        if docs:
            parts.append(f"The file still needs {join(docs)}.")
        if claim.get("appeal_deadline"):
            parts.append(f"The appeal deadline on file is {claim['appeal_deadline']}.")
    else:
        parts.append(
            f"{case_id} is a {claim.get('case_type')} claim opened {claim.get('created_at')} "
            f"and the status is {claim.get('status')}."
        )
        if claim.get("summary"):
            parts.append(str(claim["summary"]) + ".")
    if timing_question:
        parts.append("Once the missing files are received, review usually restarts and takes less than a week.")
    parts.append("What else would you like to know about this claim?")
    return " ".join(parts)


def email_offer(masked: str, preference: str | None, claim: dict[str, Any] | None, emotion: str) -> str:
    parts: list[str] = []
    if line := empathy_line(emotion):
        parts.append(line)
    if preference == "yes":
        lead = f"You mentioned earlier that you'd like a summary by email. I can send it to {masked}."
    elif preference == "no":
        lead = f"You asked me not to email earlier. I can still send a summary to {masked} if you want it."
    else:
        lead = f"I can email a short summary of this call to {masked}."
    parts.append(lead)
    parts.append("It would cover what we discussed, the claim outcome, and the next steps.")
    parts.append("Should I send it, or would you rather skip the email?")
    if claim:
        parts.append(f"Nothing goes out until you say yes. The active claim is {claim.get('case_id')}.")
    return " ".join(parts)


def email_other_address(masked: str) -> str:
    return (
        f"I can only send the summary to the address on file, {masked}. "
        "I can't use a different address on this call. Should I send it there, or skip the email?"
    )


def email_sent(masked: str) -> str:
    return f"I sent the summary to {masked}. It covers what we discussed, the claim outcome, and the next steps. You're welcome to call again if you need anything else."


def email_skipped() -> str:
    return "I won't send an email. You're welcome to call again if you need anything else."


def email_unclear_close() -> str:
    return "I didn't catch a clear yes or no, so I won't send an email. You're welcome to call again."


def closed_reply() -> str:
    return "Glad I could help. If another claim question comes up, just tell me and I'll reopen the file."


def handoff_reply(reason: str, verified: bool) -> str:
    if "consent" in reason:
        lead = "The policyholder hasn't approved the consent request, so I'm transferring you to a specialist."
    elif "identity" in reason:
        lead = "I wasn't able to verify the policy, so I'm transferring you to a specialist rather than guessing."
    elif "off-topic" in reason or "outside" in reason:
        lead = "These questions are outside what I can help with on this line, so I'll transfer you to a person."
    elif "frustrat" in reason:
        lead = "I can hear this isn't working here, so I'll transfer you to a specialist who can take it from here."
    else:
        lead = "I'll transfer you to a claims specialist."
    state = "Identity is verified." if verified else "Identity was not verified, so the file is still sealed."
    return f"{lead} {state} You won't need to start the story over."


def queue_reply() -> str:
    return "You're already in the queue for a claims specialist. Please stay on the line, or start a new call if you want to begin again with me."


def scope_reply(ask: str, offer_human: bool, emotion: str) -> str:
    parts: list[str] = []
    if line := empathy_line(emotion):
        parts.append(line)
    parts.append("I can only help with Harborline claims on this line, so I can't answer that.")
    if offer_human:
        parts.append("If you keep running into questions I can't take, I can transfer you to a person.")
    parts.append(ask)
    return " ".join(parts)


def small_talk_reply(ask: str) -> str:
    return f"Hello. {ask}"


def config_notice(status: int) -> str:
    return (
        f"I can't reach the language model (HTTP {status}). "
        "Check LLM_API_KEY, LLM_PROVIDER, and LLM_MODEL, then send your message again. "
        "Nothing about the call has changed."
    )


def transport_notice(ask: str) -> str:
    return f"I couldn't reach the language model just now, so I'll stay on the current step. {ask}"


def summary_email(session: dict[str, Any], claim: dict[str, Any] | None) -> tuple[str, str]:
    """Three-part summary. Built from memory and the claim record, never from identity slots."""
    discussed: list[str] = []
    for hint in session["memory"].get("intent_hints") or []:
        discussed.append(f"Caller said they were calling about {hint}.")
    discussed.extend(session["memory"].get("notes") or [])
    if not discussed:
        discussed.append("The caller asked about a claim.")
    discussed_lines = "\n".join(f"- {note}" for note in discussed)
    if claim:
        outcome = (
            f"- {claim.get('case_id')} ({claim.get('case_type')}) opened {claim.get('created_at')} "
            f"is {claim.get('status')}."
        )
        if claim.get("denial_reason"):
            outcome += f" Reason on file: {claim['denial_reason']}."
        docs = [str(item) for item in claim.get("documents_needed") or []]
        steps = []
        if docs:
            steps.append(f"Send {join(docs)} within a week.")
        if claim.get("appeal_deadline"):
            steps.append(f"The appeal deadline on file is {claim['appeal_deadline']}.")
        if not steps:
            steps.append("No further document is listed on this claim. Call back if the status changes.")
        next_steps = "\n".join(f"- {step}" for step in steps)
    else:
        outcome = "- No claim was opened on this call."
        next_steps = "- Call back with the claim you want to review."
    body = (
        "What we discussed:\n"
        f"{discussed_lines}\n\n"
        "Claim status:\n"
        f"{outcome}\n\n"
        "Next steps:\n"
        f"{next_steps}\n"
    )
    return "Summary of your Harborline claims call", body


def choose(items: list[str]) -> str:
    """Join options the caller may pick from."""
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} or {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + ", or " + cleaned[-1]


def join(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + ", and " + cleaned[-1]


def briefs(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim_brief(row) for row in claims]
