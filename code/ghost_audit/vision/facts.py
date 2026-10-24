"""Turn a validated ImageInterpretation into ordinary Facts (deterministically; the model decides nothing here).

Rules (documented, tested):

* Only rows that visibly show a resource ID produce a fact. File names, page titles and filename-style pairings never do.
* A fact's identifiers are only the subjects of *supported* links for that resource (same row/panel, quote contains both
  tokens). A row with a resource but no supported link is kept as an anonymous observation: the resource was seen, no
  owner is established.
* ``state`` is always PRESENT (the record was on screen). The only state-like attributes derived from words are
  ``enabled`` for data flows, from a fixed vocabulary; identifiers visible in a row become the attributes that make
  the existing deterministic interpreter see an identifiable record. Not seeing identifiers on screen is NOT evidence
  of anonymization, and a backup row with no restore information stays PRESENT (restore access unknown).
* Time: ``captured_at`` is the manifest time and is the authority for whether the screenshot may be used at all (specification:
  evidence captured after audit_at is inadmissible). Admissibility is then checked *per row*: if a row itself displays a
  date/time later than its capture time, ``observed_at`` is set to that time, so the row is inadmissible for any case audited
  before it (a record created after the cutoff did not exist at the cutoff). A page that displays times later than its manifest
  capture time is flagged (``temporal_conflict``) but the other rows are not discarded for it.
* Corroboration: screens that list many unrelated events for one subject (audit-event explorers) only contribute rows that an
  explicit catalog link row also names (``CORROBORATION_REQUIRED``); a visible subject alone does not make every row a case resource.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from ..models import EvidenceMeta, ExtractionResult, Fact
from ..taxonomy import category_for_resource, resource_prefix, system_for_resource
from ..text_ids import Identifiers, normalize_email
from .schema import ImageInterpretation
from ..timeutil import minutes_between
from .timeparse import latest_displayed, parse_display_time

TEMPORAL_CONFLICT_TOLERANCE_MIN = 5.0

# displayed dates that describe the future or a range are not evidence of when the screen was captured
NOT_CAPTURE_TIME_WORDS = ("expir", "expected", "retention", "until", "filter", "range", "scheduled", "due", "valid to", "ttl")

CORROBORATION_REQUIRED = {"SECURITY_AUDIT_LOG"}

_ROW_TIME_RES = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?\s*(?:Z|UTC)?"),
    re.compile(r"\d{1,2}\s+[A-Za-z]{3}[A-Za-z]*\.?(?:\s+\d{4})?[ ,]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|JST|KST|Z))?"),
]


def row_time(text: str, captured_at: str) -> Optional[str]:
    """Latest absolute date/time written in a row's own text (time-of-day alone carries no date and is ignored)."""
    found = []
    for rx in _ROW_TIME_RES:
        for m in rx.finditer(text or ""):
            p = parse_display_time(m.group(0), captured_at)
            if p:
                found.append(p.iso)
    return max(found) if found else None


ENABLED_WORDS = ("continuous", "healthy", "enabled", "running", "active", "syncing")
DISABLED_WORDS = ("disabled", "paused", "stopped", "inactive", "suspended")


def _enabled_from_words(*texts: str):
    blob = " ".join(texts).lower()
    if any(w in blob for w in DISABLED_WORDS):
        return False
    if any(w in blob for w in ENABLED_WORDS):
        return True
    return None


def to_extraction(meta: EvidenceMeta, it: ImageInterpretation, corroborated: Optional[Iterable[str]] = None) -> ExtractionResult:
    """``corroborated``: resource IDs that an explicit catalog link row names (needed for CORROBORATION_REQUIRED categories)."""
    corroborated = set(corroborated or ())
    res = ExtractionResult(file_id=meta.file_id, kind="image_interpreted")
    if not it.usable:
        res.kind, res.unsupported = "image", True
        res.warnings.append(f"screenshot not interpreted ({it.status}): " + "; ".join(it.problems[:3]))
        return res

    shown = latest_displayed([t["text"] for t in it.displayed_times
                              if not any(w in t["meaning"].lower() for w in NOT_CAPTURE_TIME_WORDS)], meta.captured_at)
    # a real gap only (manifest times are rounded): flagged, but it does not by itself make the other rows inadmissible
    conflict = bool(shown and minutes_between(shown, meta.captured_at) > TEMPORAL_CONFLICT_TOLERANCE_MIN)
    if conflict:
        res.warnings.append(f"temporal_conflict: the screenshot displays {shown}, later than its manifest capture time {meta.captured_at}")
    for l in it.links:
        if not l.supported:
            res.warnings.append(f"unsupported link hypothesis {l.resource_id}~{l.subject}: {l.why_not}")

    supported: Dict[str, List[str]] = {}
    for l in it.links:
        if l.supported:
            supported.setdefault(l.resource_id, []).append(normalize_email(l.subject) if "@" in l.subject else l.subject)

    for i, row in enumerate(it.rows):
        res.lines_total += 1
        res.lines_parsed += 1
        for rid in row.resource_ids:
            if not resource_prefix(rid):
                continue
            subjects = sorted(set(supported.get(rid, [])))
            emails = frozenset(s for s in subjects if "@" in s)
            refs = frozenset(s for s in subjects if s.startswith("user_"))
            category = category_for_resource(rid)
            if category in CORROBORATION_REQUIRED and rid not in corroborated:
                res.warnings.append(f"row for {rid} skipped: {category} rows need an explicit catalog link row naming the resource")
                continue
            rt = row_time(row.text, meta.captured_at)
            observed = rt if (rt and rt > meta.captured_at) else None       # a row dated after capture is only valid from that time on
            attrs: dict = {"source": "image", "application": it.application, "row_text": row.text, "visible_status": row.status_text,
                           "latest_displayed": shown, "temporal_conflict": conflict, "identifiers_visible": bool(subjects), "row_time": rt}
            if category == "DATA_FLOW":
                en = _enabled_from_words(row.status_text, row.text)
                if en is not None:
                    attrs["enabled"] = en
            if emails or refs:
                if category == "ANALYTICS_PROFILE":
                    attrs["user_id"], attrs["email"] = (sorted(refs) or [None])[0], (sorted(emails) or [None])[0]
                elif category == "SUPPORT_MESSAGE":
                    attrs["requester_email"] = (sorted(emails) or [None])[0]
                elif category == "ACCOUNT_TOMBSTONE":
                    attrs["email"] = (sorted(emails) or [None])[0]
            res.facts.append(Fact(
                file_id=meta.file_id, captured_at=meta.captured_at, system_id=system_for_resource(rid), resource_id=rid,
                category=category, state="PRESENT", identifiers=Identifiers(emails=emails, user_refs=refs), observed_at=observed,
                attrs=attrs, locator=f"image row {i}"))
    return res
