"""Produce and independently validate the submission ``output.csv``.

The run uses only the deterministic local pipeline (strict audit cutoff, no model calls, no screenshot interpretation).
``check_output`` re-reads the written CSV and verifies it against the participant-facing data, not against the pipeline's
own objects, so a serialization or logic slip cannot hide.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .context import build_context
from .extractors import IMAGE_SUFFIXES
from .intent import DELETION, classify_intent
from .loaders import load_audit_log, load_manifest, load_requests
from .paths import DATASET_DIR
from .pipeline import Pipeline
from .planner import CasePlan
from .schema import OUTPUT_COLUMNS, STATUSES, validate_rows

PathLike = Union[str, Path]


def run_pipeline(dataset_dir: Optional[PathLike] = None, screenshots: str = "transcribed") -> Dict[str, CasePlan]:
    """Plans for every request in deletion_requests.csv: strict cutoff, deterministic, no model calls.

    ``screenshots='transcribed'`` adds the committed, hash-checked transcription of the 11 PNG screenshots (validated like model
    output); ``'none'`` ignores screenshots entirely."""
    base = Path(dataset_dir) if dataset_dir is not None else DATASET_DIR
    interpretations = None
    if screenshots == "transcribed":
        from .vision.transcribed import load_transcribed_interpretations
        interpretations = load_transcribed_interpretations(load_manifest(base))
    return Pipeline(build_context(base, interpretations=interpretations)).run(load_requests(base))


def case_sort_key(case_id: str):
    head, _, tail = case_id.rpartition("_")
    return (head, int(tail)) if tail.isdigit() else (case_id, -1)


def write_output(plans: Dict[str, CasePlan], path: PathLike, order_mode: str = "string") -> List[str]:
    """Write one row per case. ``order_mode``: 'string' (plain string order, a literal reading of "sorted by case_id") or
    'numeric' (case_33 before case_100, the order of every organizer-produced file). Returns the order used."""
    order = sorted(plans) if order_mode == "string" else sorted(plans, key=case_sort_key)
    with open(path, "w", newline="", encoding="utf-8") as fh:        # csv default terminator is CRLF, like sample_output.csv
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        for cid in order:
            w.writerow(plans[cid].to_row())
    return order


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True


@dataclass
class CheckReport:
    checks: List[Check] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str = "", critical: bool = True) -> None:
        self.checks.append(Check(name, ok, detail, critical))

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.critical)

    def to_markdown(self) -> str:
        lines = ["| check | result | detail |", "|---|---|---|"]
        for c in self.checks:
            res = "PASS" if c.ok else ("FAIL" if c.critical else "WARN")
            lines.append(f"| {c.name} | {res} | {c.detail} |")
        return "\n".join(lines)


def check_output(path: PathLike, dataset_dir: Optional[PathLike] = None, order: Optional[str] = None) -> CheckReport:
    base = Path(dataset_dir) if dataset_dir is not None else DATASET_DIR
    rep = CheckReport()
    requests = load_requests(base)
    by_case = {c.case_id: c for c in requests}
    manifest = {m.file_id: m for m in load_manifest(base)}
    activity_times = {}
    with open(base / "system_activity.csv", newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            activity_times[r["activity_id"]] = r["event_time"]
    audit_times = {a.audit_id: a.event_time for a in load_audit_log(base)}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    rep.add("header is exactly case_id,deletion_status,steps_json", header == OUTPUT_COLUMNS, str(header))
    if header != OUTPUT_COLUMNS:          # the rows cannot be interpreted; report the failure instead of crashing on a missing column
        rep.stats = {"rows": len(rows), "statuses": {}, "steps_total": 0, "citations": 0, "incomplete_without_findings": [],
                     "steps_by_phase": {}, "steps_by_operation": {}}
        return rep
    rep.add("row count equals deletion_requests.csv", len(rows) == len(requests), f"{len(rows)} rows, {len(requests)} requests")
    ids = [r["case_id"] for r in rows]
    rep.add("every request has exactly one row, no extras", sorted(ids) == sorted(by_case) and len(ids) == len(set(ids)),
            f"missing={sorted(set(by_case) - set(ids))[:3]} extra={sorted(set(ids) - set(by_case))[:3]} duplicates={len(ids) - len(set(ids))}")
    by_string, by_numeric = ids == sorted(ids), ids == sorted(ids, key=case_sort_key)
    wanted = {"string": by_string, "numeric": by_numeric, None: by_string or by_numeric}[order]
    rep.add("rows sorted by case_id", wanted, f"string order: {by_string}; numeric order: {by_numeric} (the specification does not say which; either is accepted unless --case-order is given)")
    rep.add("deletion_status values are allowed", all(r["deletion_status"] in STATUSES for r in rows),
            str(dict(Counter(r["deletion_status"] for r in rows))))
    problems = validate_rows(rows, by_case, order)
    rep.add("steps_json valid; seven fields; consecutive step_no; phase/operation/state pairs; ordering; evidence rules", not problems,
            "; ".join(str(p) for p in problems[:3]) or "no problems")

    parsed: Dict[str, list] = {}
    for r in rows:
        try:
            parsed[r["case_id"]] = json.loads(r["steps_json"])
        except ValueError:
            parsed[r["case_id"]] = []
    # NOT_APPLICABLE handling
    na_bad = [r["case_id"] for r in rows if r["deletion_status"] == "NOT_APPLICABLE" and r["steps_json"] != "[]"]
    rep.add("NOT_APPLICABLE rows have steps_json == []", not na_bad, str(na_bad[:3]))
    text_na = {c.case_id for c in requests if classify_intent(c.request_text).label != DELETION}
    out_na = {r["case_id"] for r in rows if r["deletion_status"] == "NOT_APPLICABLE"}
    rep.add("NOT_APPLICABLE rows are exactly the requests whose text does not authorize deletion", out_na == text_na,
            f"{len(out_na)} not-applicable rows; text classifier says {len(text_na)}")

    # evidence IDs: real, and not after the case's audit_at
    unknown, late, image_cites, citations = [], [], [], 0
    for cid, steps in parsed.items():
        audit_at = by_case[cid].audit_at
        for s in steps:
            ev = s.get("evidence_id", "")
            if not ev:
                continue
            citations += 1
            if ev in manifest:
                t = manifest[ev].captured_at
                if manifest[ev].suffix in IMAGE_SUFFIXES:
                    image_cites.append((cid, ev))
            elif ev in activity_times:
                t = activity_times[ev]
            elif ev in audit_times:
                t = audit_times[ev]
            else:
                unknown.append((cid, ev))
                continue
            if t > audit_at:
                late.append((cid, ev, t, audit_at))
    rep.add("every cited evidence_id is a real file_id / activity_id / audit_id", not unknown, f"{citations} citations; unknown={unknown[:3]}")
    rep.add("no cited evidence was captured/logged after the case's audit_at", not late, f"violations={late[:3]}")
    rep.add("screenshot citations (allowed: admissible by manifest capture time, checked above)", True,
            f"{len(image_cites)} steps cite a screenshot file in {len({c for c, _ in image_cites})} cases", critical=False)


    # informational
    steps_total = sum(len(v) for v in parsed.values())
    incomplete_no_findings = [cid for r in rows for cid in [r["case_id"]]
                              if r["deletion_status"] == "INCOMPLETE" and not any(s["phase"] == "FINDING" for s in parsed[cid])]
    rep.add("INCOMPLETE rows that contain no finding (completeness could not be proven)", True,
            f"{len(incomplete_no_findings)} rows", critical=False)
    rep.stats = {"rows": len(rows), "statuses": dict(Counter(r["deletion_status"] for r in rows)), "steps_total": steps_total,
                 "citations": citations, "incomplete_without_findings": sorted(incomplete_no_findings, key=lambda c: int(c.split("_")[1])),
                 "steps_by_phase": dict(Counter(s["phase"] for v in parsed.values() for s in v)),
                 "steps_by_operation": dict(sorted(Counter(s["operation"] for v in parsed.values() for s in v).items()))}
    return rep
