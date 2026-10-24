"""Offline request/token/cost estimate for the screenshot run (no network, no SDK needed)."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .config import PRICING, PRICING_AS_OF, PRICING_SOURCE

PROMPT_TOKENS = 700                 # fixed instruction text + response schema (estimate)
OUTPUT_TOKENS_LOW = 900             # compact transcription of one console screen
OUTPUT_TOKENS_HIGH = 3500           # dense screen, plus any thinking tokens the model spends


def png_size(path: Path) -> Tuple[int, int]:
    head = Path(path).read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def image_tokens(width: int, height: int) -> int:
    """Gemini image input tokens per the official image-understanding page: 258 if both sides <= 384 px, otherwise
    768x768-style tiles of 258 tokens, estimated as ceil(w/u)*ceil(h/u) with u = floor(min(w, h) / 1.5)."""
    if width <= 384 and height <= 384:
        return 258
    unit = max(1, math.floor(min(width, height) / 1.5))
    return 258 * math.ceil(width / unit) * math.ceil(height / unit)


@dataclass
class Estimate:
    model: str
    images: int
    requests_expected: int
    requests_worst_case: int
    input_tokens: int
    output_tokens_low: int
    output_tokens_high: int
    cost_low: Optional[float]
    cost_high: Optional[float]
    cost_worst_case: Optional[float]
    per_image: List[dict]

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def estimate(paths: Iterable[Path], model: str, max_attempts: int = 3) -> Estimate:
    per_image, tokens_in = [], 0
    for p in paths:
        w, h = png_size(p)
        t = image_tokens(w, h) + PROMPT_TOKENS
        per_image.append({"file": Path(p).name, "width": w, "height": h, "input_tokens": t})
        tokens_in += t
    n = len(per_image)
    price = PRICING.get(model)
    if price is None:
        low = high = worst = None
    else:
        pin, pout = price
        low = (tokens_in * pin + n * OUTPUT_TOKENS_LOW * pout) / 1e6
        high = (tokens_in * pin + n * OUTPUT_TOKENS_HIGH * pout) / 1e6
        worst = max_attempts * high
    return Estimate(model, n, n, n * max_attempts, tokens_in, n * OUTPUT_TOKENS_LOW, n * OUTPUT_TOKENS_HIGH, low, high, worst, per_image)


def describe(e: Estimate) -> str:
    cost = "unknown model: no price on file" if e.cost_low is None else (
        f"${e.cost_low:.3f} - ${e.cost_high:.3f} expected; worst case with every request retried {e.requests_worst_case // max(e.images, 1)}x: ${e.cost_worst_case:.3f}")
    return (f"model={e.model} images={e.images} requests={e.requests_expected} (worst case {e.requests_worst_case})\n"
            f"input tokens ~{e.input_tokens:,}; output tokens ~{e.output_tokens_low:,} - {e.output_tokens_high:,}\n"
            f"estimated cost: {cost}\n"
            f"(prices copied on {PRICING_AS_OF} from {PRICING_SOURCE}; re-verify before approval)")
