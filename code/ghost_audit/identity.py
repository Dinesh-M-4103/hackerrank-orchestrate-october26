"""Current + historical identity resolution.

An identity is a stable ``user_id`` plus every email, phone and email hash it has ever carried (reconstructed from
the current snapshot and ``user_audit_log.csv``). Matching is exact-token and case-insensitive. ``email_hash`` is
the SHA-256 of the lower-cased email in this dataset, so the hash of every known email is indexed as well.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .models import AuditChange, IdentityRecord
from .text_ids import Identifiers, normalize_email, normalize_phone

FAR_FUTURE = "9999-12-31T23:59:59Z"
FAR_PAST = "0001-01-01T00:00:00Z"


def sha256_email(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


@dataclass
class Interval:
    value: str
    valid_from: str
    valid_to: str
    audit_id: Optional[str] = None   # the audit entry that *ended* this value (None for the current value)


@dataclass
class UserIdentity:
    user_id: str
    name: str
    current_email: str
    current_phone: str
    current_hash: str
    emails: List[Interval] = field(default_factory=list)
    phones: List[Interval] = field(default_factory=list)
    hashes: List[Interval] = field(default_factory=list)

    def email_values(self) -> Set[str]:
        return {normalize_email(i.value) for i in self.emails}

    def email_at(self, ts: str) -> Optional[str]:
        for i in self.emails:
            if i.valid_from <= ts < i.valid_to:
                return normalize_email(i.value)
        return None


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving a single identifier to a user."""

    user_id: Optional[str]
    status: str                     # CURRENT | HISTORICAL | AMBIGUOUS | UNKNOWN
    candidates: Tuple[str, ...] = ()
    audit_ids: Tuple[str, ...] = () # audit entries that explain a historical match


def _intervals(current: str, changes: List[AuditChange], norm=lambda x: x) -> List[Interval]:
    """Build the value timeline for one field from its (time-ordered) change records."""
    changes = sorted(changes, key=lambda c: c.event_time)
    out: List[Interval] = []
    if not changes:
        return [Interval(current, FAR_PAST, FAR_FUTURE)]
    start = FAR_PAST
    for c in changes:
        out.append(Interval(c.old_value, start, c.event_time, c.audit_id))
        start = c.event_time
    out.append(Interval(changes[-1].new_value, start, FAR_FUTURE))
    return out


class IdentityResolver:
    def __init__(self, identities: Iterable[IdentityRecord], changes: Iterable[AuditChange]):
        by_user_field: Dict[Tuple[str, str], List[AuditChange]] = defaultdict(list)
        for c in changes:
            by_user_field[(c.user_id, c.field_name)].append(c)

        self.users: Dict[str, UserIdentity] = {}
        self._email_idx: Dict[str, Set[str]] = defaultdict(set)
        self._hash_idx: Dict[str, Set[str]] = defaultdict(set)
        self._phone_idx: Dict[str, Set[str]] = defaultdict(set)
        self._email_audit: Dict[Tuple[str, str], List[str]] = defaultdict(list)

        for rec in identities:
            ident = UserIdentity(rec.user_id, rec.name, rec.email, rec.phone, rec.email_hash)
            ident.emails = _intervals(rec.email, by_user_field.get((rec.user_id, "email"), []))
            ident.phones = _intervals(rec.phone, by_user_field.get((rec.user_id, "phone"), []))
            ident.hashes = _intervals(rec.email_hash, by_user_field.get((rec.user_id, "email_hash"), []))
            self.users[rec.user_id] = ident
            for iv in ident.emails:
                e = normalize_email(iv.value)
                self._email_idx[e].add(rec.user_id)
                self._hash_idx[sha256_email(e)].add(rec.user_id)
                if iv.audit_id:
                    self._email_audit[(e, rec.user_id)].append(iv.audit_id)
            for iv in ident.hashes:
                self._hash_idx[iv.value.lower()].add(rec.user_id)
            for iv in ident.phones:
                p = normalize_phone(iv.value)
                if p:
                    self._phone_idx[p].add(rec.user_id)

    # ------------------------------------------------------------------ single-identifier resolution
    def resolve_email(self, email: str) -> Resolution:
        e = normalize_email(email)
        users = sorted(self._email_idx.get(e, ()))
        if not users:
            return Resolution(None, "UNKNOWN")
        if len(users) > 1:
            return Resolution(None, "AMBIGUOUS", tuple(users))
        u = users[0]
        if normalize_email(self.users[u].current_email) == e:
            return Resolution(u, "CURRENT", (u,))
        return Resolution(u, "HISTORICAL", (u,), tuple(self._email_audit.get((e, u), ())))

    def resolve_hash(self, value: str) -> Resolution:
        users = sorted(self._hash_idx.get(value.lower(), ()))
        if not users:
            return Resolution(None, "UNKNOWN")
        if len(users) > 1:
            return Resolution(None, "AMBIGUOUS", tuple(users))
        u = users[0]
        return Resolution(u, "CURRENT" if self.users[u].current_hash == value.lower() else "HISTORICAL", (u,))

    def resolve_user_ref(self, ref: str) -> Resolution:
        return Resolution(ref, "CURRENT", (ref,)) if ref in self.users else Resolution(None, "UNKNOWN")

    def resolve_phone(self, phone: str) -> Resolution:
        users = sorted(self._phone_idx.get(normalize_phone(phone), ()))
        if len(users) == 1:
            return Resolution(users[0], "CURRENT", (users[0],))
        return Resolution(None, "AMBIGUOUS" if users else "UNKNOWN", tuple(users))

    # ------------------------------------------------------------------ identifier sets
    def identifiers_for(self, user_id: str) -> Identifiers:
        """Every identifier the user has ever carried (current and historical)."""
        u = self.users[user_id]
        emails = {normalize_email(i.value) for i in u.emails}
        hashes = {i.value.lower() for i in u.hashes} | {sha256_email(e) for e in emails}
        phones = {p for p in (normalize_phone(i.value) for i in u.phones) if p}
        return Identifiers(frozenset(emails), frozenset({user_id}), frozenset(hashes), frozenset(phones))

    def match_identifiers(self, ids: Identifiers, include_phones: bool = False) -> Dict[str, Set[str]]:
        """Which users do these tokens point at? Returns {user_id: {kinds that matched}}.

        Phones are off by default: a phone alone is a weak link and is never used to create ownership."""
        out: Dict[str, Set[str]] = defaultdict(set)
        for e in ids.emails:
            for u in self._email_idx.get(e, ()):
                out[u].add("email")
        for h in ids.hashes:
            for u in self._hash_idx.get(h, ()):
                out[u].add("email_hash")
        for r in ids.user_refs:
            if r in self.users:
                out[r].add("user_ref")
        if include_phones:
            for p in ids.phones:
                for u in self._phone_idx.get(p, ()):
                    out[u].add("phone")
        return dict(out)

    def all_emails(self) -> Set[str]:
        return set(self._email_idx)
