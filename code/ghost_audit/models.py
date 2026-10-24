"""Plain data containers shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .text_ids import EMPTY_IDS, Identifiers


@dataclass(frozen=True)
class EvidenceMeta:
    file_id: str
    rel_path: str          # as written in evidence_files.csv, e.g. 'evidence/logs/x.log'
    path: Path             # absolute path on disk
    origin: str
    captured_at: str       # normalized UTC ISO string

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


@dataclass
class Fact:
    """One observation about one resource, extracted from one evidence file.

    ``captured_at`` is the file-level capture time from the manifest (the cutoff gate for citing the file).
    ``observed_at`` is the finer record/line time when the source carries one.
    ``state`` is the state *as the source reports it* (FOUND, NOT_FOUND, IDENTIFIABLE, ACTIVE, PRESENT, ...);
    policy interpretation happens in later stages.
    """

    file_id: str
    captured_at: str
    system_id: Optional[str]
    resource_id: Optional[str]
    category: Optional[str]
    state: str
    identifiers: Identifiers = EMPTY_IDS
    observed_at: Optional[str] = None
    source_role: Optional[str] = None   # 'primary', 'read_replica', 'replica_ap_south', 'failover_replica', ...
    attrs: Dict[str, Any] = field(default_factory=dict)
    refs: tuple = ()                    # non-resource reference tokens (ORD-..., CS-..., NST-...)
    locator: str = ""                   # where in the file (line number, table, json path)

    def effective_time(self) -> str:
        return self.observed_at or self.captured_at

    def available_at(self, audit_at: str) -> bool:
        """True when this fact may be used/cited for a case audited at ``audit_at`` (both the file and the record
        must be at or before the cutoff)."""
        if self.captured_at > audit_at:
            return False
        return self.observed_at is None or self.observed_at <= audit_at


@dataclass(frozen=True)
class Scope:
    """A source that enumerates a collection of a system at a point in time (so an empty result proves absence)."""

    file_id: str
    captured_at: str
    system_id: str
    collection: Optional[str]
    source_role: Optional[str] = None
    observed_at: Optional[str] = None
    locator: str = ""
    # JSON bundle snapshots list the records of the subjects they were assembled for, not every record in the system:
    # their silence proves absence only for subjects that appear in the same file.
    subject_scoped: bool = False


@dataclass
class ExtractionResult:
    file_id: str
    kind: str                           # 'handover_txt', 'json_bundle', 'sql_snapshot', 'log', 'pdf', 'image', ...
    facts: List[Fact] = field(default_factory=list)
    scopes: List[Scope] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unsupported: bool = False           # True when the format needs a tool/model we do not run locally (PNG)
    lines_total: int = 0                # parse-coverage accounting (lines, rows or records seen)
    lines_parsed: int = 0
    error: Optional[str] = None         # set when the parser raised; coverage reports treat this as a failure


@dataclass(frozen=True)
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass(frozen=True)
class Activity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class IdentityRecord:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: str


@dataclass(frozen=True)
class AuditChange:
    audit_id: str
    user_id: str
    event_time: str
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str
