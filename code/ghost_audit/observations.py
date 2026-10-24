"""Interpret a raw Fact as a normalized Observation about the *state of a resource*.

Kinds (what the evidence shows the record is, independent of policy):

    PRESENT     the record exists (nothing is known about identifiers in it, or it has none to inspect)
    ABSENT      the source explicitly reports the record as not found
    EXPOSED     the record exists and still carries direct identifiers (name/email/phone/hash/user link)
    REDACTED    the record exists with direct identifiers removed (a stable reference may remain)
    ANONYMIZED  the record exists and is no longer attributable to a user
    ACTIVE      a data flow is enabled
    DISABLED    a data flow is disabled
    RESTORABLE  a backup/archive can be restored
    BLOCKED     restore of a backup/archive is blocked / denied
    UNKNOWN     the source says something we cannot map

Nothing here applies policy; ``policy.py`` decides what each kind means for each data category.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .models import Fact
from .text_ids import identifiers_in_text

PRESENT, ABSENT, EXPOSED, REDACTED, ANONYMIZED = "PRESENT", "ABSENT", "EXPOSED", "REDACTED", "ANONYMIZED"
ACTIVE, DISABLED, RESTORABLE, BLOCKED, UNKNOWN = "ACTIVE", "DISABLED", "RESTORABLE", "BLOCKED", "UNKNOWN"

_NULLS = {"", "null", "none", "nan"}
_TRUE = {"true", "1", "yes", "y"}


def is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in _NULLS)


def truthy(v: Any) -> bool:
    return v is True or (not isinstance(v, bool) and not is_null(v) and str(v).strip().lower() in _TRUE)


def first(attrs: Dict[str, Any], *names: str) -> Any:
    """First non-null attribute among ``names`` (also matches dotted keys such as 'document.email')."""
    for n in names:
        for k, v in attrs.items():
            if (k == n or k.endswith("." + n)) and not is_null(v):
                return v
    return None


def any_set(attrs: Dict[str, Any], *names: str) -> bool:
    return first(attrs, *names) is not None


@dataclass
class Observation:
    fact: Fact
    kind: str
    reason: str = ""
    retention_until: Optional[str] = None

    @property
    def file_id(self) -> str:
        return self.fact.file_id

    @property
    def time(self) -> str:
        return self.fact.effective_time()

    @property
    def role(self) -> Optional[str]:
        return self.fact.source_role

    @property
    def category(self) -> Optional[str]:
        return self.fact.category


# --------------------------------------------------------------------------------------------- explicit states
_EXPLICIT = {
    "FOUND": PRESENT, "PRESENT": PRESENT, "NOT_FOUND": ABSENT, "IDENTIFIABLE": EXPOSED, "EXPOSED": EXPOSED,
    "REDACTED": REDACTED, "ANONYMIZED": ANONYMIZED, "DISABLED": DISABLED, "BLOCKED": BLOCKED, "RESTORABLE": RESTORABLE,
}


def _from_state(category: Optional[str], state: str) -> Optional[str]:
    if state == "ACTIVE":
        return RESTORABLE if category == "BACKUP_ARCHIVE" else ACTIVE
    return _EXPLICIT.get(state)


# --------------------------------------------------------------------------------------------- log events
def _interpret_event(f: Fact) -> Optional[Observation]:
    a, ev = f.attrs, f.attrs.get("event", "")
    if ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
        return Observation(f, ACTIVE if truthy(a.get("enabled")) else DISABLED, f"{ev} enabled={a.get('enabled')}")
    if ev == "restore_policy_read_completed":
        return Observation(f, RESTORABLE if truthy(a.get("restore_allowed")) else BLOCKED, f"restore_allowed={a.get('restore_allowed')}")
    if ev == "restore_candidate_evaluated":
        if truthy(a.get("deletion_block")):
            return Observation(f, BLOCKED, "deletion_block=true")
        return Observation(f, RESTORABLE if truthy(a.get("eligible")) else BLOCKED, f"eligible={a.get('eligible')} deletion_block={a.get('deletion_block')}")
    if ev == "restore_authorization_completed":
        return Observation(f, BLOCKED if str(a.get("decision", "")).lower() == "deny" else RESTORABLE, f"decision={a.get('decision')}")
    if ev == "analytics_profile_read_completed":
        exposed = any(any_set(a, k) for k in ("email", "phone", "email_hash", "customer_reference", "user_id"))
        return Observation(f, EXPOSED if exposed else ANONYMIZED, "direct identifiers present" if exposed else "no direct identifiers")
    if ev == "analytics_identity_scan":
        return Observation(f, EXPOSED if str(a.get("direct_identifiers", "0")) not in ("0", "") else ANONYMIZED, f"direct_identifiers={a.get('direct_identifiers')}")
    if ev == "analytics_profile_read":
        return Observation(f, EXPOSED if str(a.get("direct_identifiers", "0")) not in ("0", "") else ANONYMIZED, f"direct_identifiers={a.get('direct_identifiers')}")
    if ev == "support_ticket_read_completed":
        exposed = any(any_set(a, k) for k in ("requester_email", "requester_name", "requester_phone"))
        return Observation(f, EXPOSED if exposed else REDACTED, "requester identifiers present" if exposed else "requester identifiers removed")
    if ev == "message_pii_scan":
        return Observation(f, EXPOSED, f"detector={a.get('detector')} matched_value present")
    if ev == "message_read":
        return Observation(f, REDACTED if str(a.get("message_text", "")).strip() == "[REDACTED]" else PRESENT, "message_text")
    if ev == "attachment_pii_scan":
        return Observation(f, PRESENT, "attachment scanned; direct identifier found")
    if ev in ("export_metadata_read_completed", "export_download_check_completed"):
        return Observation(f, PRESENT if truthy(a.get("available")) else ABSENT, f"available={a.get('available')}")
    if ev == "audit_record_read_completed":
        return Observation(f, PRESENT, "audit record read", retention_until=first(a, "retention_until"))
    if ev == "retained_order_loaded":
        return Observation(f, PRESENT, "retained order loaded", retention_until=first(a, "retained_until"))
    if ev in ("contact_query_completed", "contact_lookup_completed"):
        return Observation(f, PRESENT if str(a.get("rows", "1")) != "0" else ABSENT, f"rows={a.get('rows')}")
    kind = _from_state(f.category, f.state)
    return Observation(f, kind, f"{ev}") if kind else None


# --------------------------------------------------------------------------------------------- snapshot records
def _interpret_record(f: Fact) -> Observation:
    a, cat = f.attrs, f.category
    if a.get("source") == "image" and cat in ("ANALYTICS_PROFILE", "ACCOUNT_TOMBSTONE", "SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG")             and not a.get("identifiers_visible"):
        return Observation(f, PRESENT, "seen in a screenshot; the absence of visible identifiers does not show anonymization")
    if cat == "SUPPORT_MESSAGE":
        text = a.get("message_text")
        if isinstance(text, str):
            if text.strip() == "[REDACTED]":
                return Observation(f, REDACTED, "message_text redacted")
            if not identifiers_in_text(text).is_empty():
                return Observation(f, EXPOSED, "message_text contains direct identifiers")
            return Observation(f, PRESENT, "message_text without detectable identifiers")
        exposed = any(any_set(a, k) for k in ("requester_email", "requester_name", "requester_phone"))
        return Observation(f, EXPOSED if exposed else REDACTED, "requester identifiers present" if exposed else "requester identifiers removed")
    if cat == "ANALYTICS_PROFILE":
        exposed = any(any_set(a, k) for k in ("user_id", "customer_reference", "email", "phone", "email_hash"))
        return Observation(f, EXPOSED if exposed else ANONYMIZED, "attributable to a user" if exposed else "no user attribution")
    if cat == "ACCOUNT_TOMBSTONE":
        exposed = any(any_set(a, k) for k in ("name", "email", "phone", "email_hash"))
        return Observation(f, EXPOSED if exposed else REDACTED, "direct identifiers present" if exposed else "only the stable reference remains")
    if cat == "EMAIL_DELIVERY_LOG":
        exposed = any(any_set(a, k) for k in ("email", "recipient_email", "to"))
        return Observation(f, EXPOSED if exposed else REDACTED, "email address present" if exposed else "email address removed")
    if cat == "DATA_FLOW":
        if first(a, "enabled") is None and "enabled" not in a:
            return Observation(f, PRESENT, "data flow seen; enabled/disabled state not shown")
        return Observation(f, ACTIVE if truthy(first(a, "enabled")) else DISABLED, f"enabled={first(a, 'enabled')}")
    if cat == "BACKUP_ARCHIVE":
        if "restore_allowed" not in a and not any(k.endswith(".restore_allowed") for k in a):
            return Observation(f, PRESENT, "archive seen; restore access not shown")
        return Observation(f, RESTORABLE if truthy(first(a, "restore_allowed")) else BLOCKED, f"restore_allowed={first(a, 'restore_allowed')}")
    if cat == "SECURITY_AUDIT_LOG":
        return Observation(f, PRESENT, "audit record", retention_until=first(a, "retention_until"))
    if cat == "LEGAL_HOLD_RECORD":
        return Observation(f, PRESENT, "retained order record", retention_until=first(a, "retained_until"))
    return Observation(f, PRESENT, "record present")


def interpret(f: Fact) -> Optional[Observation]:
    """Normalize one fact. Returns None for facts that do not describe a resource's state (e.g. start-of-query log lines)."""
    if f.resource_id is None:
        return None
    if f.attrs.get("event"):
        return _interpret_event(f)
    if f.state == "PRESENT":
        return _interpret_record(f)
    kind = _from_state(f.category, f.state)
    if kind is None:
        return Observation(f, UNKNOWN, f"unmapped state {f.state}")
    retention = first(f.attrs, "retention_until", "retained_until")
    return Observation(f, kind, f"source state {f.state}", retention_until=retention)
