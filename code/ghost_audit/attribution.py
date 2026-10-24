"""Account attribution: which ``account_*`` resource belongs to which case?

Policy (set by the project owner):

1. Direct evidence and explicit identity links first (``CONFIRMED``).
2. Timing-based attribution only when the candidate is *uniquely* supported by the documented rule and the audit
   cutoff is respected (``PROBABLE``).
3. Never choose at random among ambiguous candidates; an ambiguous case keeps its full candidate set (``AMBIGUOUS``)
   and *no* account is asserted for it.
4. Candidate sets and the basis for every decision are preserved for evaluation and debugging.

Documented timing rule ("primary-submit window")
    The deletion worker logs ``Deletion worker submitted primary profile account_N`` (a DELETE_RECORD on
    account_service) for each account it processes. For the evidence-confirmed pairs in the dataset that event lands
    44-75 minutes (1st-99th percentile) before the case's ``reported_at``; the rule uses the wider 30-90 minute window.
    A candidate must also be logged at or before ``audit_at``.

Candidates are then filtered: an account that evidence (as of ``audit_at``) ties to a *different* user is dropped.
Finally the cases are solved jointly: a case left with exactly one candidate claims it, and that account is removed
from the other cases' candidate sets (``timing_elimination``). Two cases claiming the same single account block each
other (``timing_conflict``) rather than one winning.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .identity import IdentityResolver
from .models import Activity, Case
from .owners import OwnerMap
from .timeutil import minutes_between

LAG_MIN_MINUTES = 30.0
LAG_MAX_MINUTES = 90.0
COMPLETION_REPORT_WINDOW_MINUTES = 10.0
SUBMIT_MARKER = "submitted primary profile"

CONFIRMED = "CONFIRMED"
PROBABLE = "PROBABLE"
AMBIGUOUS = "AMBIGUOUS"
NONE = "NONE"


@dataclass(frozen=True)
class TimingCandidate:
    resource_id: str
    activity_id: str
    event_time: str
    lag_minutes: float
    rule: str


@dataclass
class AccountAttribution:
    case_id: str
    user_id: Optional[str]
    status: str                              # CONFIRMED | PROBABLE | AMBIGUOUS | NONE
    basis: str                               # owner_link | timing_unique | timing_elimination | timing_ambiguous | ...
    accounts: Tuple[str, ...] = ()           # asserted accounts (empty unless CONFIRMED / PROBABLE)
    candidates: Tuple[str, ...] = ()         # candidate set at the end of solving
    initial_candidates: Tuple[str, ...] = () # candidates before elimination
    evidence_ids: Tuple[str, ...] = ()       # file_ids / activity_ids supporting the assertion
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


class AccountAttributor:
    def __init__(self, resolver: IdentityResolver, owners: OwnerMap, activities: Iterable[Activity], allow_elimination: bool = True):
        self.resolver = resolver
        self.owners = owners
        self.allow_elimination = allow_elimination
        self._deletes = [a for a in activities
                         if a.event_type == "DELETE_RECORD" and a.target_system == "account_service" and a.source_system == "deletion_worker"]

    # ------------------------------------------------------------------ candidate generation
    def timing_candidates(self, case: Case) -> List[TimingCandidate]:
        out: List[TimingCandidate] = []
        for a in self._deletes:
            if a.event_time > case.audit_at:          # cutoff: later activity cannot support this case
                continue
            lag = minutes_between(case.reported_at, a.event_time)
            if SUBMIT_MARKER in a.details:
                if LAG_MIN_MINUTES <= lag <= LAG_MAX_MINUTES:
                    out.append(TimingCandidate(a.resource_id, a.activity_id, a.event_time, lag, "primary_submit_window"))
            elif abs(lag) <= COMPLETION_REPORT_WINDOW_MINUTES:
                out.append(TimingCandidate(a.resource_id, a.activity_id, a.event_time, lag, "completion_report_window"))
        return out

    # ------------------------------------------------------------------ solving
    def attribute(self, cases: Sequence[Case], strict_cutoff: bool = True) -> Dict[str, AccountAttribution]:
        """Attribute accounts for ``cases`` jointly. ``strict_cutoff=False`` also uses ownership evidence captured
        after ``audit_at`` (for diagnostics only - it violates the evidence-time rule)."""
        results: Dict[str, AccountAttribution] = {}
        pending: Dict[str, Set[str]] = {}
        cand_meta: Dict[Tuple[str, str], TimingCandidate] = {}
        user_of: Dict[str, Optional[str]] = {}

        for case in cases:
            as_of = case.audit_at if strict_cutoff else None
            res = self.resolver.resolve_email(case.email)
            user_of[case.case_id] = res.user_id
            if res.user_id is None:
                results[case.case_id] = AccountAttribution(case.case_id, None, NONE, "user_unresolved" if res.status == "UNKNOWN" else "user_ambiguous",
                                                           notes=(f"identity resolution: {res.status}",))
                continue
            user = res.user_id
            confirmed = {}
            for rid, links in self.owners.resources_of(user, as_of, prefix="account").items():
                if set(self.owners.users_of(rid, as_of)) == {user} and not self.owners.is_disputed(rid, as_of):   # shared or contradicted links do not confirm
                    confirmed[rid] = links
            timing = self.timing_candidates(case)
            if confirmed:
                chosen, basis, notes = dict(confirmed), "owner_link", ()
                if len(confirmed) > 1:
                    # The person has several evidence-linked accounts (e.g. an older deleted one). Timing may tell which
                    # one this case's deletion run processed; it never overrides a single confirmed account.
                    in_window = {tc.resource_id for tc in timing}
                    supported = {a: ls for a, ls in confirmed.items() if a in in_window}
                    if len(supported) == 1:
                        chosen, basis = supported, "owner_link_timing_disambiguated"
                        notes = (f"other linked accounts excluded as timing-inconsistent: {sorted(set(confirmed) - set(supported))}",)
                    else:
                        notes = ("multiple accounts linked to this user; timing does not single one out",)
                ev = tuple(sorted({l.source_id for ls in chosen.values() for l in ls}))
                results[case.case_id] = AccountAttribution(
                    case.case_id, user, CONFIRMED, basis, tuple(sorted(chosen)), tuple(sorted(confirmed)),
                    tuple(sorted(confirmed)), ev, notes=notes)
                continue
            cands = []
            for tc in timing:
                cand_meta[(case.case_id, tc.resource_id)] = tc
                other = set(self.owners.users_of(tc.resource_id, as_of)) - {user}
                if not other:
                    cands.append(tc.resource_id)
            pending[case.case_id] = set(cands)

        initial = {cid: tuple(sorted(s)) for cid, s in pending.items()}
        assigned: Dict[str, Tuple[str, str]] = {}      # case -> (account, basis)
        blocked: Set[str] = set()
        round_no = 0
        while True:
            singles = {cid: next(iter(s)) for cid, s in pending.items() if len(s) == 1 and cid not in assigned and cid not in blocked}
            if not singles or (round_no > 0 and not self.allow_elimination):
                break
            claims: Dict[str, List[str]] = defaultdict(list)
            for cid, acc in singles.items():
                claims[acc].append(cid)
            progressed = False
            for acc, cids in claims.items():
                if len(cids) > 1:
                    blocked.update(cids)
                    continue
                cid = cids[0]
                assigned[cid] = (acc, "timing_unique" if round_no == 0 else "timing_elimination")
                progressed = True
                for other_cid, s in pending.items():
                    if other_cid != cid:
                        s.discard(acc)
            if not progressed:
                break
            round_no += 1

        for cid, s in pending.items():
            user = user_of[cid]
            if cid in assigned:
                acc, basis = assigned[cid]
                tc = cand_meta[(cid, acc)]
                results[cid] = AccountAttribution(cid, user, PROBABLE, basis, (acc,), (acc,), initial[cid], (tc.activity_id,),
                                                  notes=(f"rule={tc.rule}; lag={tc.lag_minutes:.0f}min",))
            elif cid in blocked:
                results[cid] = AccountAttribution(cid, user, AMBIGUOUS, "timing_conflict", (), tuple(sorted(s)), initial[cid],
                                                  notes=("another case has the same single candidate",))
            elif len(s) > 1:
                results[cid] = AccountAttribution(cid, user, AMBIGUOUS, "timing_ambiguous", (), tuple(sorted(s)), initial[cid])
            elif len(s) == 1:   # only reachable when elimination is disabled
                results[cid] = AccountAttribution(cid, user, AMBIGUOUS, "elimination_disabled", (), tuple(sorted(s)), initial[cid])
            else:
                basis = "candidates_consumed" if initial[cid] else "no_candidate"
                results[cid] = AccountAttribution(cid, user, NONE, basis, (), (), initial[cid])
        return results


def summarize(results: Dict[str, AccountAttribution]) -> dict:
    by_status: Dict[str, int] = defaultdict(int)
    by_basis: Dict[str, int] = defaultdict(int)
    sizes: Dict[int, int] = defaultdict(int)
    for r in results.values():
        by_status[r.status] += 1
        by_basis[f"{r.status}/{r.basis}"] += 1
        if r.status == AMBIGUOUS:
            sizes[len(r.candidates)] += 1
    return {"total": len(results), "by_status": dict(by_status), "by_basis": dict(sorted(by_basis.items())),
            "ambiguous_candidate_set_sizes": dict(sorted(sizes.items()))}
