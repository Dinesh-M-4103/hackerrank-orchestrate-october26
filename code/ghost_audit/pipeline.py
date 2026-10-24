"""Orchestration: intent -> attribution -> relevant resources -> state -> policy -> plan, for a list of cases."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .attribution import AMBIGUOUS, CONFIRMED, PROBABLE, AccountAttributor, AccountAttribution
from .context import Context, build_context
from .intent import Intent, decide
from .intent_hints import nondeletion_hints
from .models import Case
from .planner import CasePlan, plan_case
from .policy import PolicyEngine
from .scoring import CaseRow
from .state import ResourceState, StateConfig, StateReconstructor
from .taxonomy import resource_prefix
from .timeutil import minutes_between

OWNERSHIP_STRICT = "strict"           # ownership evidence must be captured at or before audit_at (default)
OWNERSHIP_LINK_ONLY = "link_only"     # DIAGNOSTIC: later evidence may reveal who owns a resource; state is still strict


class Pipeline:
    def __init__(self, ctx: Optional[Context] = None, state_config: StateConfig = StateConfig(), ownership: str = OWNERSHIP_STRICT,
                 allow_elimination: bool = True, scopes: Optional[Iterable] = None):
        self.ctx = ctx or build_context()
        self.engine = PolicyEngine.from_rules(self.ctx.rules)
        scopes = list(scopes) if scopes is not None else [s for r in self.ctx.extractions.values() for s in r.scopes]
        self.states = StateReconstructor(self.ctx.facts, scopes, self.ctx.activities, state_config)
        self.ownership = ownership
        self.attributor = AccountAttributor(self.ctx.resolver, self.ctx.owners, self.ctx.activities, allow_elimination)

    # ------------------------------------------------------------------ helpers
    def _as_of(self, case: Case) -> Optional[str]:
        return case.audit_at if self.ownership == OWNERSHIP_STRICT else None

    def relevant_states(self, case: Case, user: str, attribution: AccountAttribution) -> List[ResourceState]:
        """States of the person's own resources: evidence-linked resources (any type but accounts) plus the accounts the
        attribution asserts. Accounts are never taken from the owner map directly: a person may have older accounts that
        belong to other deletion runs."""
        as_of = self._as_of(case)
        window = self.states.config.horizon_days * 1440.0
        ids = set()
        for r, links in self.ctx.owners.resources_of(user, as_of).items():
            # a link far from audit_at (e.g. an earlier deletion run of the same person) says nothing about this case
            if self.ctx.owners.is_disputed(r, as_of):
                continue
            if resource_prefix(r) != "account" and any(abs(minutes_between(l.time, case.audit_at)) <= window for l in links):
                ids.add(r)
        if attribution.status in (CONFIRMED, PROBABLE):
            ids |= set(attribution.accounts)
        files = self.ctx.owners.sources_of(user, as_of)
        return [self.states.reconstruct(r, case.audit_at, subject_files=files) for r in sorted(ids)]

    def run(self, cases: Iterable[Case]) -> Dict[str, CasePlan]:
        cases = list(cases)
        hints = nondeletion_hints(cases, self.ctx.activities, self.ctx.owners, self.ctx.resolver, strict_cutoff=True)
        intents: Dict[str, Intent] = {c.case_id: decide(c.request_text, hints.get(c.case_id)) for c in cases}
        deletion = [c for c in cases if intents[c.case_id].authorizes_deletion]
        strict = self.ownership == OWNERSHIP_STRICT
        attributions = self.attributor.attribute(deletion, strict_cutoff=strict)

        plans: Dict[str, CasePlan] = {}
        for c in cases:
            intent = intents[c.case_id]
            if not intent.authorizes_deletion:
                plans[c.case_id] = plan_case(c, intent, None, [], self.engine)
                continue
            attr = attributions[c.case_id]
            user = attr.user_id
            states = self.relevant_states(c, user, attr) if user else []
            cand_states = {}
            if attr.status == AMBIGUOUS:
                files = self.ctx.owners.sources_of(user, self._as_of(c)) if user else set()
                cand_states = {a: self.states.reconstruct(a, c.audit_at, subject_files=files).kind for a in attr.candidates}
            plans[c.case_id] = plan_case(
                c, intent, attr, states, self.engine,
                owners_for_attachment=lambda rid, c=c: self.ctx.owners.users_of(rid, self._as_of(c)),
                candidate_states=cand_states)
        return plans


def rows_for(plans: Dict[str, CasePlan]) -> Dict[str, CaseRow]:
    import json
    out = {}
    for cid, p in plans.items():
        out[cid] = CaseRow(cid, p.status, json.loads(p.to_row()["steps_json"]))
    return out
