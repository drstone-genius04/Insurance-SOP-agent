"""Load claim fixtures and match them with deterministic normalizers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_NAME = {number: name.title() for name, number in MONTHS.items()}

_DATE_FORMATS = (
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"),
    re.compile(
        r"^(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$",
        re.I,
    ),
)


def fold_name(value: str | None) -> str:
    """Case- and punctuation-fold a personal name for comparison."""
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z\s]", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_date(value: str | None) -> str | None:
    """Normalize a date to ISO. Return None when the value is not a full date."""
    if not value:
        return None
    text = value.strip()
    iso = _DATE_FORMATS[0].match(text)
    if iso:
        return _iso(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    us = _DATE_FORMATS[1].match(text)
    if us:
        return _iso(int(us.group(3)), int(us.group(1)), int(us.group(2)))
    named = _DATE_FORMATS[2].match(text)
    if named:
        return _iso(int(named.group(3)), MONTHS[named.group(1).lower()], int(named.group(2)))
    return None


def normalize_phone(value: str | None) -> str | None:
    """Normalize a US phone number to +1XXXXXXXXXX."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+1{digits}"


def normalize_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    return value.strip().lower()


def normalize_last4(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) != 4:
        return None
    return digits


def normalize_policy(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"POL-\d+", value.upper())
    return match.group(0) if match else None


def mask_email(value: str | None) -> str:
    """Show the first character and the domain. Never the full local part."""
    email = normalize_email(value)
    if not email or "@" not in email:
        return "the email on file"
    local, domain = email.split("@", 1)
    return f"{local[:1]}*****@{domain}"


def _iso(year: int, month: int, day: int) -> str | None:
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


class Records:
    """Read-only fixture store. All identity and claim decisions go through here."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.policyholders: list[dict[str, Any]] = payload["policyholders"]
        self.claims: list[dict[str, Any]] = payload["claims"]
        self.schema: dict[str, Any] = payload["schema"]
        self.guidance: dict[str, Any] = payload["guidance"]
        self.representatives: list[dict[str, Any]] = payload["representatives"]
        self.consent: dict[str, Any] = payload["consent"]
        self.leak_terms: list[str] = payload["leak_terms"]

    @classmethod
    def load(cls, fixtures_dir: Path) -> Records:
        def read(name: str, default: Any) -> Any:
            path = fixtures_dir / name
            if not path.exists():
                return default
            return json.loads(path.read_text())

        policyholders = read("policyholders.json", [])
        claims = read("claims.json", [])
        return cls(
            {
                "policyholders": policyholders,
                "claims": claims,
                "schema": read("claim_schema.json", {}),
                "guidance": read("required_document_guideline.json", {}),
                "representatives": read("representatives.json", []),
                "consent": read("consent_scenarios.json", {"default": {"status_sequence": ["pending", "approved"]}}),
                "leak_terms": _leak_terms(claims),
            }
        )

    def by_id(self, party_id: str | None) -> dict[str, Any] | None:
        return next((row for row in self.policyholders if row["party_id"] == party_id), None)

    def locate(self, slots: dict[str, str | None]) -> dict[str, Any] | None:
        """Find a policyholder. Policy number, then name, phone, and email."""
        policy = normalize_policy(slots.get("policy_number"))
        if policy:
            found = next((row for row in self.policyholders if row["policy_number"] == policy), None)
            if found:
                return found
        name = fold_name(slots.get("name"))
        if name:
            found = self._by_name(name)
            if found:
                return found
        phone = normalize_phone(slots.get("phone"))
        if phone:
            found = self._by_phone(phone)
            if found:
                return found
        email = normalize_email(slots.get("email"))
        if email:
            found = self._by_email(email)
            if found:
                return found
        return None

    def factor_matches(self, record: dict[str, Any], factor: str, value: str | None) -> bool:
        """Compare one identity factor. The policy number is never a factor."""
        if not value:
            return False
        if factor == "name":
            folded = fold_name(value)
            names = [record.get("name"), *record.get("name_aliases", [])]
            return folded in {fold_name(item) for item in names if item}
        if factor == "dob":
            return normalize_date(value) == record.get("dob")
        if factor == "phone":
            phone = normalize_phone(value)
            known = [record.get("phone"), *record.get("phone_aliases", [])]
            return bool(phone) and phone in {normalize_phone(item) for item in known}
        if factor == "email":
            email = normalize_email(value)
            known = [record.get("email"), *record.get("email_aliases", [])]
            return bool(email) and email in {normalize_email(item) for item in known}
        if factor == "id_last4":
            return normalize_last4(value) == str(record.get("id_last4"))
        return False

    def claims_for(self, party_id: str | None) -> list[dict[str, Any]]:
        return [row for row in self.claims if row.get("party_id") == party_id]

    def claim(self, case_id: str | None) -> dict[str, Any] | None:
        return next((row for row in self.claims if row.get("case_id") == case_id), None)

    def filter_claims(self, party_id: str | None, hints: dict[str, Any]) -> list[dict[str, Any]]:
        """Narrow a caller's claims by type, status, month, year, and case id."""
        selected: list[dict[str, Any]] = []
        for row in self.claims_for(party_id):
            if hints.get("case_id") and row.get("case_id") != hints["case_id"]:
                continue
            if hints.get("case_type") and row.get("case_type") != hints["case_type"]:
                continue
            if hints.get("status") and row.get("status") != hints["status"]:
                continue
            created = str(row.get("created_at") or "")
            if hints.get("month"):
                if len(created) < 7 or int(created[5:7]) != int(hints["month"]):
                    continue
                if hints.get("year") and int(created[:4]) != int(hints["year"]):
                    continue
            elif hints.get("year") and (len(created) < 4 or int(created[:4]) != int(hints["year"])):
                continue
            selected.append(row)
        return selected

    def lookup_representative(self, rep_name: str | None, buyer_name: str | None) -> dict[str, Any] | None:
        """Match a representative by their name and the policyholder's name or alias."""
        rep = fold_name(rep_name)
        buyer = fold_name(buyer_name)
        if not rep or not buyer:
            return None
        for row in self.representatives:
            if fold_name(row.get("rep_name")) != rep:
                continue
            record = self.by_id(row.get("buyer_party_id"))
            names = [row.get("buyer_name")]
            if record:
                names.extend([record.get("name"), *record.get("name_aliases", [])])
            if buyer in {fold_name(item) for item in names if item}:
                return row
        return None

    def consent_status(self, scenario: str, check: int) -> str:
        """Return the simulated consent status for a 1-based check count."""
        sequence = self.consent.get(scenario, self.consent.get("default", {})).get("status_sequence", [])
        if check <= 0:
            return "pending"
        if check > len(sequence):
            return "timed_out"
        status = str(sequence[check - 1])
        if status == "timed_out":
            return "timed_out"
        return status

    def guidance_lines(self, claim: dict[str, Any], intent_path: str | None, message: str) -> list[str]:
        """Select guideline sentences that match this claim and the caller's wording."""
        guide = self.guidance
        lines: list[str] = []
        default = (guide.get("default_guidance") or {}).get("en")
        if default:
            lines.append(str(default))
        case_type = (guide.get("case_type_guidance") or {}).get(claim.get("case_type"), {})
        if case_type.get("en"):
            lines.append(str(case_type["en"]))
        documents = [str(item) for item in claim.get("documents_needed") or []]
        doc_text = _join(documents) or "the requested files"
        average = (
            (guide.get("claim_followup_settings") or {})
            .get("average_processing_time_after_submission", {})
            .get("en", "usually less than a week")
        )
        lowered = message.lower()
        for entry in guide.get("claim_followup_guidance") or []:
            hints = entry.get("intent_hints") or []
            needles = [item.lower() for item in entry.get("match_any") or []]
            path_ok = intent_path in hints if intent_path else False
            word_ok = any(needle in lowered for needle in needles)
            if entry.get("requires_documents") and not documents:
                continue
            if needles and not word_ok:
                continue
            if not needles and not path_ok:
                continue
            text = str(entry.get("en") or "")
            lines.append(
                text.format(
                    case_id=claim.get("case_id"),
                    documents=doc_text,
                    average_processing_time_after_submission=average,
                )
            )
        specific = guide.get("document_guidance") or {}
        alternatives = guide.get("document_alternative_guidance") or {}
        for name in documents:
            for key, body in specific.items():
                if _doc_match(name, key) and body.get("en"):
                    lines.append(str(body["en"]))
            for key, body in alternatives.items():
                if key != "default" and _doc_match(name, key) and body.get("en"):
                    lines.append(str(body["en"]))
        fallback = (guide.get("claim_followup_fallback") or {}).get("en")
        if fallback:
            lines.append(str(fallback))
        return _unique(lines)

    def _by_name(self, folded: str) -> dict[str, Any] | None:
        hits = []
        for row in self.policyholders:
            names = [row.get("name"), *row.get("name_aliases", [])]
            if folded in {fold_name(item) for item in names if item}:
                hits.append(row)
        return hits[0] if len(hits) == 1 else None

    def _by_phone(self, phone: str) -> dict[str, Any] | None:
        for row in self.policyholders:
            known = [row.get("phone"), *row.get("phone_aliases", [])]
            if phone in {normalize_phone(item) for item in known}:
                return row
        return None

    def _by_email(self, email: str) -> dict[str, Any] | None:
        for row in self.policyholders:
            known = [row.get("email"), *row.get("email_aliases", [])]
            if email in {normalize_email(item) for item in known}:
                return row
        return None


