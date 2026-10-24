"""Build the ordered findings / remediation / verification plan for one case, and decide its status.

Rules implemented here:

* Status comes from the evidence-backed assessments, never from ``reported_status`` (the planner does not read it).
* ``NOT_APPLICABLE`` -> status NOT_APPLICABLE and ``[]``.
* INCOMPLETE if any assessed resource violates policy or any active flow / restorable backup exists; otherwise COMPLETE.
  When nothing could be assessed, completeness cannot be proven, so the case is INCOMPLETE and flagged
  ``insufficient_evidence``.
* Findings cite the deciding evidence file. Remediation and verification steps of an INCOMPLETE case are planned future
  work and carry an empty ``evidence_id``. A COMPLETE case contains only verification steps, each citing the evidence
  that proves the compliant state.
* Account steps are only produced for CONFIRMED or PROBABLE attributions. AMBIGUOUS candidates are never asserted, and
  they never block processing of independently confirmed resources.
* Ordering: phase, then the operation order of the specification, then ``system_id``, then ``resource_id``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .attribution import AMBIGUOUS, CONFIRMED, PROBABLE, AccountAttribution
from .intent import Intent
from .models import Case
from .observations import UNKNOWN
from .policy import Assessment, Context, PolicyEngine
from .schema import OPERATION_STATE, STEP_FIELDS, sort_key, validate_steps
from .state import ResourceState


@dataclass
class PlanItem:
    state: ResourceState
    assessment: Assessment


@dataclass
class CasePlan:
    case_id: str
    status: str
    steps: List[dict] = field(default_factory=list)
    intent: Optional[Intent] = None
    attribution: Optional[AccountAttribution] = None
    items: List[PlanItem] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    candidate_states: Dict[str, str] = field(default_factory=dict)     # ambiguous account candidates -> reconstructed kind

    def to_row(self) -> dict:
        ordered = [{k: s[k] for k in STEP_FIELDS} for s in self.steps]
        return {"case_id": self.case_id, "deletion_status": self.status,
                "steps_json": json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))}


def _step(phase: str, system_id: str, resource_id: str, op: str, evidence_id: str) -> dict:
    return {"step_no": 0, "phase": phase, "system_id": system_id, "resource_id": resource_id, "operation": op,
            "state": OPERATION_STATE[op], "evidence_id": evidence_id}


def assemble(items: List[PlanItem], account_only_verify: List[ResourceState], complete: bool,
             planned_verify: Optional[List[tuple]] = None) -> List[dict]:
    """Findings + remediation + verification for the assessed items, sorted and numbered."""
    steps: List[dict] = []
    for it in items:
        a, st = it.assessment, it.state
        system = st.system_id or ""
        if not a.acted:
            continue
        if a.violation:
            steps.append(_step("FINDING", system, st.resource_id, a.finding_op, st.evidence_id))
            steps.append(_step("REMEDIATION", system, st.resource_id, a.remediation_op, ""))
        if a.verification_op:
            if complete and not st.evidence_id:
                continue                      # cannot cite proof, so a COMPLETE case may not assert it
            steps.append(_step("VERIFICATION", system, st.resource_id, a.verification_op, st.evidence_id if complete else ""))
    for st in account_only_verify:            # asserted account whose state is unobserved: a future check, INCOMPLETE cases only
        steps.append(_step("VERIFICATION", st.system_id or "account_service", st.resource_id, "VERIFY_RECORD_ABSENT", ""))
    for st, op in (planned_verify or []):    # owned resources whose current state is unobserved: planned checks, uncited
        steps.append(_step("VERIFICATION", st.system_id or "", st.resource_id, op, ""))
    steps.sort(key=sort_key)
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return steps


def plan_case(case: Case, intent: Intent, attribution: Optional[AccountAttribution], states: List[ResourceState],
              engine: PolicyEngine, owners_for_attachment=None, candidate_states: Optional[Dict[str, str]] = None) -> CasePlan:
    plan = CasePlan(case.case_id, "NOT_APPLICABLE", intent=intent, attribution=attribution)
    if not intent.authorizes_deletion:
        plan.notes.append("request does not authorize deletion: " + "; ".join(intent.reasons))
        return plan

    asserted = set(attribution.accounts) if attribution and attribution.status in (CONFIRMED, PROBABLE) else set()
    items: List[PlanItem] = []
    unobserved_accounts: List[ResourceState] = []
    planned: List[tuple] = []
    for st in states:
        if st.kind == UNKNOWN:
            if st.resource_id in asserted:
                unobserved_accounts.append(st)
            else:
                op = engine.final_verification(st.category)
                if op:
                    planned.append((st, op))
                    plan.notes.append(f"{st.resource_id}: owned but state unobserved at audit_at; final-state check planned only")
            continue
        ctx = Context(audit_at=case.audit_at, retention_until=st.retention_until)
        if st.category == "SUPPORT_ATTACHMENT" and owners_for_attachment is not None:
            users = owners_for_attachment(st.resource_id)
            ctx.sole_owner = None if not users else len(users) == 1
        a = engine.assess(st.category, st.kind, ctx)
        if a.unknown and not a.acted:
            op = engine.final_verification(st.category)
            if op:
                planned.append((st, op))
                plan.notes.append(f"{st.resource_id}: observed but its compliance state is not visible; final-state check planned only")
        items.append(PlanItem(st, a))
        for n in a.notes:
            plan.notes.append(f"{st.resource_id}: {n}")
        if "conflict" in st.flags:
            plan.flags.append(f"conflict:{st.resource_id}")
        if "replica_drift" in st.flags:
            plan.flags.append(f"replica_drift:{st.resource_id}")
    plan.items = items

    acted = [i for i in items if i.assessment.acted]
    violation = any(i.assessment.violation for i in acted)
    if violation:
        plan.status = "INCOMPLETE"
    elif acted:
        plan.status = "COMPLETE"
    else:
        plan.status = "INCOMPLETE"
        plan.flags.append("insufficient_evidence")
        plan.notes.append("no resource could be assessed from admissible evidence; completeness cannot be proven")

    complete = plan.status == "COMPLETE"
    plan.steps = assemble(items, [] if complete else unobserved_accounts, complete, [] if complete else planned)

    if attribution is not None:
        if attribution.status == AMBIGUOUS:
            plan.flags.append("ambiguous_account")
            plan.candidate_states = dict(candidate_states or {})
            if any(k not in ("ABSENT", "UNKNOWN") for k in plan.candidate_states.values()):
                plan.flags.append("status_at_risk")
                plan.notes.append("an ambiguous account candidate is not absent; the account's own state could change the status")
        elif attribution.status == "NONE":
            plan.flags.append("no_account_attributed")
    problems = validate_steps(case.case_id, plan.status, plan.steps)
    for p in problems:
        plan.flags.append("schema_problem")
        plan.notes.append(str(p))
    return plan
