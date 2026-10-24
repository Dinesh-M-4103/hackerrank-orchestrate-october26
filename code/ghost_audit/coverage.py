"""Parse-coverage check: every non-image evidence file must exist, parse without error, and have every line/record
accounted for. Images are reported as needing a vision model (they are never silently skipped)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .extractors import extract_all
from .models import EvidenceMeta, ExtractionResult


@dataclass
class FileCoverage:
    file_id: str
    path: str
    kind: str
    exists: bool
    facts: int = 0
    scopes: int = 0
    lines_total: int = 0
    lines_parsed: int = 0
    warnings: int = 0
    error: Optional[str] = None
    unsupported: bool = False

    @property
    def ok(self) -> bool:
        if self.unsupported:
            return True       # reported separately; not a parser failure
        return self.exists and self.error is None and self.lines_parsed == self.lines_total and self.warnings == 0


@dataclass
class CoverageReport:
    files: List[FileCoverage] = field(default_factory=list)

    @property
    def failures(self) -> List[FileCoverage]:
        return [f for f in self.files if not f.ok]

    @property
    def unsupported(self) -> List[FileCoverage]:
        return [f for f in self.files if f.unsupported]

    @property
    def empty_but_scoped(self) -> List[FileCoverage]:
        """Parsed files with no facts but at least one scope (an empty snapshot is evidence of absence)."""
        return [f for f in self.files if not f.unsupported and f.facts == 0 and f.scopes > 0]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        n = len(self.files)
        sup = self.unsupported
        lines = [f"evidence files: {n}; parsed OK: {n - len(sup) - len(self.failures)}; "
                 f"unsupported locally (need vision model): {len(sup)}; failures: {len(self.failures)}",
                 f"facts: {sum(f.facts for f in self.files)}; scopes: {sum(f.scopes for f in self.files)}"]
        if sup:
            lines.append("unsupported: " + ", ".join(f"{f.file_id}({f.path.rsplit('/', 1)[-1]})" for f in sup))
        if self.empty_but_scoped:
            lines.append("empty snapshots (scope only): " + ", ".join(f.file_id for f in self.empty_but_scoped))
        for f in self.failures:
            lines.append(f"FAIL {f.file_id} {f.path}: parsed {f.lines_parsed}/{f.lines_total}, warnings={f.warnings}, "
                         f"error={f.error}, exists={f.exists}")
        return "\n".join(lines)


def coverage_for(manifest: Iterable[EvidenceMeta], results: Optional[Dict[str, ExtractionResult]] = None) -> CoverageReport:
    manifest = list(manifest)
    results = results if results is not None else extract_all(m for m in manifest if m.path.exists())
    rep = CoverageReport()
    for m in manifest:
        exists = m.path.exists()
        r = results.get(m.file_id)
        if r is None:
            rep.files.append(FileCoverage(m.file_id, m.rel_path, "missing", exists, error=None if exists else "file not found"))
            continue
        rep.files.append(FileCoverage(m.file_id, m.rel_path, r.kind, exists, len(r.facts), len(r.scopes), r.lines_total,
                                      r.lines_parsed, len(r.warnings), r.error, r.unsupported))
    return rep
