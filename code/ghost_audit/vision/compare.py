"""Diagnostics: the pipeline with screenshot interpretation disabled vs enabled (fake or real interpretations).

Only *changed* cases are reported, and only as step differences; no prediction file is produced.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..context import build_context
from ..loaders import load_manifest, load_requests
from ..pipeline import Pipeline, rows_for
from ..planner import CasePlan
from ..scoring import read_rows, score
from ..paths import DATASET_DIR
from .schema import ImageInterpretation

_KEY = ("phase", "operation", "system_id", "resource_id", "evidence_id")


def _plans(interpretations: Optional[Dict[str, ImageInterpretation]], dataset_dir=None) -> Dict[str, CasePlan]:
    ctx = build_context(dataset_dir, interpretations=interpretations)
    cases = load_requests(dataset_dir, sample=True) + load_requests(dataset_dir)
    return Pipeline(ctx).run(cases)


def compare(interpretations: Dict[str, ImageInterpretation], dataset_dir=None) -> dict:
    off = _plans(None, dataset_dir)
    on = _plans(interpretations, dataset_dir)
    sample_ids = {c.case_id for c in load_requests(dataset_dir, sample=True)}
    changes: List[dict] = []
    for cid in sorted(on, key=lambda c: (c not in sample_ids, c)):
        a, b = off[cid], on[cid]
        ka = {tuple(s[k] for k in _KEY) for s in a.steps}
        kb = {tuple(s[k] for k in _KEY) for s in b.steps}
        if a.status != b.status or ka != kb:
            changes.append({"case_id": cid, "sample": cid in sample_ids, "status": (a.status, b.status),
                            "added": sorted(kb - ka), "removed": sorted(ka - kb),
                            "flags_on": [f for f in b.flags if f.startswith(("ambiguous", "no_account", "insufficient"))],
                            "notes": [n for n in b.notes if "screenshot" in n or "image" in n or "disput" in n.lower()]})
    truth = read_rows(DATASET_DIR / "sample_output.csv")
    pick = lambda plans: {k: v for k, v in rows_for(plans).items() if k in sample_ids}
    return {"changes": changes,
            "sample_score_off": score(pick(off), truth).to_dict(),
            "sample_score_on": score(pick(on), truth).to_dict(),
            "counts": {"cases": len(on), "changed": len(changes)}}


def format_comparison(result: dict) -> str:
    lines = [f"cases compared: {result['counts']['cases']}; changed by screenshot interpretation: {result['counts']['changed']}"]
    for c in result["changes"]:
        arrow = c["status"][0] if c["status"][0] == c["status"][1] else f"{c['status'][0]} -> {c['status'][1]}"
        lines.append(f"{c['case_id']}{' (sample)' if c['sample'] else ''}: status {arrow}")
        for k in c["added"]:
            lines.append(f"    + {k[0][:4]} {k[1]} {k[2]}/{k[3]} ev={k[4] or '-'}")
        for k in c["removed"]:
            lines.append(f"    - {k[0][:4]} {k[1]} {k[2]}/{k[3]} ev={k[4] or '-'}")
    off, on = result["sample_score_off"], result["sample_score_on"]
    lines.append("sample score (disabled -> enabled): " + ", ".join(
        f"{k} {off[k]:.3f}->{on[k]:.3f}" for k in ("status_accuracy", "step_precision", "step_recall", "step_f1", "evidence_accuracy", "sequence_exact_rate")))
    return "\n".join(lines)
