"""Run a vision client over screenshot evidence, with caching, validation and structured failure results."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from ..extractors import IMAGE_SUFFIXES
from ..models import EvidenceMeta
from .client import (BudgetExceeded, ModelResponse, ResilientClient, UsageTracker, VisionClient, VisionError, image_key)
from .config import PROMPT_VERSION, VisionConfig
from .schema import (DISABLED, ERROR, RESPONSE_JSON_SCHEMA, SCHEMA_VERSION, UNREADABLE, ImageInterpretation, parse_interpretation)

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


class ScreenshotInterpreter:
    def __init__(self, client: Optional[VisionClient], config: VisionConfig, usage: Optional[UsageTracker] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.usage = usage if usage is not None else UsageTracker()
        self._resilient = ResilientClient(client, config, self.usage, sleep, clock) if client is not None else None
        self._cache_dir = Path(config.cache_dir) if config.cache_dir else None
        self._aborted: Optional[str] = None

    # ------------------------------------------------------------------ cache (raw responses only)
    def _cache_path(self, key: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{key}.{self.config.model}.{PROMPT_VERSION}.{SCHEMA_VERSION}.json"

    def _cache_get(self, key: str) -> Optional[str]:
        p = self._cache_path(key)
        try:
            return json.loads(p.read_text(encoding="utf-8"))["raw"] if p and p.exists() else None
        except (OSError, ValueError, KeyError):
            return None

    def _cache_put(self, key: str, raw: str) -> None:
        p = self._cache_path(key)
        if p is None:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"raw": raw}), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ one image
    def interpret(self, meta: EvidenceMeta) -> ImageInterpretation:
        base = dict(file_id=meta.file_id, path=meta.rel_path, captured_at=meta.captured_at, model=self.config.model)
        if not self.config.enabled or self._resilient is None:
            it = ImageInterpretation(status=DISABLED, **base)
            it.problems.append("vision is disabled (no request was made)")
            return it
        if self._aborted:
            it = ImageInterpretation(status=ERROR, **base)
            it.problems.append(f"skipped after a fatal error: {self._aborted}")
            return it
        try:
            data = Path(meta.path).read_bytes()
        except OSError as exc:
            it = ImageInterpretation(status=UNREADABLE, **base)
            it.problems.append(f"image file cannot be read ({type(exc).__name__})")
            return it
        mime = _MIME.get(meta.suffix)
        if mime is None:
            it = ImageInterpretation(status=UNREADABLE, **base)
            it.problems.append(f"unsupported image type {meta.suffix}")
            return it

        key = image_key(data)
        raw = self._cache_get(key)
        if raw is not None:
            self.usage.record(kind="cache_hit", label=meta.file_id)
            usage = {"cache_hit": True}
        else:
            try:
                resp: ModelResponse = self._resilient.generate(data, mime, RESPONSE_JSON_SCHEMA, label=meta.file_id)
            except VisionError as exc:
                it = ImageInterpretation(status=ERROR, **base)
                it.problems.append(f"{type(exc).__name__}: {exc}")
                if getattr(exc, "fatal", False):
                    self._aborted = type(exc).__name__
                return it
            raw, usage = resp.text, {"input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens}
        it = parse_interpretation(raw, model=self.config.model, **{k: base[k] for k in ("file_id", "path", "captured_at")})
        it.usage = usage
        if it.status in ("OK", "UNCERTAIN") and "cache_hit" not in usage:
            self._cache_put(key, raw)
        if it.status == "OK" and it.confidence < self.config.min_confidence:
            it.status = "UNCERTAIN"
            it.problems.append(f"confidence {it.confidence:.2f} is below the minimum {self.config.min_confidence:.2f}")
        return it

    def interpret_all(self, manifest: Iterable[EvidenceMeta]) -> Dict[str, ImageInterpretation]:
        return {m.file_id: self.interpret(m) for m in manifest if m.suffix in IMAGE_SUFFIXES}
