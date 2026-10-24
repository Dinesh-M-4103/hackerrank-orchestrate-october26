"""Vision configuration. Gemini is OFF by default and needs three independent opt-ins before any real request:

1. ``GHOST_AUDIT_VISION=gemini`` in the environment,
2. an API key in ``GEMINI_API_KEY`` (read from the environment only; never printed or logged),
3. the explicit ``--approve-real-calls`` flag on the command that would send images.

Unit tests, the deterministic pipeline and the fake client need none of them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

ENABLE_ENV = "GHOST_AUDIT_VISION"
ENABLE_VALUE = "gemini"
KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "GHOST_AUDIT_VISION_MODEL"

DEFAULT_MODEL = "gemini-2.5-flash"
PROMPT_VERSION = "v1"

# USD per 1M tokens (standard tier), copied by hand from https://ai.google.dev/gemini-api/docs/pricing on 2026-10-24.
# Prices change: re-verify before approving any real call. input applies to text and image tokens.
PRICING_AS_OF = "2026-10-24"
PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
PRICING = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),     # introductory price through 2026-12-31 per the pricing page
}


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = False
    provider: str = "gemini"
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    max_attempts: int = 3
    base_delay_s: float = 2.0
    max_delay_s: float = 30.0
    min_interval_s: float = 6.0          # client-side pacing between requests (<= 10 requests/minute)
    max_requests: int = 24               # hard cap per run: 11 images x at most 2 attempts, plus slack
    min_confidence: float = 0.5
    cache_dir: Optional[str] = None      # raw responses are cached by (image hash, prompt version, model)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **overrides) -> "VisionConfig":
        env = os.environ if env is None else env
        return cls(enabled=env.get(ENABLE_ENV, "").strip().lower() == ENABLE_VALUE,
                   model=env.get(MODEL_ENV, DEFAULT_MODEL), **overrides)

    def has_key(self, env: Optional[Mapping[str, str]] = None) -> bool:
        env = os.environ if env is None else env
        return bool(env.get(KEY_ENV, "").strip())
