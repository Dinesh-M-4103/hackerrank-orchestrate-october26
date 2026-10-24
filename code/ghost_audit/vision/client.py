"""Vision provider interface, a fake client for tests, a resilient wrapper, and the (gated) Gemini client.

What is sent to a provider: one image and the fixed instruction prompt. Never case IDs, request text, identity records,
other evidence or the dataset. API keys are read from the environment only and never appear in messages, logs or reports.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Union

from .config import KEY_ENV, VisionConfig

PROMPT = """You are transcribing ONE screenshot of an internal business console (all people and data are fictional).
Return JSON matching the supplied schema and nothing else.

Rules:
- Transcribe only text that is actually visible. Never guess, complete or "fix" text. If something is cut off, blurred or
  unreadable, say so in `ambiguities`; if the whole screen cannot be read, set `readable` to false.
- Do NOT decide whether any data should be deleted, kept, redacted or anonymized, and do not judge compliance.
- `rows`: one entry per table row and one per detail panel that shows a resource ID (for example account_5762,
  search_428, support_1034, data_flow_1121, export_1260, backup_9287, analytics_7438, audit_record_003, marketing_001).
  Put in each row only the e-mails, user_NN references and phone numbers visible in THAT row or panel.
  `status_text` is the visible status/mode wording of that row (for example "Healthy", "Continuous", "Ready"), or "".
- `displayed_times`: every date/time visible on the screen, exactly as written, with a short `meaning`.
- `possible_links`: only when a resource ID and an e-mail or user_NN appear in the SAME row or the SAME detail panel.
  `basis` is "same_row" or "same_panel" in that case, and `quote` must be the visible text containing both. File names,
  page headers and mere proximity are NOT links: use basis "filename", "header" or "proximity" for those.
