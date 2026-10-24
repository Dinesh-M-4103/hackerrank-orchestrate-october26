"""Build a FakeVisionClient from the transcription file (tests / diagnostics only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..extractors import IMAGE_SUFFIXES
from ..models import EvidenceMeta
from .client import FakeVisionClient, image_key


def fake_client_from_fixture(path: Path, manifest: Iterable[EvidenceMeta]) -> FakeVisionClient:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    responses = {fid: e["response"] for fid, e in data["screenshots"].items()}
    outcomes = {}
    for m in manifest:
        if m.suffix in IMAGE_SUFFIXES and m.file_id in responses and Path(m.path).exists():
            outcomes[image_key(Path(m.path).read_bytes())] = json.dumps(responses[m.file_id], ensure_ascii=False)
    return FakeVisionClient(outcomes)
