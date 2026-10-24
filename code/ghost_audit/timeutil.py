"""Timestamp helpers. All pipeline timestamps are normalized to UTC ISO-8601 with a trailing Z."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (Z or offset; naive values are taken as UTC)."""
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_ts(value: Optional[str]) -> Optional[str]:
    """Return the canonical 'YYYY-MM-DDTHH:MM:SSZ' form, or None when value is empty/unparseable."""
    if value is None or not str(value).strip():
        return None
    try:
        return parse_ts(str(value)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def minutes_between(later: str, earlier: str) -> float:
    """Minutes from `earlier` to `later` (negative when `later` is actually earlier)."""
    return (parse_ts(later) - parse_ts(earlier)).total_seconds() / 60.0
