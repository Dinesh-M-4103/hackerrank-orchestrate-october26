"""PDF business documents (statements, support attachments, registers, compliance extracts), via pypdf.

The PDFs carry no explicit deletion verdict; they are ordinary artifacts. We extract the facts they contain:
resource IDs mentioned, identifier tokens, reference tokens (ORD-/NST-/CS-/CJX-) and, for retention registers,
one fact per register row.
"""
from __future__ import annotations

import re
from typing import List

from ..models import EvidenceMeta, ExtractionResult
from ..taxonomy import RESOURCE_TOKEN_RE, resource_prefix
from ..text_ids import identifiers_in_text
from .common import make_fact

REF_RE = re.compile(r"\b(?:ORD|NST|CS|CJX)-[A-Za-z0-9\-]*\d\b")
ROW_RE = re.compile(r"(ORD-\d+)\s*\|\s*(user_\d+)\s*\|(?P<rest>.*)")
RETAIN_RE = re.compile(r"Retain until\s+(\d{1,2}\s+\w{3}\s+\d{4})", re.I)


def pdf_text(path) -> str:
    from pypdf import PdfReader  # imported lazily so the rest of the package works without it

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_pdf(meta: EvidenceMeta) -> ExtractionResult:
    res = ExtractionResult(file_id=meta.file_id, kind="pdf")
    text = pdf_text(meta.path)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    res.lines_total = len(lines)
    if not lines:
        res.warnings.append("no extractable text")
        return res
    res.lines_parsed = len(lines)
    full = "\n".join(lines)

    # retention-register rows: one fact per row
    rows = [(i, ROW_RE.search(ln)) for i, ln in enumerate(lines)]
    rows = [(i, m) for i, m in rows if m]
    for i, m in rows:
        retain = RETAIN_RE.search(m.group("rest"))
        res.facts.append(
            make_fact(
                file_id=meta.file_id,
                captured_at=meta.captured_at,
                resource_id=None,
                state="RETAINED_REFERENCE",
                system_id="legal_service",
                category="LEGAL_HOLD_RECORD",
                identifiers=identifiers_in_text(m.group(2), with_phones=False),
                attrs={"retain_until_text": retain.group(1) if retain else None, "row": m.group(0)},
                refs=(m.group(1),),
                locator=f"line {i + 1}",
            )
        )
    if rows:
        return res

    ids = identifiers_in_text(full)
    refs = tuple(sorted(set(REF_RE.findall(full))))
    resources: List[str] = sorted({r for r in RESOURCE_TOKEN_RE.findall(full) if resource_prefix(r)})
    is_attachment = "attachment" in full.lower()
    for rid in resources:
        category = "SUPPORT_ATTACHMENT" if (is_attachment and resource_prefix(rid) == "support") else None
        res.facts.append(
            make_fact(
                file_id=meta.file_id,
                captured_at=meta.captured_at,
                resource_id=rid,
                state="FOUND",
                category=category,
                identifiers=ids,
                attrs={"document_title": lines[0]},
                refs=refs,
                locator="document",
            )
        )
    if not resources:
        res.facts.append(
            make_fact(
                file_id=meta.file_id,
                captured_at=meta.captured_at,
                resource_id=None,
                state="DOCUMENT",
                identifiers=ids,
                attrs={"document_title": lines[0]},
                refs=refs,
                locator="document",
            )
        )
    return res
