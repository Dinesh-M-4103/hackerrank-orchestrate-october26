"""Policy engine: what does ``deletion_rules.csv`` require of a resource, and is its observed state compliant?

The required action per data category is *read from the rules file* (``PolicyEngine.from_rules``); the code below adds
the explicit, testable semantics of each action:

    DELETE     the record must not exist                         -> PRESENT/EXPOSED/... is a violation, ABSENT is compliant
    REDACT     keep the record, remove direct identifiers        -> EXPOSED is a violation, REDACTED is compliant
    ANONYMIZE  keep the record, unlink it from the user          -> EXPOSED is a violation, ANONYMIZED is compliant
    RETAIN     keep the record untouched                         -> PRESENT is compliant (verify present)
    RETAIN (backup)  keep the archive but restore must be blocked -> RESTORABLE is a violation, BLOCKED is compliant
    REDACT (tombstone) as REDACT, preserving only the stable user reference

``DATA_FLOW`` has no row in the rules file; the specification requires that no active sync/import path can recreate
prohibited data, so an ACTIVE flow is a violation and DISABLED is compliant.

Situations the specification does not resolve are *not* given invented behaviour: they yield an Assessment with
``acted=False`` and a note (see ``UNRESOLVED`` in the README / milestone report).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .models import Rule
from .observations import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, PRESENT, REDACTED, RESTORABLE, UNKNOWN)

DELETE, REDACT, ANONYMIZE, RETAIN = "DELETE", "REDACT", "ANONYMIZE", "RETAIN"
FLOW_CATEGORY = "DATA_FLOW"
FLOW_ACTION = "DISABLE"          # synthetic: not a row of deletion_rules.csv

# categories whose RETAIN rule is about restore access rather than keeping a record untouched
RESTORE_CATEGORIES = {"BACKUP_ARCHIVE"}
TOMBSTONE_CATEGORY = "ACCOUNT_TOMBSTONE"


@dataclass(frozen=True)
class Assessment:
    category: str
    rule_id: str
    required_action: str
    kind: str                         # observed kind that was assessed
    violation: bool
    acted: bool                       # False when the spec does not resolve the situation (no steps are generated)
    finding_op: Optional[str] = None
    remediation_op: Optional[str] = None
    verification_op: Optional[str] = None
    notes: tuple = ()
    unknown: bool = False             # True when the state is merely not observable (a final-state check can still be planned)


@dataclass
class Context:
    """Per-resource facts the rule conditions need."""
    audit_at: str
    retention_until: Optional[str] = None
    sole_owner: Optional[bool] = None          # SUPPORT_ATTACHMENT: does it belong only to the deletion subject?
    subject_reference_kept: Optional[bool] = None
    notes: List[str] = field(default_factory=list)


class PolicyEngine:
    def __init__(self, rules: Iterable[Rule]):
        self.rules: Dict[str, Rule] = {r.data_category: r for r in rules}

    @classmethod
    def from_rules(cls, rules: Iterable[Rule]) -> "PolicyEngine":
        return cls(rules)

    def required_action(self, category: str) -> Optional[str]:
        if category == FLOW_CATEGORY:
            return FLOW_ACTION
        r = self.rules.get(category)
        return r.required_action if r else None

    def rule_id(self, category: str) -> str:
        r = self.rules.get(category)
        return r.rule_id if r else "spec:no_data_flow_rule"

    def final_verification(self, category: Optional[str]) -> Optional[str]:
        """The verification operation for the *required final state* of a category (used for owned resources whose current
        state is not observable: the check can still be planned, it just cannot be cited)."""
        if category is None:
            return None
        action = self.required_action(category)
        if action == FLOW_ACTION:
            return "VERIFY_DATA_FLOW_DISABLED"
        if action == DELETE:
            return "VERIFY_RECORD_ABSENT"
        if action == REDACT:
            return "VERIFY_RECORD_REDACTED"
        if action == ANONYMIZE:
            return "VERIFY_RECORD_ANONYMIZED"
        if action == RETAIN:
            return "VERIFY_RESTORE_BLOCKED" if category in RESTORE_CATEGORIES else "VERIFY_RECORD_PRESENT"
        return None

    # ------------------------------------------------------------------ assessment
    def assess(self, category: Optional[str], kind: str, ctx: Context) -> Assessment:
        if category is None or kind == UNKNOWN:
            return self._skip(category or "?", kind, "no category or unmapped state")
        action = self.required_action(category)
        if action is None:
            return self._skip(category, kind, "category has no rule in deletion_rules.csv")
        rid = self.rule_id(category)
        notes = list(ctx.notes)

        if action == FLOW_ACTION:
            if kind == ACTIVE:
                return Assessment(category, rid, action, kind, True, True, "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED", tuple(notes))
            if kind == DISABLED:
                return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_DATA_FLOW_DISABLED", tuple(notes))
            return self._skip(category, kind, "data flow state not enabled/disabled", rid, action, unknown=True)

        if action == DELETE:
            if category == "SUPPORT_ATTACHMENT" and ctx.sole_owner is False:
                return self._skip(category, kind, "rule_06 applies only when the attachment belongs only to the subject; it is shared", rid, action)
            if kind == ABSENT:
                return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_RECORD_ABSENT", tuple(notes))
            if kind in (PRESENT, EXPOSED, REDACTED, ANONYMIZED):
                return Assessment(category, rid, action, kind, True, True, "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT", tuple(notes))
            return self._skip(category, kind, "state cannot be a DELETE-category record", rid, action)

        if action == REDACT:
            if kind == EXPOSED:
                return Assessment(category, rid, action, kind, True, True, "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED", tuple(notes))
            if kind == REDACTED:
                if category == TOMBSTONE_CATEGORY and ctx.subject_reference_kept is False:
                    notes.append("tombstone lost its stable user reference")
                    return self._skip(category, kind, "tombstone has no stable reference (spec gives no finding for this)", rid, action)
                return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_RECORD_REDACTED", tuple(notes))
            if kind == ABSENT:
                return self._skip(category, kind, "record is gone although the rule requires redaction, not deletion; the spec defines no finding for over-deletion", rid, action)
            return self._skip(category, kind, "cannot tell whether identifiers remain", rid, action, unknown=True)

        if action == ANONYMIZE:
            if kind == EXPOSED:
                return Assessment(category, rid, action, kind, True, True, "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED", tuple(notes))
            if kind == ANONYMIZED:
                return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_RECORD_ANONYMIZED", tuple(notes))
            return self._skip(category, kind, "cannot tell whether the record is still attributable", rid, action, unknown=True)

        if action == RETAIN:
            if category in RESTORE_CATEGORIES:
                if kind == RESTORABLE:
                    return Assessment(category, rid, action, kind, True, True, "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED", tuple(notes))
                if kind == BLOCKED:
                    return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_RESTORE_BLOCKED", tuple(notes))
                return self._skip(category, kind, "archive present but restore access unknown", rid, action, unknown=True)
            if ctx.retention_until and ctx.retention_until < ctx.audit_at:
                notes.append("retention period ended before audit_at")
                return self._skip(category, kind, "retention period expired; the spec does not say what applies then", rid, action)
            if kind in (PRESENT, EXPOSED, REDACTED, ANONYMIZED):
                return Assessment(category, rid, action, kind, False, True, None, None, "VERIFY_RECORD_PRESENT", tuple(notes))
            if kind == ABSENT:
                return self._skip(category, kind, "required retained record is missing; the spec defines no finding for a missing retained record", rid, action)
        return self._skip(category, kind, "unhandled combination", rid, action)

    @staticmethod
    def _skip(category: str, kind: str, why: str, rid: str = "", action: str = "", unknown: bool = False) -> Assessment:
        return Assessment(category, rid, action, kind, False, False, notes=(why,), unknown=unknown)
