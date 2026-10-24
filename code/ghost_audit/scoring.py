"""Local evaluation framework.

There is no official scorer in the repository, so this module implements the metrics the problem statement names
(case-level status accuracy; step-level, field-level and sequence accuracy) against a truth file with the same
schema (``dataset/sample_output.csv`` for the 32 public samples). The weighting the hidden grader uses is unknown; every
metric is therefore reported separately and nothing here is a claim about the official score.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Case
from .schema import PHASES, STEP_FIELDS

KEY_FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")   # step_no is positional


@dataclass
class CaseRow:
    case_id: str
    status: str
    steps: List[dict]


def read_rows(path) -> Dict[str, CaseRow]:
    out: Dict[str, CaseRow] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            out[r["case_id"]] = CaseRow(r["case_id"], r["deletion_status"], json.loads(r["steps_json"] or "[]"))
    return out


def _key(step: dict) -> Tuple:
    return tuple(step.get(k) for k in KEY_FIELDS)


def lcs_len(a: Sequence, b: Sequence) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def prf(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


@dataclass
class Report:
    n_cases: int = 0
    n_missing: int = 0
    status_accuracy: float = 0.0
    confusion: Dict[str, int] = field(default_factory=dict)
    step_precision: float = 0.0
    step_recall: float = 0.0
    step_f1: float = 0.0
    phase_f1: Dict[str, float] = field(default_factory=dict)
    field_accuracy: Dict[str, float] = field(default_factory=dict)       # positional, over truth steps
    field_accuracy_overall: float = 0.0
    evidence_accuracy: float = 0.0                                       # evidence_id agreement on otherwise-matching steps
    sequence_exact_rate: float = 0.0
    sequence_lcs_ratio: float = 0.0
    case_exact_rate: float = 0.0
    per_case: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self, per_case: bool = False) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "per_case"}
        if per_case:
            d["per_case"] = self.per_case
        return d

    def to_markdown(self) -> str:
        pc = lambda x: f"{100 * x:.1f}%"
        lines = [
            f"- cases scored: {self.n_cases} (missing predictions: {self.n_missing})",
            f"- case-level status accuracy: {pc(self.status_accuracy)}  (confusion truth->pred: {self.confusion})",
            f"- step-level precision/recall/F1: {pc(self.step_precision)} / {pc(self.step_recall)} / {pc(self.step_f1)}",
            "- step F1 by phase: " + ", ".join(f"{p}={pc(self.phase_f1.get(p, 0.0))}" for p in PHASES),
            "- field-level accuracy (positional): " + ", ".join(f"{k}={pc(v)}" for k, v in self.field_accuracy.items())
            + f"; overall={pc(self.field_accuracy_overall)}",
            f"- evidence_id agreement on matching steps: {pc(self.evidence_accuracy)}",
            f"- sequence: exact={pc(self.sequence_exact_rate)}, mean LCS ratio={pc(self.sequence_lcs_ratio)}",
            f"- fully correct cases (status + exact sequence): {pc(self.case_exact_rate)}",
        ]
        return "\n".join(lines)


def score(pred: Dict[str, CaseRow], truth: Dict[str, CaseRow], case_ids: Optional[Iterable[str]] = None) -> Report:
    ids = sorted(case_ids if case_ids is not None else truth)
    rep = Report(n_cases=len(ids))
    status_ok = 0
    conf: Counter = Counter()
    tp = fp = fn = 0
    phase_counts = {p: [0, 0, 0] for p in PHASES}
    field_hits: Counter = Counter()
    field_total = 0
    ev_match = ev_total = 0
    seq_exact = 0
    lcs_sum = 0.0
    case_exact = 0

    for cid in ids:
        t = truth[cid]
        p = pred.get(cid)
        if p is None:
            rep.n_missing += 1
            p = CaseRow(cid, "MISSING", [])
        conf[f"{t.status}->{p.status}"] += 1
        s_ok = p.status == t.status
        status_ok += s_ok

        tk = Counter(_key(s) for s in t.steps)
        pk = Counter(_key(s) for s in p.steps)
        c_tp = sum((tk & pk).values())
        c_fp = sum((pk - tk).values())
        c_fn = sum((tk - pk).values())
        tp, fp, fn = tp + c_tp, fp + c_fp, fn + c_fn
        for ph in PHASES:
            tkp = Counter(k for k in tk.elements() if k[0] == ph)
            pkp = Counter(k for k in pk.elements() if k[0] == ph)
            m = sum((tkp & pkp).values())
            phase_counts[ph][0] += m
            phase_counts[ph][1] += sum((pkp - tkp).values())
            phase_counts[ph][2] += sum((tkp - pkp).values())

        for i, ts in enumerate(t.steps):
            ps = p.steps[i] if i < len(p.steps) else {}
            for f in STEP_FIELDS:
                field_hits[f] += ps.get(f) == ts.get(f)
            field_total += 1
        # evidence agreement: steps equal on everything except evidence_id
        p_noev = {_key(s)[:-1]: s.get("evidence_id") for s in p.steps}
        for s in t.steps:
            k = _key(s)[:-1]
            if k in p_noev:
                ev_total += 1
                ev_match += p_noev[k] == s.get("evidence_id")

        t_seq = [_key(s) for s in t.steps]
        p_seq = [_key(s) for s in p.steps]
        exact = t.steps == p.steps
        seq_exact += exact
        denom = len(t_seq) + len(p_seq)
        lcs = 1.0 if denom == 0 else 2 * lcs_len(t_seq, p_seq) / denom
        lcs_sum += lcs
        case_exact += (s_ok and exact)
        rep.per_case[cid] = {"truth": t.status, "pred": p.status, "status_ok": s_ok, "tp": c_tp, "fp": c_fp, "fn": c_fn,
                             "sequence_exact": exact, "lcs": round(lcs, 4)}

    n = max(len(ids), 1)
    rep.status_accuracy = status_ok / n
    rep.confusion = dict(sorted(conf.items()))
    rep.step_precision, rep.step_recall, rep.step_f1 = prf(tp, fp, fn)
    rep.phase_f1 = {ph: prf(*phase_counts[ph])[2] for ph in PHASES}
    rep.field_accuracy = {f: (field_hits[f] / field_total if field_total else 1.0) for f in STEP_FIELDS}
    rep.field_accuracy_overall = (sum(field_hits.values()) / (field_total * len(STEP_FIELDS))) if field_total else 1.0
    rep.evidence_accuracy = ev_match / ev_total if ev_total else 1.0
    rep.sequence_exact_rate = seq_exact / n
    rep.sequence_lcs_ratio = lcs_sum / n
    rep.case_exact_rate = case_exact / n
    return rep


# ---------------------------------------------------------------------------------------------- naive baselines
def baseline_all_incomplete(cases: Iterable[Case]) -> Dict[str, CaseRow]:
    """Say INCOMPLETE with no steps for everything: the status floor on a mostly-incomplete dataset."""
    return {c.case_id: CaseRow(c.case_id, "INCOMPLETE", []) for c in cases}


def baseline_reported_status(cases: Iterable[Case]) -> Dict[str, CaseRow]:
    """Trust the workflow's claim (COMPLETE -> COMPLETE, anything else -> INCOMPLETE). The spec says not to; this
    measures how misleading the claims are."""
    return {c.case_id: CaseRow(c.case_id, "COMPLETE" if c.reported_status == "COMPLETE" else "INCOMPLETE", []) for c in cases}


BASELINES = {"all-incomplete": baseline_all_incomplete, "reported-status": baseline_reported_status}