- `confidence` is between 0 and 1. `rationale` is one or two sentences about what is visible.
"""


# ---------------------------------------------------------------------------------------------------- errors
class VisionError(Exception):
    """Base class. Messages never contain credentials."""


class RateLimitError(VisionError):
    retryable = True


class TimeoutError_(VisionError):
    retryable = True


class TransientError(VisionError):
    retryable = True


class AuthError(VisionError):
    retryable = False
    fatal = True


class InvalidRequestError(VisionError):
    """HTTP 400/404-style rejection (bad schema, unknown model, no access to the model). Every image shares the same prompt,
    schema and model, so the same rejection would recur for all of them: it stops the run instead of burning the budget."""
    retryable = False
    fatal = True


class BudgetExceeded(VisionError):
    retryable = False
    fatal = True


class VisionDisabled(VisionError):
    retryable = False
    fatal = True


def classify_api_error(code: Optional[int]) -> VisionError:
    """Map an HTTP status to a retry class (pure function, unit-tested)."""
    if code == 429:
        return RateLimitError("rate limited (HTTP 429)")
    if code in (408, 504):
        return TimeoutError_(f"timeout (HTTP {code})")
    if code is not None and 500 <= code < 600:
        return TransientError(f"server error (HTTP {code})")
    if code in (401, 403):
        return AuthError(f"authentication/permission error (HTTP {code})")
    return InvalidRequestError(f"request rejected (HTTP {code})")


# ---------------------------------------------------------------------------------------------------- protocol
@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class VisionClient(Protocol):
    def generate(self, image: bytes, mime_type: str, prompt: str, schema: Mapping[str, Any], model: str) -> ModelResponse: ...


def image_key(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()


# ---------------------------------------------------------------------------------------------------- fake client
Outcome = Union[str, Exception, ModelResponse]


class FakeVisionClient:
    """Deterministic stand-in keyed by the image's SHA-256. An outcome is a raw response string, a ModelResponse, an
    exception to raise, or a list of those consumed one per call (to script retry behaviour). Never touches the network."""

    def __init__(self, outcomes: Optional[Mapping[str, Union[Outcome, List[Outcome]]]] = None, default: Optional[Outcome] = None):
        self._outcomes = {k: (list(v) if isinstance(v, list) else [v]) for k, v in (outcomes or {}).items()}
        self._default = default
        self.calls: List[dict] = []

    def generate(self, image: bytes, mime_type: str, prompt: str, schema: Mapping[str, Any], model: str) -> ModelResponse:
        key = image_key(image)
        self.calls.append({"image_sha256": key, "mime_type": mime_type, "prompt_chars": len(prompt), "model": model})
        queue = self._outcomes.get(key)
        outcome = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self._default
        if outcome is None:
            raise InvalidRequestError("fake client has no outcome for this image")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, ModelResponse) else ModelResponse(outcome, 0, 0)


# ---------------------------------------------------------------------------------------------------- usage + resilience
@dataclass
class UsageTracker:
    entries: List[dict] = field(default_factory=list)

    def record(self, **kw) -> None:
        self.entries.append(kw)

    @property
    def requests(self) -> int:
        return sum(1 for e in self.entries if e.get("kind") == "request")

    def totals(self) -> dict:
        ok = [e for e in self.entries if e.get("kind") == "request" and e.get("ok")]
        return {"requests": self.requests, "successful": len(ok), "input_tokens": sum(e.get("input_tokens", 0) for e in ok),
                "output_tokens": sum(e.get("output_tokens", 0) for e in ok),
                "cache_hits": sum(1 for e in self.entries if e.get("kind") == "cache_hit")}


class ResilientClient:
    """Retries retryable errors with exponential backoff, paces requests, enforces a hard request budget, and records usage."""

    def __init__(self, inner: VisionClient, config: VisionConfig, usage: Optional[UsageTracker] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.inner, self.config = inner, config
        self.usage = usage if usage is not None else UsageTracker()
        self._sleep, self._clock = sleep, clock
        self._last: Optional[float] = None

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.config.min_interval_s - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def generate(self, image: bytes, mime_type: str, schema: Mapping[str, Any], label: str = "") -> ModelResponse:
        last: Optional[VisionError] = None
        for attempt in range(1, self.config.max_attempts + 1):
            if self.usage.requests >= self.config.max_requests:
                raise BudgetExceeded(f"request budget of {self.config.max_requests} exhausted")
            self._pace()
            t0 = self._clock()
            try:
                resp = self.inner.generate(image, mime_type, PROMPT, schema, self.config.model)
            except VisionError as exc:
                self.usage.record(kind="request", label=label, attempt=attempt, ok=False, error=type(exc).__name__, seconds=self._clock() - t0)
                if not getattr(exc, "retryable", False):
                    raise
                last = exc
                if attempt < self.config.max_attempts:
                    self._sleep(min(self.config.max_delay_s, self.config.base_delay_s * 2 ** (attempt - 1)))
                continue
            self.usage.record(kind="request", label=label, attempt=attempt, ok=True, input_tokens=resp.input_tokens,
                              output_tokens=resp.output_tokens, seconds=self._clock() - t0)
            return resp
        assert last is not None
        raise last


# ---------------------------------------------------------------------------------------------------- real client (gated)
class GeminiVisionClient:
    """Google Gemini via the ``google-genai`` SDK. Constructing it is the only place an API key is read."""

    def __init__(self, api_key: str, timeout_s: float):
        from google import genai            # optional dependency, imported lazily
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def generate(self, image: bytes, mime_type: str, prompt: str, schema: Mapping[str, Any], model: str) -> ModelResponse:
        types = self._types
        try:
            resp = self._client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image, mime_type=mime_type), prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=dict(schema),
                                                   temperature=0.0, max_output_tokens=8192),
            )
        except Exception as exc:  # noqa: BLE001 - translate every SDK/transport failure; never echo the message (may hold URLs/keys)
            code = getattr(exc, "code", None)
            if isinstance(code, int):
                raise classify_api_error(code) from None
            if "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError):
                raise TimeoutError_("request timed out") from None
            raise TransientError(f"transport failure ({type(exc).__name__})") from None
        meta = getattr(resp, "usage_metadata", None)
        return ModelResponse(resp.text or "", int(getattr(meta, "prompt_token_count", 0) or 0),
                             int((getattr(meta, "response_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)))


def build_real_client(config: VisionConfig, approve_real_calls: bool, env: Optional[Mapping[str, str]] = None) -> GeminiVisionClient:
    """The single gate to the network: every opt-in must be present or a VisionDisabled error names what is missing."""
    env = os.environ if env is None else env
    missing = []
    if not config.enabled:
        missing.append("GHOST_AUDIT_VISION=gemini")
    if not env.get(KEY_ENV, "").strip():
        missing.append(f"{KEY_ENV} (environment variable)")
    if not approve_real_calls:
        missing.append("--approve-real-calls")
    if missing:
        raise VisionDisabled("real Gemini calls are disabled; missing: " + ", ".join(missing))
    return GeminiVisionClient(env[KEY_ENV].strip(), config.timeout_s)