def claim_brief(claim: dict[str, Any]) -> dict[str, Any]:
    """Fields safe to show before a claim is selected: id, type, date, status."""
    return {
        "case_id": claim.get("case_id"),
        "case_type": claim.get("case_type"),
        "created_at": claim.get("created_at"),
        "status": claim.get("status"),
    }


def claim_facts(claim: dict[str, Any]) -> dict[str, Any]:
    """The grounded record the responder may use after a claim is selected."""
    keys = (
        "case_id",
        "case_type",
        "created_at",
        "status",
        "summary",
        "denial_reason",
        "documents_needed",
        "appeal_deadline",
        "expected_reimbursement_amount",
        "allowed_max_amount",
        "net_pay",
        "net_fee",
    )
    return {key: claim.get(key) for key in keys if claim.get(key) not in (None, "", [])}


def _doc_match(needed: str, guide_key: str) -> bool:
    left = needed.lower()
    right = guide_key.lower()
    return left in right or right in left or left.split()[-1] in right


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _unique(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for line in lines:
        if line and line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def _leak_terms(claims: list[dict[str, Any]]) -> list[str]:
    terms: set[str] = set()
    for claim in claims:
        for key in ("case_id", "summary", "denial_reason", "appeal_deadline"):
            value = str(claim.get(key) or "").strip()
            if len(value) >= 6:
                terms.add(value)
        for doc in claim.get("documents_needed") or []:
            if len(str(doc)) >= 6:
                terms.add(str(doc))
        for key in ("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee"):
            amount = str(claim.get(key) or "")
            if amount and amount not in {"0", "0.00", "0.0"}:
                terms.add(amount)
    return sorted(terms)
