"""Shared fixtures: synthetic identities/evidence so unit tests never depend on the dataset."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ghost_audit.identity import IdentityResolver, sha256_email
from ghost_audit.models import Activity, AuditChange, Case, EvidenceMeta, Fact, IdentityRecord, Rule, Scope
from ghost_audit.paths import DATASET_DIR

HAVE_DATASET = (DATASET_DIR / "evidence_files.csv").exists() and (DATASET_DIR / "sample_output.csv").exists()
needs_dataset = unittest.skipUnless(HAVE_DATASET, "participant dataset not present (it is excluded from code.zip)")


def identities():
    """ana: current ana.new@x.com, previously ana.old@x.com. bob: bob@x.com. cy: cy@x.com."""
    return [
        IdentityRecord("user_1", "Ana A", "ana.new@x.com", "+1 555 0100", sha256_email("ana.new@x.com"), "2026-05-01T00:00:00Z"),
        IdentityRecord("user_2", "Bob B", "bob@x.com", "+1 555 0200", sha256_email("bob@x.com"), "2026-01-01T00:00:00Z"),
        IdentityRecord("user_3", "Cy C", "cy@x.com", "+1 555 0300", sha256_email("cy@x.com"), "2026-01-01T00:00:00Z"),
    ]


def changes():
    return [
        AuditChange("audit_1", "user_1", "2026-03-01T00:00:00Z", "email", "ana.old@x.com", "ana.new@x.com"),
        AuditChange("audit_2", "user_1", "2026-03-01T00:00:00Z", "email_hash", sha256_email("ana.old@x.com"), sha256_email("ana.new@x.com")),
        AuditChange("audit_3", "user_1", "2026-04-01T00:00:00Z", "phone", "+1 555 0099", "+1 555 0100"),
    ]


def resolver() -> IdentityResolver:
    return IdentityResolver(identities(), changes())


def case(case_id, email, reported_at="2026-09-01T12:00:00Z", audit_at="2026-09-03T12:00:00Z", status="COMPLETE") -> Case:
    return Case(case_id, email, "2026-08-30T12:00:00Z", status, reported_at, audit_at, "Please delete my account.")


def submit(activity_id, resource, event_time, result="SUCCESS") -> Activity:
    return Activity(activity_id, event_time, "DELETE_RECORD", "deletion_worker", "account_service", resource, result,
                    f"Deletion worker submitted primary profile {resource} to account_service.")


def link_activity(activity_id, resource, email, user, event_time, label=None) -> Activity:
    label = label or resource
    return Activity(activity_id, event_time, "LINK_RESOURCE_IDENTITY", "data_catalog", "account_service", resource, "SUCCESS",
                    f"Data catalog linked {resource} ({label}) to {email} and {user}.")


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def meta(self, name: str, content, file_id="evidence_t", captured_at="2026-09-01T00:00:00Z") -> EvidenceMeta:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return EvidenceMeta(file_id, f"evidence/{name}", path, "test", captured_at)


def minimal_pdf(lines) -> bytes:
    """A tiny valid one-page PDF whose text layer is ``lines`` (Helvetica)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 11 Tf", "50 750 Td", "14 TL"]
    for ln in lines:
        ops.append(f"({esc(ln)}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# --------------------------------------------------------------------------- milestone 2 fixtures
RULES = [Rule(*r) for r in [
    ("rule_01", "USER_PROFILE", "Always", "DELETE"),
    ("rule_02", "SECONDARY_IDENTIFIER", "Always", "DELETE"),
    ("rule_03", "SEARCH_INDEX_ENTRY", "Always", "DELETE"),
    ("rule_04", "CACHE_ENTRY", "Always", "DELETE"),
    ("rule_05", "SUPPORT_MESSAGE", "Conversation contains shared customer, agent, or order context that must be retained", "REDACT"),
    ("rule_06", "SUPPORT_ATTACHMENT", "Attachment belongs only to the deletion subject", "DELETE"),
    ("rule_07", "ANALYTICS_PROFILE", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_08", "PRODUCT_TELEMETRY", "Record is still attributable to a user", "ANONYMIZE"),
    ("rule_09", "USER_EXPORT", "Always", "DELETE"),
    ("rule_10", "MARKETING_PROFILE", "Always", "DELETE"),
    ("rule_11", "EMAIL_DELIVERY_LOG", "Log contains a user email address", "REDACT"),
    ("rule_12", "SECURITY_AUDIT_LOG", "Record is within the supplied retention period", "RETAIN"),
    ("rule_13", "LEGAL_HOLD_RECORD", "A supplied legal hold is active", "RETAIN"),
    ("rule_14", "BACKUP_ARCHIVE", "Restore access can be blocked", "RETAIN"),
    ("rule_15", "ACCOUNT_TOMBSTONE", "Account is deleted and only a stable internal user_id is needed to preserve references to retained records", "REDACT"),
]]


def F(file_id, rid, state="PRESENT", captured="2026-09-02T00:00:00Z", observed=None, category=None, system=None, emails=(), refs=(),
      role=None, **attrs) -> Fact:
    """A synthetic Fact. Identifiers (emails / user refs) become part of the fact so the owner map can link it."""
    from ghost_audit.taxonomy import category_for_resource, system_for_resource
    from ghost_audit.text_ids import Identifiers
    return Fact(file_id, captured, system or system_for_resource(rid), rid, category or category_for_resource(rid), state,
                Identifiers(frozenset(emails), frozenset(refs)), observed_at=observed, source_role=role, attrs=dict(attrs))


def S(file_id, system, collection, captured="2026-09-02T00:00:00Z", role=None, subject_scoped=False, observed=None) -> Scope:
    return Scope(file_id, captured, system, collection, role, observed, "", subject_scoped)


def make_pipeline(facts=(), activities=(), scopes=(), **kw):
    from ghost_audit.context import Context
    from ghost_audit.owners import build_owner_map
    from ghost_audit.pipeline import Pipeline
    r = resolver()
    facts, activities = list(facts), list(activities)
    ctx = Context(Path("."), [], {}, facts, activities, RULES, r, build_owner_map(facts, activities, r))
    return Pipeline(ctx, scopes=list(scopes), **kw)


def deletion_case(case_id="c1", email="bob@x.com", reported_at="2026-09-01T12:00:00Z", audit_at="2026-09-03T12:00:00Z", status="COMPLETE",
                  text="Please delete my account and all my personal data."):
    from ghost_audit.models import Case
    return Case(case_id, email, "2026-08-30T12:00:00Z", status, reported_at, audit_at, text)
