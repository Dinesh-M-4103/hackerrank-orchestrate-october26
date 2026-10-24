"""Structured screenshot interpretation: what the model may return, and how it is validated.

The response separates (a) directly observed content, (b) visible identifiers, (c) displayed times, (d) *possible* links,
and (e) the model's own uncertainty. Validation is deliberately strict:

* unknown or evidence-like keys (``evidence_id``, ``activity_id``, ``audit_id``, ``file_id``, ``state``, ``status``,
  ``deletion_status``, ``operation``...) are discarded and reported: the model cannot supply provenance, states or actions;
* identifiers must have the expected shape (e-mail, ``user_NN``, a known resource-ID pattern);
* a link counts as *supported* only when it is ``same_row``/``same_panel`` and its quote visibly contains both tokens;
  anything else (filename/proximity/header, missing quote) is kept only as an unsupported hypothesis.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..taxonomy import RESOURCE_TOKEN_RE
from ..text_ids import EMAIL_RE, PHONE_RE, USER_REF_RE, normalize_email, normalize_phone

SCHEMA_VERSION = "1"

# interpretation statuses
OK, UNREADABLE, MALFORMED, UNCERTAIN, ERROR, DISABLED = "OK", "UNREADABLE", "MALFORMED", "UNCERTAIN", "ERROR", "DISABLED"

FORBIDDEN_KEYS = {"evidence_id", "activity_id", "audit_id", "file_id", "state", "status", "deletion_status", "operation",
                  "phase", "step_no", "policy", "required_action", "verdict", "compliant"}
SUPPORTED_BASES = {"same_row", "same_panel"}

RESPONSE_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean"},
        "application": {"type": "string"},
        "page_title": {"type": "string"},
        "visible_text_lines": {"type": "array", "items": {"type": "string"}},
        "identifiers": {
            "type": "object",
            "properties": {k: {"type": "array", "items": {"type": "string"}}
                           for k in ("resource_ids", "emails", "user_refs", "phones", "reference_codes")},
            "required": ["resource_ids", "emails", "user_refs", "phones", "reference_codes"],
        },
        "displayed_times": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "meaning": {"type": "string"}}, "required": ["text", "meaning"]}},
        "rows": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "resource_ids": {"type": "array", "items": {"type": "string"}},
            "emails": {"type": "array", "items": {"type": "string"}},
            "user_refs": {"type": "array", "items": {"type": "string"}},
            "phones": {"type": "array", "items": {"type": "string"}},
            "status_text": {"type": "string"}},
            "required": ["text", "resource_ids", "emails", "user_refs", "phones", "status_text"]}},
        "possible_links": {"type": "array", "items": {"type": "object", "properties": {
            "resource_id": {"type": "string"}, "subject": {"type": "string"},
            "basis": {"type": "string", "enum": ["same_row", "same_panel", "header", "filename", "proximity", "other"]},
            "quote": {"type": "string"}}, "required": ["resource_id", "subject", "basis", "quote"]}},
        "confidence": {"type": "number"},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["readable", "application", "page_title", "visible_text_lines", "identifiers", "displayed_times", "rows",
                 "possible_links", "confidence", "ambiguities", "rationale"],
}


class SchemaError(ValueError):
    """The response cannot be interpreted at all (not JSON, wrong top-level shape)."""


@dataclass
class Row:
    text: str
    resource_ids: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    user_refs: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    status_text: str = ""


@dataclass
class Link:
    resource_id: str
    subject: str
    basis: str
    quote: str
    supported: bool = False
    why_not: str = ""


@dataclass
class ImageInterpretation:
    file_id: str
    path: str
    captured_at: str
    status: str = OK
    model: str = ""
    application: str = ""
    page_title: str = ""
    visible_text_lines: List[str] = field(default_factory=list)
    identifiers: Dict[str, List[str]] = field(default_factory=dict)
    displayed_times: List[Dict[str, str]] = field(default_factory=list)
    rows: List[Row] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    confidence: float = 0.0
    ambiguities: List[str] = field(default_factory=list)
    rationale: str = ""
    discarded: List[str] = field(default_factory=list)       # model-supplied keys/values that were dropped, and why
    problems: List[str] = field(default_factory=list)        # validation or API problems
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["rows"] = [r.__dict__ for r in self.rows]
        d["links"] = [l.__dict__ for l in self.links]
        return d


def _strs(v: Any, name: str, problems: List[str]) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        problems.append(f"{name}: expected a list of strings")
        return []
    return [x.strip() for x in v if x.strip()]


def _clean_ids(raw: Dict[str, Any], problems: List[str], discarded: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for key, rx in (("emails", None), ("user_refs", None), ("resource_ids", None), ("phones", None), ("reference_codes", None)):
        vals = _strs(raw.get(key), f"identifiers.{key}", problems)
        keep = []
        for v in vals:
            if key == "emails":
                ok = EMAIL_RE.fullmatch(v.strip()) is not None
                v = normalize_email(v)
            elif key == "user_refs":
                ok = USER_REF_RE.fullmatch(v) is not None
            elif key == "resource_ids":
                ok = RESOURCE_TOKEN_RE.fullmatch(v) is not None
            elif key == "phones":
                ok = PHONE_RE.search(v) is not None
                v = normalize_phone(v)
            else:
                ok = True
            if ok:
                keep.append(v)
            else:
                discarded.append(f"identifiers.{key}: {v!r} has the wrong shape")
        out[key] = sorted(set(keep))
    return out


def parse_interpretation(raw: Any, *, file_id: str, path: str, captured_at: str, model: str = "") -> ImageInterpretation:
    """Validate a raw model response. Never raises for model mistakes: returns MALFORMED / UNREADABLE / UNCERTAIN results.
    Provenance (file_id, path, captured_at) always comes from the caller (the manifest), never from the model."""
    it = ImageInterpretation(file_id=file_id, path=path, captured_at=captured_at, model=model)
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError as exc:
        it.status, it.problems = MALFORMED, [f"response is not valid JSON: {exc}"]
        return it
    if not isinstance(data, dict):
        it.status, it.problems = MALFORMED, ["response is not a JSON object"]
        return it
    for k in data:
        if k.lower() in FORBIDDEN_KEYS:
            it.discarded.append(f"top-level key {k!r}: the model may not supply provenance, states or actions")
    if data.get("readable") is not True:
        it.status = UNREADABLE
        it.problems.append("model reported the image as unreadable" if data.get("readable") is False else "missing 'readable' flag")
        it.ambiguities = _strs(data.get("ambiguities"), "ambiguities", it.problems)
        return it

    probs = it.problems
    it.application, it.page_title = str(data.get("application") or ""), str(data.get("page_title") or "")
    it.visible_text_lines = _strs(data.get("visible_text_lines"), "visible_text_lines", probs)
    ids = data.get("identifiers")
    if not isinstance(ids, dict):
        it.status = MALFORMED
        probs.append("identifiers must be an object")
        return it
    it.identifiers = _clean_ids(ids, probs, it.discarded)

    times = data.get("displayed_times") or []
    if isinstance(times, list):
        it.displayed_times = [{"text": str(t.get("text", "")), "meaning": str(t.get("meaning", ""))} for t in times if isinstance(t, dict) and t.get("text")]
    else:
        probs.append("displayed_times must be a list")

    for i, r in enumerate(data.get("rows") or []):
        if not isinstance(r, dict) or not isinstance(r.get("text"), str):
            probs.append(f"rows[{i}] malformed")
            continue
        sub = _clean_ids({"emails": r.get("emails"), "user_refs": r.get("user_refs"), "resource_ids": r.get("resource_ids"),
                          "phones": r.get("phones")}, probs, it.discarded)
        it.rows.append(Row(r["text"], sub["resource_ids"], sub["emails"], sub["user_refs"], sub["phones"], str(r.get("status_text") or "")))

    for i, l in enumerate(data.get("possible_links") or []):
        if not isinstance(l, dict):
            probs.append(f"possible_links[{i}] malformed")
            continue
        link = Link(str(l.get("resource_id", "")).strip(), str(l.get("subject", "")).strip(), str(l.get("basis", "other")), str(l.get("quote", "")))
        quote = link.quote.lower()
        if link.basis not in SUPPORTED_BASES:
            link.why_not = f"basis {link.basis!r} is not direct visual grouping (filenames, headers and proximity never establish a link)"
        elif RESOURCE_TOKEN_RE.fullmatch(link.resource_id) is None:
            link.why_not = "resource_id has an unknown shape"
        elif link.resource_id.lower() not in quote or link.subject.lower() not in quote:
            link.why_not = "the quoted visible text does not contain both the resource and the subject"
        elif not (EMAIL_RE.fullmatch(link.subject) or USER_REF_RE.fullmatch(link.subject)):
            link.why_not = "subject is neither an e-mail address nor a user_NN reference"
        else:
            link.supported = True
        it.links.append(link)

    try:
        it.confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        it.confidence = 0.0
        probs.append("confidence is not a number")
    it.ambiguities = _strs(data.get("ambiguities"), "ambiguities", probs)
    it.rationale = str(data.get("rationale") or "")
    _flag_contradictions(it)
    return it


def _flag_contradictions(it: ImageInterpretation) -> None:
    """The same resource tied to two different subjects (in rows or supported links) is contradictory evidence."""
    subjects: Dict[str, set] = {}
    for r in it.rows:
        for rid in r.resource_ids:
            subs = set(r.emails) | set(r.user_refs)
            if subs:
                subjects.setdefault(rid, set()).update(subs)
    clash = {rid: s for rid, s in subjects.items() if len({x for x in s if "@" in x}) > 1 or len({x for x in s if x.startswith("user_")}) > 1}
    if clash:
        it.status = UNCERTAIN
        it.problems.append(f"contradictory subjects for {sorted(clash)}")
    for l in it.links:
        if l.supported and l.resource_id in subjects and normalize_email(l.subject) not in {normalize_email(x) for x in subjects[l.resource_id]} \
                and l.subject not in subjects[l.resource_id]:
            it.status = UNCERTAIN
            it.problems.append(f"link {l.resource_id}~{l.subject} contradicts the rows of the same image")
