"""Official output schema (problem_statement.md / AGENTS.md section 6.3-6.4) and a validator for output rows.

The schema has no confidence, uncertainty or notes field: a row is exactly ``case_id,deletion_status,steps_json`` and
every step has exactly the seven fields below. Uncertainty therefore cannot be *expressed* in the output; it can only
be handled by deciding which steps to include (see the milestone-1 report).
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

OUTPUT_COLUMNS = ["case_id", "deletion_status", "steps_json"]
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")

# (phase, operation) -> required state, in the order the problem statement lists the operations.
OPERATIONS = [
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
OPERATION_STATE = {op: state for _, op, state in OPERATIONS}
OPERATION_PHASE = {op: phase for phase, op, _ in OPERATIONS}
# The statement's ordering rule for the verification table (ABSENT, PRESENT, REDACTED, ...) differs from the order the
# sample outputs use only if the samples contradict it; sort_key follows the statement and the tests check the samples.
OPERATION_RANK = {op: i for i, (_, op, _) in enumerate(OPERATIONS)}
PHASE_RANK = {p: i for i, p in enumerate(PHASES)}


def sort_key(step: dict):
    """Required order inside a plan: phase, operation order, system_id, resource_id."""
    return (PHASE_RANK[step["phase"]], OPERATION_RANK[step["operation"]], step["system_id"], step["resource_id"])


@dataclass
class Problem:
    case_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.case_id}: {self.message}"


def validate_steps(case_id: str, status: str, steps: list) -> List[Problem]:
    out: List[Problem] = []

    def bad(msg: str) -> None:
        out.append(Problem(case_id, msg))

    if status not in STATUSES:
        bad(f"invalid deletion_status {status!r}")
    if status == "NOT_APPLICABLE" and steps:
        bad("NOT_APPLICABLE must have steps_json == []")
    if not isinstance(steps, list):
        bad("steps_json is not a JSON array")
        return out
    last_rank = None
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or set(s) != set(STEP_FIELDS):
            bad(f"step {i}: fields must be exactly {list(STEP_FIELDS)}")
            continue
        if s["step_no"] != i:
            bad(f"step {i}: step_no is {s['step_no']!r}, expected {i}")
        if s["phase"] not in PHASES:
            bad(f"step {i}: invalid phase {s['phase']!r}")
            continue
        if s["operation"] not in OPERATION_STATE:
            bad(f"step {i}: invalid operation {s['operation']!r}")
            continue
        if OPERATION_PHASE[s["operation"]] != s["phase"]:
            bad(f"step {i}: operation {s['operation']} does not belong to phase {s['phase']}")
        if OPERATION_STATE[s["operation"]] != s["state"]:
            bad(f"step {i}: state {s['state']!r} invalid for {s['operation']} (expected {OPERATION_STATE[s['operation']]})")
        for k in ("system_id", "resource_id"):
            if not isinstance(s[k], str) or not s[k]:
                bad(f"step {i}: {k} must be a non-empty string")
        if not isinstance(s["evidence_id"], str):
            bad(f"step {i}: evidence_id must be a string")
        elif s["phase"] != "FINDING" and status == "INCOMPLETE" and s["phase"] in ("REMEDIATION", "VERIFICATION") and s["evidence_id"]:
            bad(f"step {i}: {s['phase']} step of an INCOMPLETE case must use an empty evidence_id")
        elif s["phase"] == "FINDING" and not s["evidence_id"]:
            bad(f"step {i}: findings must cite evidence")
        elif status == "COMPLETE" and not s["evidence_id"]:
            bad(f"step {i}: COMPLETE verification steps must cite evidence")
        if status == "COMPLETE" and s["phase"] != "VERIFICATION":
            bad(f"step {i}: COMPLETE cases contain only VERIFICATION steps")
        rank = PHASE_RANK[s["phase"]]
        if last_rank is not None and rank < last_rank:
            bad(f"step {i}: phases must run FINDING -> REMEDIATION -> VERIFICATION")
        last_rank = rank if last_rank is None else max(last_rank, rank)
    # within the plan: operation order of the specification, then system_id, then resource_id; no repeated step
    well_formed = [s for s in steps if isinstance(s, dict) and set(s) == set(STEP_FIELDS) and s.get("phase") in PHASE_RANK
                   and s.get("operation") in OPERATION_RANK and isinstance(s.get("system_id"), str) and isinstance(s.get("resource_id"), str)]
    if [sort_key(s) for s in well_formed] != sorted(sort_key(s) for s in well_formed):
        bad("steps are not ordered by phase, operation order, system_id, resource_id")
    seen = [(s["phase"], s["operation"], s["system_id"], s["resource_id"]) for s in well_formed]
    if len(seen) != len(set(seen)):
        bad("the same step appears more than once")
    return out


def _natural(case_id: str):
    head, _, tail = case_id.rpartition("_")
    return (head, int(tail)) if tail.isdigit() else (case_id, -1)


def validate_rows(rows: Iterable[dict], expected_case_ids: Iterable[str] = (), order: Optional[str] = None) -> List[Problem]:
    """``order``: 'string' | 'numeric' | None (accept either; the specification says only "sorted by case_id")."""
    problems: List[Problem] = []
    seen = []
    for r in rows:
        cid = r.get("case_id", "?")
        seen.append(cid)
        try:
            steps = json.loads(r.get("steps_json", ""))
        except ValueError:
            problems.append(Problem(cid, "steps_json is not valid JSON"))
            continue
        problems.extend(validate_steps(cid, r.get("deletion_status", ""), steps))
    if len(seen) != len(set(seen)):
        problems.append(Problem("*", "duplicate case_id rows"))
    ok_string, ok_numeric = seen == sorted(seen), seen == sorted(seen, key=_natural)
    if not {"string": ok_string, "numeric": ok_numeric, None: ok_string or ok_numeric}[order]:
        problems.append(Problem("*", f"rows are not sorted by case_id ({order or 'string or numeric'} order)"))
    exp = set(expected_case_ids)
    if exp:
        for cid in sorted(exp - set(seen)):
            problems.append(Problem(cid, "missing row"))
        for cid in sorted(set(seen) - exp):
            problems.append(Problem(cid, "unexpected row"))
    return problems


def validate_csv(path: Path, expected_case_ids: Iterable[str] = ()) -> List[Problem]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != OUTPUT_COLUMNS:
            return [Problem("*", f"header must be exactly {OUTPUT_COLUMNS}, got {reader.fieldnames}")]
        return validate_rows(list(reader), expected_case_ids)
