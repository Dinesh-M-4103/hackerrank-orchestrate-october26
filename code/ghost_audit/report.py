"""Human-readable diagnostics: per-case mismatches against a truth file, and the effect of ambiguous attribution."""
from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List

from .planner import CasePlan
from .scoring import CaseRow

_KEY = ("phase", "system_id", "resource_id", "operation", "evidence_id")


def _k(s: dict) -> tuple:
    return tuple(s[k] for k in _KEY)


def classify_missing(plan: CasePlan, step: dict) -> str:
    """Best-effort cause of a step the plan lacks, for the mismatch report (not used by the pipeline)."""
    att = plan.attribution
    if step["system_id"] == "account_service":
        if att is None:
            return "account: no attribution"
        return f"account not attributed ({att.status}/{att.basis})"
    if step["operation"] == "VERIFY_RECORD_ANONYMIZED":
        return "anonymized record carries no identifier; ownership not derivable from admissible evidence"
    if step["operation"] == "VERIFY_RECORD_PRESENT" and step["system_id"] == "security_service":
        return "retained audit record not discoverable from admissible evidence"
    return "not derivable from admissible evidence"


def mismatches(plans: Dict[str, CasePlan], pred: Dict[str, CaseRow], truth: Dict[str, CaseRow]) -> List[dict]:
    out = []
    for cid in sorted(truth):
        t, p = truth[cid], pred.get(cid)
        if p is None:
            out.append({"case_id": cid, "status": (t.status, None), "missing": [], "extra": [], "causes": ["no prediction"]})
            continue
        ts, ps = {_k(s): s for s in t.steps}, {_k(s): s for s in p.steps}
        missing = [ts[k] for k in sorted(set(ts) - set(ps))]
        extra = [ps[k] for k in sorted(set(ps) - set(ts))]
        if p.status == t.status and not missing and not extra and t.steps == p.steps:
            continue
        causes = Counter(classify_missing(plans[cid], s) for s in missing)
        out.append({"case_id": cid, "status": (t.status, p.status), "missing": missing, "extra": extra,
                    "causes": [f"{n}x {c}" for c, n in sorted(causes.items())], "flags": list(plans[cid].flags)})
    return out


def format_mismatches(items: Iterable[dict]) -> str:
    lines = []
    for m in items:
        flag = "" if m["status"][0] == m["status"][1] else "   <-- STATUS DIFFERS"
        lines.append(f"{m['case_id']}: truth={m['status'][0]} pred={m['status'][1]}{flag}")
        for s in m["missing"]:
            lines.append(f"    missing  {s['phase'][:4]} {s['operation']} {s['system_id']}/{s['resource_id']} ev={s['evidence_id'] or '-'}")
        for s in m["extra"]:
            lines.append(f"    EXTRA    {s['phase'][:4]} {s['operation']} {s['system_id']}/{s['resource_id']} ev={s['evidence_id'] or '-'}")
        for c in m["causes"]:
            lines.append(f"    cause: {c}")
    return "\n".join(lines)


def ambiguity_effect(plans: Dict[str, CasePlan]) -> dict:
    """Do ambiguous account candidates share a state, and can ambiguity change the status? (aggregate only)"""
    amb = [p for p in plans.values() if p.attribution is not None and p.attribution.status == "AMBIGUOUS"]
    shared = Counter()
    for p in amb:
        kinds = set(p.candidate_states.values())
        shared["all_candidates_same_state" if len(kinds) == 1 else "candidates_differ"] += 1
        for k in kinds:
            shared[f"state:{k}"] += 1
    return {
        "ambiguous_cases": len(amb),
        **dict(sorted(shared.items())),
        "status_at_risk": sum("status_at_risk" in p.flags for p in amb),
        "insufficient_evidence": sum("insufficient_evidence" in p.flags for p in amb),
    }
