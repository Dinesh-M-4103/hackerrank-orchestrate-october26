"""Resource -> user ownership, built only from direct evidence and explicit identity links.

Two kinds of link exist:

* ``EVIDENCE``       - a fact in an evidence file carries identifiers (email, hash, ``user_NN``) that resolve to exactly
                       one user; the fact's resource belongs to that user.
* ``ACTIVITY_LINK``  - a ``LINK_RESOURCE_IDENTITY`` activity row names a resource, an email and a user explicitly.

A link is only usable for a case audited at ``T`` when its source was captured / logged at or before ``T``
(pass ``as_of=T``). Nothing here guesses: a fact whose identifiers point at several users is recorded as a conflict
and creates no link. Timing-based attribution lives in ``attribution.py`` and is kept apart on purpose.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .identity import IdentityResolver
from .models import Activity, Fact
from .taxonomy import resource_prefix
from .text_ids import identifiers_in_text

LINK_EVENT = "LINK_RESOURCE_IDENTITY"
_LABEL_RE = re.compile(r"\(([^)]*)\)")


@dataclass(frozen=True)
class OwnerLink:
    resource_id: str
    user_id: str
    kind: str            # 'EVIDENCE' | 'ACTIVITY_LINK'
    source_id: str       # file_id or activity_id
    time: str            # captured_at (evidence) or event_time (activity): the cutoff gate
    via: Tuple[str, ...] # identifier kinds that matched: email / email_hash / user_ref
    locator: str = ""


@dataclass(frozen=True)
class OwnerConflict:
    resource_id: str
    source_id: str
    users: Tuple[str, ...]
    reason: str


class OwnerMap:
    def __init__(self) -> None:
        self._links: Dict[str, List[OwnerLink]] = defaultdict(list)
        self._by_user: Dict[str, List[OwnerLink]] = defaultdict(list)
        self.conflicts: List[OwnerConflict] = []
        self.aliases: Dict[str, str] = {}      # label (e.g. 'NST-2026-0718-1842', 'user_17') -> resource_id
        self.anonymous: Dict[str, List[str]] = defaultdict(list)   # resource -> file_ids that mention it with no identity
        # resource -> [(source_id, time, reason)]: evidence that contradicts an existing link (e.g. a screenshot showing a
        # different, unresolvable subject for a resource that a catalog row ties to someone else)
        self.disputes: Dict[str, List[tuple]] = defaultdict(list)

    # ---------------------------------------------------------------- construction
    def _add(self, link: OwnerLink) -> None:
        self._links[link.resource_id].append(link)
        self._by_user[link.user_id].append(link)

    def add_activity_links(self, activities: Iterable[Activity], resolver: IdentityResolver) -> None:
        for a in activities:
            if a.event_type != LINK_EVENT:
                continue
            ids = identifiers_in_text(a.details, with_phones=False)
            users = resolver.match_identifiers(ids)
            label = _LABEL_RE.search(a.details)
            if label:
                self.aliases[label.group(1).strip()] = a.resource_id
            if len(users) == 1:
                (u, kinds), = users.items()
                self._add(OwnerLink(a.resource_id, u, "ACTIVITY_LINK", a.activity_id, a.event_time, tuple(sorted(kinds)), "details"))
            else:
                self.conflicts.append(OwnerConflict(a.resource_id, a.activity_id, tuple(sorted(users)), "link row does not resolve to one user"))

    def add_facts(self, facts: Iterable[Fact], resolver: IdentityResolver) -> None:
        for f in facts:
            rid = f.resource_id
            if rid is None:  # a document that cites an aliased reference (e.g. a statement number)
                rid = next((self.aliases[r] for r in f.refs if r in self.aliases), None)
            if rid is None or not resource_prefix(rid):
                continue
            if f.identifiers.is_empty():
                self.anonymous[rid].append(f.file_id)
                continue
            users = resolver.match_identifiers(f.identifiers)
            if not users:
                self.anonymous[rid].append(f.file_id)
            elif len(users) == 1:
                (u, kinds), = users.items()
                self._add(OwnerLink(rid, u, "EVIDENCE", f.file_id, max(f.captured_at, f.observed_at or f.captured_at), tuple(sorted(kinds)), f.locator))
            else:
                self.conflicts.append(OwnerConflict(rid, f.file_id, tuple(sorted(users)), "identifiers point at several users"))

    def detect_disputes(self, facts: Iterable[Fact], resolver: IdentityResolver, sources: Optional[set] = None) -> None:
        """A fact that names a subject for a *linked* resource, but whose identifiers resolve to nobody, contradicts the link.
        ``sources`` limits the check to some files (default: every fact)."""
        for f in facts:
            if f.resource_id is None or f.resource_id not in self._links or f.identifiers.is_empty():
                continue
            if sources is not None and f.file_id not in sources:
                continue
            if not (f.identifiers.emails or f.identifiers.user_refs):
                continue
            if resolver.match_identifiers(f.identifiers):
                continue
            self.disputes[f.resource_id].append((f.file_id, max(f.captured_at, f.observed_at or f.captured_at),
                                                 "evidence shows a subject that resolves to nobody, contradicting the link"))

    def is_disputed(self, resource_id: str, as_of: Optional[str] = None) -> bool:
        return any(as_of is None or t <= as_of for _, t, _ in self.disputes.get(resource_id, ()))

    # ---------------------------------------------------------------- queries
    @staticmethod
    def _usable(link: OwnerLink, as_of: Optional[str]) -> bool:
        return as_of is None or link.time <= as_of

    def links_for(self, resource_id: str, as_of: Optional[str] = None) -> List[OwnerLink]:
        return [l for l in self._links.get(resource_id, ()) if self._usable(l, as_of)]

    def users_of(self, resource_id: str, as_of: Optional[str] = None) -> Dict[str, List[OwnerLink]]:
        out: Dict[str, List[OwnerLink]] = defaultdict(list)
        for l in self.links_for(resource_id, as_of):
            out[l.user_id].append(l)
        return dict(out)

    def owner_of(self, resource_id: str, as_of: Optional[str] = None) -> Optional[str]:
        """The single user the resource is linked to, or None when unlinked or linked to several users."""
        users = self.users_of(resource_id, as_of)
        return next(iter(users)) if len(users) == 1 else None

    def resources_of(self, user_id: str, as_of: Optional[str] = None, prefix: Optional[str] = None) -> Dict[str, List[OwnerLink]]:
        out: Dict[str, List[OwnerLink]] = defaultdict(list)
        for l in self._by_user.get(user_id, ()):
            if self._usable(l, as_of) and (prefix is None or resource_prefix(l.resource_id) == prefix):
                out[l.resource_id].append(l)
        return dict(out)

    def sources_of(self, user_id: str, as_of: Optional[str] = None) -> set:
        """File / activity IDs that link at least one resource to this user (used to decide whether a subject-scoped
        snapshot covers the person)."""
        return {l.source_id for l in self._by_user.get(user_id, ()) if self._usable(l, as_of)}

    def all_resources(self) -> List[str]:
        return sorted(self._links)


def build_owner_map(facts: Iterable[Fact], activities: Iterable[Activity], resolver: IdentityResolver) -> OwnerMap:
    om = OwnerMap()
    om.add_activity_links(activities, resolver)   # first: aliases are needed to place reference-only documents
    facts = list(facts)
    om.add_facts(facts, resolver)
    om.detect_disputes(facts, resolver, sources={f.file_id for f in facts if f.attrs.get("source") == "image"})
    return om
