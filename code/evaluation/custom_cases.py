"""Additional evaluation cases written by the participant (not part of the official samples).

Each case is a small synthetic world (facts, activity rows, snapshot scopes, a request) plus the output that the
*specification* requires, written by hand BEFORE looking at what the pipeline produces. The runner compares the pipeline's
steps to those expectations exactly (phase, operation, system, resource, evidence_id, in order).

    python code/evaluation/main.py --custom
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost_audit.models import Activity  # noqa: E402
from tests.support import F, S, deletion_case, make_pipeline, submit  # noqa: E402

Step = Tuple[str, str, str, str, str]       # phase, operation, system_id, resource_id, evidence_id
BOB = ["bob@x.com"]
RET = "2031-12-31T23:59:59Z"


@dataclass
class CustomCase:
    name: str
    purpose: str
    build: Callable[[], Dict[str, object]]            # returns {case_id: CasePlan}
    expected: Dict[str, Tuple[str, List[Step]]]       # case_id -> (status, steps)


def _plans(facts=(), acts=(), scopes=(), cases=None):
    pipe = make_pipeline(facts, acts, scopes)
    return pipe.run(cases or [deletion_case()])


# ------------------------------------------------------------------------------------------------ 1
def _c1():
    facts = [
        F("db", "account_1", role="failover_replica", emails=BOB, category="USER_PROFILE", system="account_service"),
        F("e1", "data_flow_1", emails=BOB, enabled=True),
        F("e1", "backup_1", emails=BOB, restore_allowed=True),
        F("e1", "search_1", state="NOT_FOUND", emails=BOB),
        F("e1", "audit_record_1", refs=["user_2"], retention_until=RET),
    ]
    scopes = [S("db", "account_service", "customer_profiles_primary", role="primary"),
              S("db", "account_service", "customer_profiles_failover_replica", role="failover_replica")]
    return _plans(facts, [submit("a1", "account_1", "2026-09-01T11:00:00Z")], scopes)


C1 = CustomCase(
    "replica survivor, active data flow and restorable backup",
    "A record deleted from the primary but still on the failover replica is a surviving record; an enabled connector and a "
    "restorable archive are findings; the archive is retained (restore blocked, not deleted); the retained audit record is "
    "verified present; remediation follows the operation order DISABLE -> BLOCK -> DELETE.",
    _c1,
    {"c1": ("INCOMPLETE", [
        ("FINDING", "REPORT_REMAINING_RECORD", "account_service", "account_1", "db"),
        ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "integration_service", "data_flow_1", "e1"),
        ("FINDING", "REPORT_RESTORE_RISK", "backup_service", "backup_1", "e1"),
        ("REMEDIATION", "DISABLE_DATA_FLOW", "integration_service", "data_flow_1", ""),
        ("REMEDIATION", "BLOCK_RESTORE", "backup_service", "backup_1", ""),
        ("REMEDIATION", "DELETE_RECORD", "account_service", "account_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_service", "account_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_PRESENT", "security_service", "audit_record_1", ""),
        ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "integration_service", "data_flow_1", ""),
        ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "backup_service", "backup_1", ""),
    ])},
)


# ------------------------------------------------------------------------------------------------ 2
def _c2():
    facts = [
        F("early", "search_1", state="NOT_FOUND", emails=BOB, captured="2026-09-02T00:00:00Z"),
        F("early", "audit_record_1", refs=["user_2"], retention_until=RET, captured="2026-09-02T00:00:00Z"),
        F("later", "search_1", emails=BOB, captured="2026-09-09T00:00:00Z"),          # captured after audit_at: must not matter
    ]
    return _plans(facts, cases=[deletion_case(audit_at="2026-09-03T12:00:00Z", status="NOT_STARTED")])


C2 = CustomCase(
    "evidence captured after audit_at must not change the answer",
    "The search entry was absent at audit time and reappeared in a later capture. The later file is inadmissible; the case is "
    "COMPLETE and cites only the earlier file. The workflow's NOT_STARTED claim must not matter.",
    _c2,
    {"c1": ("COMPLETE", [
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_1", "early"),
        ("VERIFICATION", "VERIFY_RECORD_PRESENT", "security_service", "audit_record_1", "early"),
    ])},
)


# ------------------------------------------------------------------------------------------------ 3
def _c3():
    facts = [
        F("e1", "account_1", state="NOT_FOUND", emails=["ana.new@x.com"], category="USER_PROFILE", system="account_service"),
        F("e1", "tombstone_1", refs=["user_1"], subject_reference="user_1", name="Ana A", email=None, phone=None, email_hash=None),
        F("e1", "audit_record_1", refs=["user_1"], retention_until=RET),
    ]
    return _plans(facts, cases=[deletion_case(email="ana.old@x.com")])


C3 = CustomCase(
    "historical e-mail and an exposed account tombstone",
    "The request uses the person's OLD e-mail. The tombstone must keep only the stable reference, so its visible name is a "
    "finding to REDACT (not delete); the absent account and the retained audit record are verified.",
    _c3,
    {"c1": ("INCOMPLETE", [
        ("FINDING", "REPORT_EXPOSED_PII", "identity_service", "tombstone_1", "e1"),
        ("REMEDIATION", "REDACT_RECORD", "identity_service", "tombstone_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_service", "account_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_PRESENT", "security_service", "audit_record_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_REDACTED", "identity_service", "tombstone_1", ""),
    ])},
)


# ------------------------------------------------------------------------------------------------ 4
def _c4():
    facts = [F("e1", "search_1", emails=BOB), F("e1", "account_1", emails=BOB, category="USER_PROFILE", system="account_service")]
    return _plans(facts, [submit("a1", "account_1", "2026-09-01T11:00:00Z")],
                  cases=[deletion_case(text="Please stop the promotional emails. I still need the account, so I am not asking for deletion.", status="COMPLETE")])


C4 = CustomCase(
    "marketing opt-out is not a deletion request",
    "Live resources exist and the workflow says COMPLETE, but the person explicitly does not ask for deletion: "
    "NOT_APPLICABLE with an empty steps array.",
    _c4,
    {"c1": ("NOT_APPLICABLE", [])},
)


# ------------------------------------------------------------------------------------------------ 5
def _c5():
    acts = [submit("a1", "account_7", "2026-09-01T11:00:00Z"), submit("a2", "account_8", "2026-09-01T11:10:00Z")]
    facts = [F("e1", "search_1", emails=BOB), F("e1", "audit_record_2", refs=["user_3"], retention_until=RET)]
    scopes = [S("snap", "account_service", "customer_profiles", role="inventory")]
    cases = [deletion_case("c1", "bob@x.com"), deletion_case("c2", "cy@x.com")]
    return _plans(facts, acts, scopes, cases)


C5 = CustomCase(
    "two people, two candidate accounts: do not guess, keep processing what is confirmed",
    "Both requests could own either account (same reported_at, two candidate deletions). No account step may be asserted for "
    "either, but each person's independently confirmed resources are still processed.",
    _c5,
    {"c1": ("INCOMPLETE", [
        ("FINDING", "REPORT_REMAINING_RECORD", "search_service", "search_1", "e1"),
        ("REMEDIATION", "DELETE_RECORD", "search_service", "search_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_1", ""),
    ]),
     "c2": ("COMPLETE", [("VERIFICATION", "VERIFY_RECORD_PRESENT", "security_service", "audit_record_2", "e1")])},
)


# ------------------------------------------------------------------------------------------------ 6
def _c6():
    claim = Activity("activity_9", "2026-09-01T10:00:00Z", "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "SUCCESS",
                     "Deletion worker reported the requested operation as completed.")
    return _plans([F("e1", "search_1", emails=BOB)], [claim], cases=[deletion_case(status="COMPLETE")])


C6 = CustomCase(
    "a SUCCESS workflow claim is contradicted by the evidence",
    "The deletion worker reports success and the request is marked COMPLETE, but the snapshot still holds the search entry.",
    _c6,
    {"c1": ("INCOMPLETE", [
        ("FINDING", "REPORT_REMAINING_RECORD", "search_service", "search_1", "e1"),
        ("REMEDIATION", "DELETE_RECORD", "search_service", "search_1", ""),
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_1", ""),
    ])},
)


# ------------------------------------------------------------------------------------------------ 7
def _c7():
    facts = [
        F("e1", "legal_hold_1", refs=["user_2"], retained_until="2033-12-31T23:59:59Z"),
        F("e1", "audit_record_1", refs=["user_2"], retention_until="2026-01-01T00:00:00Z"),      # retention ended before audit_at
        F("e1", "search_1", state="NOT_FOUND", emails=BOB),
    ]
    return _plans(facts)


C7 = CustomCase(
    "active legal hold is retained; an expired retention period gets no invented treatment",
    "The legal-hold record must remain (verify present). The audit record's retention ended before audit_at, and the "
    "specification does not say what applies then, so no step is generated for it.",
    _c7,
    {"c1": ("COMPLETE", [
        ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_service", "search_1", "e1"),
        ("VERIFICATION", "VERIFY_RECORD_PRESENT", "legal_service", "legal_hold_1", "e1"),
    ])},
)

CASES: List[CustomCase] = [C1, C2, C3, C4, C5, C6, C7]


@dataclass
class CustomResult:
    name: str
    purpose: str
    passed: bool
    detail: List[str]
    expected: Dict[str, Tuple[str, List[Step]]]
    actual: Dict[str, Tuple[str, List[Step]]]


def _steps(plan) -> List[Step]:
    return [(s["phase"], s["operation"], s["system_id"], s["resource_id"], s["evidence_id"]) for s in plan.steps]


def run_custom_cases(cases: Optional[List[CustomCase]] = None) -> List[CustomResult]:
    out = []
    for c in (cases or CASES):
        plans = c.build()
        actual = {cid: (p.status, _steps(p)) for cid, p in plans.items()}
        detail = []
        for cid, (status, steps) in c.expected.items():
            got_status, got_steps = actual.get(cid, ("<missing>", []))
            if got_status != status:
                detail.append(f"{cid}: status {got_status}, expected {status}")
            if got_steps != steps:
                detail.append(f"{cid}: steps differ (expected {len(steps)}, got {len(got_steps)})")
        out.append(CustomResult(c.name, c.purpose, not detail, detail, c.expected, actual))
    return out
