"""Screenshot evidence from a committed transcription file (the deterministic stand-in for a runtime vision model).

The file ``code/evaluation/screenshot_transcriptions.json`` holds what was read off the 11 PNG screenshots, in the same
structured shape the Gemini interpreter returns, with the SHA-256 of each image. Loading it re-runs the normal response
validation, so a transcription is held to exactly the rules a model response would be: shape checks, visibly grounded links,
no model-supplied evidence IDs/states/actions, manifest provenance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, Optional

from ..extractors import IMAGE_SUFFIXES
from ..models import EvidenceMeta
from ..paths import CODE_DIR
from .schema import ERROR, ImageInterpretation, parse_interpretation

TRANSCRIPTION_FILE = CODE_DIR / "evaluation" / "screenshot_transcriptions.json"
METHOD = "agent-transcription (Claude Code, claude-sonnet-5-5; read by eye during development)"


def load_transcribed_interpretations(manifest: Iterable[EvidenceMeta], path: Optional[Path] = None) -> Dict[str, ImageInterpretation]:
    """Interpretations for every manifest image that has a transcription whose recorded SHA-256 matches the image on disk.
    A mismatch yields an ERROR interpretation (no facts); images without an entry are simply absent (they stay unsupported)."""
    path = Path(path) if path is not None else TRANSCRIPTION_FILE
    if not path.exists():
        return {}
    shots = json.loads(path.read_text(encoding="utf-8")).get("screenshots", {})
    out: Dict[str, ImageInterpretation] = {}
    for m in manifest:
        entry = shots.get(m.file_id)
        if entry is None or m.suffix not in IMAGE_SUFFIXES:
            continue
        base = dict(file_id=m.file_id, path=m.rel_path, captured_at=m.captured_at, model=METHOD)
        try:
            digest = hashlib.sha256(Path(m.path).read_bytes()).hexdigest()
        except OSError:
            it = ImageInterpretation(status=ERROR, **base)
            it.problems.append("image file cannot be read")
            out[m.file_id] = it
            continue
        if digest != entry.get("sha256") or entry.get("file") != m.rel_path:
            it = ImageInterpretation(status=ERROR, **base)
            it.problems.append("the image on disk differs from the image that was transcribed (SHA-256 or path mismatch); transcription not used")
            out[m.file_id] = it
            continue
        out[m.file_id] = parse_interpretation(entry["response"], model=METHOD, file_id=m.file_id, path=m.rel_path, captured_at=m.captured_at)
    return out
