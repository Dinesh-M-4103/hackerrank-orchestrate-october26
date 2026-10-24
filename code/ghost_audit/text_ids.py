"""Identifier tokenization.

Matching is exact-token and case-insensitive: a look-alike address that merely *contains* another address
(``ca.eze08@x.com`` vs ``a.eze08@x.com``) never matches it, because the extractor takes the whole token.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, FrozenSet

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
USER_REF_RE = re.compile(r"(?<![A-Za-z0-9_])user_\d+(?![A-Za-z0-9_])")
HASH_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9a-fA-F]{64}(?![0-9A-Fa-f])")
PHONE_RE = re.compile(r"\+\d[\d\s().\-]{6,}\d")


def normalize_email(value: str) -> str:
    return value.strip().strip(".,;:'\"<>()[]").lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return ("+" + digits) if digits else ""


@dataclass(frozen=True)
class Identifiers:
    emails: FrozenSet[str] = frozenset()
    user_refs: FrozenSet[str] = frozenset()
    hashes: FrozenSet[str] = frozenset()
    phones: FrozenSet[str] = frozenset()

    def is_empty(self) -> bool:
        return not (self.emails or self.user_refs or self.hashes or self.phones)

    def union(self, other: "Identifiers") -> "Identifiers":
        return Identifiers(
            self.emails | other.emails,
            self.user_refs | other.user_refs,
            self.hashes | other.hashes,
            self.phones | other.phones,
        )

    def to_dict(self) -> dict:
        return {
            "emails": sorted(self.emails),
            "user_refs": sorted(self.user_refs),
            "hashes": sorted(self.hashes),
            "phones": sorted(self.phones),
        }


EMPTY_IDS = Identifiers()


def identifiers_in_text(text: str, with_phones: bool = True) -> Identifiers:
    """Extract identifier tokens from free text."""
    if not text:
        return EMPTY_IDS
    phones: FrozenSet[str] = frozenset()
    if with_phones:
        phones = frozenset(p for p in (normalize_phone(m) for m in PHONE_RE.findall(text)) if p)
    return Identifiers(
        emails=frozenset(normalize_email(m) for m in EMAIL_RE.findall(text)),
        user_refs=frozenset(USER_REF_RE.findall(text)),
        hashes=frozenset(h.lower() for h in HASH_RE.findall(text)),
        phones=phones,
    )


def identifiers_in_value(value: Any) -> Identifiers:
    """Extract identifier tokens from any JSON-like value; values under phone-ish keys are normalized as phones."""
    emails, refs, hashes, phones = set(), set(), set(), set()

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif isinstance(v, str):
            ids = identifiers_in_text(v, with_phones=False)
            emails.update(ids.emails)
            refs.update(ids.user_refs)
            hashes.update(ids.hashes)
            if "phone" in key.lower():
                p = normalize_phone(v)
                if p:
                    phones.add(p)

    walk(value)
    return Identifiers(frozenset(emails), frozenset(refs), frozenset(hashes), frozenset(phones))
