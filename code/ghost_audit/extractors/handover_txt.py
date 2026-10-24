"""Pipe-delimited handover / check-list text files.

Line shape: ``- <ts> | <system> | <resource> | <CATEGORY> | <STATE> | <identifier>`` where the identifier is an
email (any case) or a ``user_NN`` reference. A ``Snapshot: <ts>`` header gives the file-level time.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import EvidenceMeta, ExtractionResult
from ..taxonomy import resource_prefix
from ..text_ids import identifiers_in_text
from ..timeutil import norm_ts
from .common import make_fact

_SNAPSHOT_RE = re.compile(r"^\s*Snapshot:\s*(\S+)", re.I)


def extract_handover_txt(meta: EvidenceMeta) -> ExtractionResult:
    res = ExtractionResult(file_id=meta.file_id, kind="handover_txt")
    for n, line in enumerate(Path(meta.path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.lstrip("- ").split("|")]
        if len(parts) >= 6 and resource_prefix(parts[2]):
            res.lines_total += 1
            ts, system, resource, category, state, ident = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
            res.facts.append(
                make_fact(
                    file_id=meta.file_id,
                    captured_at=meta.captured_at,
                    resource_id=resource,
                    state=state.upper().replace(" ", "_"),
                    system_id=system,
                    category=category,
                    identifiers=identifiers_in_text(ident, with_phones=False),
                    observed_at=norm_ts(ts),
                    attrs={"subject_text": ident},
                    locator=f"line {n}",
                )
            )
            res.lines_parsed += 1
        elif _SNAPSHOT_RE.match(line) or line.strip().isupper() or line.rstrip().endswith(":"):
            continue  # header / title / section label
        elif "|" in line:
            res.lines_total += 1
            res.warnings.append(f"line {n}: unparsed pipe row")
    return res
