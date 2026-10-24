"""Evidence extractors: one module per format, dispatched by file type."""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from ..models import EvidenceMeta, ExtractionResult
from .handover_txt import extract_handover_txt
from .json_records import extract_json
from .logs import extract_log
from .pdf_doc import extract_pdf
from .sql_snapshot import extract_sql

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def extract_file(meta: EvidenceMeta) -> ExtractionResult:
    """Extract facts from one evidence file. Unsupported formats (images) return ``unsupported=True`` rather than
    raising, so coverage reports can list them; any parser failure is captured in ``error``."""
    suffix = meta.suffix
    try:
        if suffix == ".txt":
            return extract_handover_txt(meta)
        if suffix == ".json":
            return extract_json(meta)
        if suffix == ".sql":
            return extract_sql(meta)
        if suffix == ".log":
            return extract_log(meta)
        if suffix == ".pdf":
            return extract_pdf(meta)
        if suffix in IMAGE_SUFFIXES:
            return ExtractionResult(file_id=meta.file_id, kind="image", unsupported=True,
                                    warnings=["image evidence needs a vision model (not run locally)"])
        return ExtractionResult(file_id=meta.file_id, kind="unknown", unsupported=True, warnings=[f"unsupported suffix {suffix}"])
    except Exception as exc:  # noqa: BLE001 - a failing parser must be reported, never hidden
        res = ExtractionResult(file_id=meta.file_id, kind=f"error:{suffix}")
        res.error = f"{type(exc).__name__}: {exc}"
        return res


def extract_all(manifest: Iterable[EvidenceMeta]) -> Dict[str, ExtractionResult]:
    return {m.file_id: extract_file(m) for m in manifest}
