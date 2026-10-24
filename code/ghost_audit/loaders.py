"""CSV loaders for the participant-facing dataset. Every loader takes an explicit dataset directory so tests can
point at synthetic fixtures."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional, Union

from .models import Activity, AuditChange, Case, EvidenceMeta, IdentityRecord, Rule
from .paths import DATASET_DIR
from .timeutil import norm_ts

PathLike = Union[str, Path]


def _rows(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def _dir(dataset_dir: Optional[PathLike]) -> Path:
    return Path(dataset_dir) if dataset_dir is not None else DATASET_DIR


def _case(r: dict) -> Case:
    return Case(
        case_id=r["case_id"],
        email=r["email"].strip(),
        requested_at=norm_ts(r["requested_at"]) or r["requested_at"],
        reported_status=r["reported_status"].strip(),
        reported_at=norm_ts(r["reported_at"]) or r["reported_at"],
        audit_at=norm_ts(r["audit_at"]) or r["audit_at"],
        request_text=r["request_text"],
    )


def load_requests(dataset_dir: Optional[PathLike] = None, sample: bool = False) -> List[Case]:
    name = "sample_deletion_requests.csv" if sample else "deletion_requests.csv"
    return [_case(r) for r in _rows(_dir(dataset_dir) / name)]


def load_identities(dataset_dir: Optional[PathLike] = None) -> List[IdentityRecord]:
    return [
        IdentityRecord(r["user_id"], r["name"], r["email"].strip(), r["phone"].strip(), r["email_hash"].strip().lower(),
                       norm_ts(r["updated_at"]) or r["updated_at"])
        for r in _rows(_dir(dataset_dir) / "user_identities.csv")
    ]


def load_audit_log(dataset_dir: Optional[PathLike] = None) -> List[AuditChange]:
    return [
        AuditChange(r["audit_id"], r["user_id"], norm_ts(r["event_time"]) or r["event_time"], r["field_name"],
                    r["old_value"].strip(), r["new_value"].strip())
        for r in _rows(_dir(dataset_dir) / "user_audit_log.csv")
    ]


def load_activity(dataset_dir: Optional[PathLike] = None) -> List[Activity]:
    return [
        Activity(r["activity_id"], norm_ts(r["event_time"]) or r["event_time"], r["event_type"], r["source_system"],
                 r["target_system"], r["resource_id"], r["reported_result"], r["details"])
        for r in _rows(_dir(dataset_dir) / "system_activity.csv")
    ]


def load_rules(dataset_dir: Optional[PathLike] = None) -> List[Rule]:
    return [Rule(r["rule_id"], r["data_category"], r["condition"], r["required_action"])
            for r in _rows(_dir(dataset_dir) / "deletion_rules.csv")]


def load_manifest(dataset_dir: Optional[PathLike] = None) -> List[EvidenceMeta]:
    base = _dir(dataset_dir)
    return [
        EvidenceMeta(r["file_id"], r["file_path"], base / r["file_path"], r["origin"],
                     norm_ts(r["captured_at"]) or r["captured_at"])
        for r in _rows(base / "evidence_files.csv")
    ]
