"""Per-resource state reconstruction at ``audit_at``.

Three different times are kept apart:

* ``captured_at``  - when the evidence *file* was captured (manifest). A file captured after ``audit_at`` is
  inadmissible **in full**, even if it contains an inner snapshot or record dated earlier (strict reading of
  section 6.6: "never cite an evidence item captured after the case's audit_at"). ``admissibility="record"`` is a
  diagnostic alternative that trusts inner record times; it is never used to *cite* a file.
* ``observed_at``  - the time of the record/log line/snapshot inside the file, when the source carries one.
* ``audit_at``     - the cutoff for the case.

Reconstruction rules
--------------------
1. Only admissible facts are used. Activity rows and ``reported_status`` are *claims* and never decide a state; they
   are attached to the result as provenance and as a contradiction note.
2. Observations older than ``horizon_days`` before ``audit_at`` are stale: they cannot establish the state at
   ``audit_at`` and are ignored (the resource is then treated as unobserved).
3. The latest observation time wins (a later ABSENT supersedes an earlier PRESENT; a later PRESENT after ABSENT is
   reported as recreation). Observations sharing the latest time that disagree are a *conflict*; the least compliant
   kind is chosen (surviving data is never hidden).
4. Replicas: every node of a snapshot (primary, regional, reporting, read, failover) counts. A record present on any
   node survives, even when the primary no longer has it (``replica_drift``).
5. An explicitly enumerated collection that does not list the resource is evidence of absence, but only for that
   system/collection, only from admissible and non-stale snapshots, and only for snapshots newer than the last time the
   resource was seen. Service-export lookup lists and ledgers are not enumerations; JSON bundle snapshots are
   subject-scoped (they prove absence only for subjects that appear in the same file); a later snapshot of one database
   node cannot supersede presence on another node; retained categories (audit/legal/backup/tombstone) are never declared
   absent by silence alone.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .models import Activity, Fact, Scope
from .observations import (ABSENT, ACTIVE, ANONYMIZED, BLOCKED, DISABLED, EXPOSED, PRESENT, REDACTED, RESTORABLE, UNKNOWN,
                           Observation, interpret)
from .taxonomy import COLLECTION_CATEGORY, category_for_resource, system_for_resource
from .timeutil import minutes_between

HORIZON_DAYS = 30
# worst first: used to pick the least compliant reading when sources at the same time disagree
SEVERITY = [EXPOSED, ACTIVE, RESTORABLE, PRESENT, REDACTED, ANONYMIZED, DISABLED, BLOCKED, ABSENT, UNKNOWN]
ENUMERATING_COLLECTIONS = set(COLLECTION_CATEGORY)
# Records the policy requires us to *keep* are never declared absent from the mere silence of a collection snapshot
# (snapshots in this dataset do not list every retained record); only an explicit NOT_FOUND observation counts.
NO_SCOPE_ABSENCE_CATEGORIES = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE", "ACCOUNT_TOMBSTONE"}


@dataclass(frozen=True)
class StateConfig:
    horizon_days: float = HORIZON_DAYS
    admissibility: str = "file"      # 'file' (default, strict) | 'record' (diagnostic only)


@dataclass
class ResourceState:
    resource_id: str
    system_id: Optional[str]
    category: Optional[str]
    kind: str
    evidence_id: str = ""                          # file_id of the decisive observation / enumerating snapshot
    observed_at: Optional[str] = None
    basis: str = "none"                            # 'observation' | 'scope_absence' | 'none'
    decisive: Optional[Observation] = None
    observations: List[Observation] = field(default_factory=list)
    history: List[Tuple[str, str, str]] = field(default_factory=list)   # (time, kind, file_id), oldest first
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    claims: List[dict] = field(default_factory=list)
    retention_until: Optional[str] = None

    @property
    def observed(self) -> bool:
        return self.kind != UNKNOWN


class StateReconstructor:
    def __init__(self, facts: Iterable[Fact], scopes: Iterable[Scope], activities: Iterable[Activity], config: StateConfig = StateConfig()):
        self.config = config
        self._by_res: Dict[str, List[Fact]] = defaultdict(list)
        self._facts_by_file_res: Dict[Tuple[str, str], List[Fact]] = defaultdict(list)
        for f in facts:
            if f.resource_id:
                self._by_res[f.resource_id].append(f)
                self._facts_by_file_res[(f.file_id, f.resource_id)].append(f)
        self._scopes = [s for s in scopes if s.collection in ENUMERATING_COLLECTIONS]
        self._scopes_by_file: Dict[str, List[Scope]] = defaultdict(list)
        for s in self._scopes:
            self._scopes_by_file[s.file_id].append(s)
        self._claims: Dict[str, List[Activity]] = defaultdict(list)
        for a in activities:
            self._claims[a.resource_id].append(a)

    # ------------------------------------------------------------------ admissibility
    def _admissible_fact(self, f: Fact, audit_at: str) -> bool:
        if self.config.admissibility == "record":
            return f.effective_time() <= audit_at
        return f.available_at(audit_at)

    def _fresh(self, time: str, audit_at: str) -> bool:
        return minutes_between(audit_at, time) / 1440.0 <= self.config.horizon_days

    @staticmethod
    def _scope_time(s: Scope) -> str:
        return s.observed_at or s.captured_at

    # ------------------------------------------------------------------ main entry point
    def reconstruct(self, resource_id: str, audit_at: str, category: Optional[str] = None,
                    subject_files: Optional[Set[str]] = None) -> ResourceState:
        """``subject_files``: files that mention the resource's subject; a subject-scoped (bundle) snapshot only proves
        absence when it is one of them."""
        category = category or self._category_of(resource_id)
        system = system_for_resource(resource_id)
        st = ResourceState(resource_id, system, category, UNKNOWN)
        st.claims = [{"activity_id": a.activity_id, "event_type": a.event_type, "result": a.reported_result, "time": a.event_time}
                     for a in sorted(self._claims.get(resource_id, ()), key=lambda a: a.event_time) if a.event_time <= audit_at]

        obs_all: List[Observation] = []
        stale = 0
        for f in self._by_res.get(resource_id, ()):
            if not self._admissible_fact(f, audit_at):
                continue
            ob = interpret(f)
            if ob is None or ob.kind == UNKNOWN:
                continue
            if not self._fresh(ob.time, audit_at):
                stale += 1
                continue
            obs_all.append(ob)
        if stale:
            st.flags.append("stale_observations_ignored")
            st.notes.append(f"{stale} observation(s) older than {self.config.horizon_days:g} days ignored")
        obs_all.sort(key=lambda o: (o.time, o.file_id))
        st.observations = obs_all
        st.history = [(o.time, o.kind, o.file_id) for o in obs_all]

        decisive: Optional[Observation] = None
        if obs_all:
            latest = obs_all[-1].time
            group = [o for o in obs_all if o.time == latest]
            kinds = {o.kind for o in group}
            decisive = min(group, key=lambda o: (SEVERITY.index(o.kind), o.file_id))
            if len(kinds) > 1:
                st.flags.append("conflict")
                st.notes.append("sources at the same time disagree: " + ", ".join(sorted(f"{o.kind}@{o.file_id}" for o in group)) +
                                f"; least compliant ({decisive.kind}) used")
            earlier = [o for o in obs_all if o.time < latest]
            if earlier and any(o.kind == ABSENT for o in earlier) and decisive.kind != ABSENT:
                st.flags.append("recreated")
                st.notes.append("observed absent earlier but present again later")
            if earlier and any(o.kind != decisive.kind for o in earlier):
                st.flags.append("state_changed")
            self._note_replica_drift(st, decisive, resource_id)
            st.kind, st.evidence_id, st.observed_at, st.basis = decisive.kind, decisive.file_id, decisive.time, "observation"
            st.decisive, st.retention_until = decisive, decisive.retention_until
            if decisive.kind != ABSENT:      # a later enumerating snapshot that no longer lists it supersedes the observation
                roles = {o.role for o in group if o.role}
                later = self._absence_scope(resource_id, system, category, audit_at, after=latest, subject_files=subject_files, roles=roles)
                if later is not None:
                    st.flags.append("superseded_by_absence")
                    st.notes.append(f"present at {latest}, but the later enumerating snapshot {later.file_id} does not list it")
                    st.kind, st.evidence_id, st.observed_at, st.basis, st.decisive = ABSENT, later.file_id, self._scope_time(later), "scope_absence", None
        else:
            sc = self._absence_scope(resource_id, system, category, audit_at, after=None, subject_files=subject_files)
            if sc is not None:
                st.kind, st.evidence_id, st.observed_at, st.basis = ABSENT, sc.file_id, self._scope_time(sc), "scope_absence"
                st.notes.append(f"not listed in the enumerated collection {sc.collection!r} of {sc.system_id}")

        self._note_claim_contradictions(st)
        return st

    # ------------------------------------------------------------------ helpers
    def _category_of(self, resource_id: str) -> Optional[str]:
        for f in self._by_res.get(resource_id, ()):
            if f.category:
                return f.category
        return category_for_resource(resource_id)

    def _absence_scope(self, resource_id: str, system: Optional[str], category: Optional[str], audit_at: str,
                       after: Optional[str], subject_files: Optional[Set[str]] = None,
                       roles: Optional[Set[str]] = None) -> Optional[Scope]:
        """Latest admissible, fresh, enumerating snapshot of the resource's own collection that does not list it.

        ``roles``: when a record was seen on specific database nodes, only a later snapshot *of those nodes* can show it
        gone (an empty inventory export says nothing about a failover replica)."""
        if not system or not category or category in NO_SCOPE_ABSENCE_CATEGORIES:
            return None
        best: Optional[Scope] = None
        for s in self._scopes:
            if s.system_id != system or COLLECTION_CATEGORY.get(s.collection) != category:
                continue
            t = self._scope_time(s)
            if s.captured_at > audit_at and self.config.admissibility == "file":
                continue
            if t > audit_at or not self._fresh(t, audit_at):
                continue
            if after is not None and t <= after:
                continue
            if roles and s.source_role not in roles:
                continue
            if s.subject_scoped and (subject_files is None or s.file_id not in subject_files):
                continue
            if self._facts_by_file_res.get((s.file_id, resource_id)):
                continue                      # the snapshot does list it (some node holds it)
            if best is None or (t, s.file_id) > (self._scope_time(best), best.file_id):
                best = s
        return best

    def _note_replica_drift(self, st: ResourceState, ob: Observation, resource_id: str) -> None:
        """Record when a record survives on some nodes of the deciding snapshot but is missing from others."""
        present_roles = {f.source_role for f in self._facts_by_file_res.get((ob.file_id, resource_id), ()) if f.source_role}
        scoped = {s.source_role for s in self._scopes_by_file.get(ob.file_id, ())
                  if s.system_id == st.system_id and COLLECTION_CATEGORY.get(s.collection) == st.category and s.source_role}
        missing = scoped - present_roles
        if present_roles and missing:
            st.flags.append("replica_drift")
            st.notes.append(f"{ob.file_id}: present on {sorted(present_roles)} but absent on {sorted(missing)}")

    @staticmethod
    def _note_claim_contradictions(st: ResourceState) -> None:
        """Workflow claims are never trusted: flag a SUCCESS claim that evidence contradicts."""
        if st.kind in (PRESENT, EXPOSED, RESTORABLE, ACTIVE):
            for c in st.claims:
                if c["result"] == "SUCCESS" and c["event_type"] in ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "BLOCK_RESTORE", "DISABLE_DATA_FLOW"):
                    st.flags.append("claim_contradicted")
                    st.notes.append(f"{c['activity_id']} claims {c['event_type']} SUCCESS but evidence {st.evidence_id} shows {st.kind}")
                    break
