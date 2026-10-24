"""Deterministic parsing of times *displayed* in a screenshot. The model only transcribes the text; this code (not the
model) turns it into UTC, so a hallucinated ISO timestamp can never enter the pipeline.

Time-only strings ("06:46") carry no date and are ignored for cutoff purposes. A day+month without a year takes the
year of the evidence capture (rolled back one year if that would land more than ~6 months after the capture).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from ..timeutil import norm_ts, parse_ts

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_TZ_HOURS = {"": 0, "Z": 0, "UTC": 0, "GMT": 0, "JST": 9, "KST": 9}

_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)\s*(Z|UTC|[+-]\d{2}:?\d{2})?", re.I)
_DMY_T = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?,?\s+(\d{4})[ ,]+(\d{1,2}:\d{2}(?::\d{2})?)\s*([A-Za-z]{2,4})?")
_DM_T = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?,?\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*([A-Za-z]{2,4})?")
_DMY = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?,?\s+(\d{4})\b")
_DM = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\b")


@dataclass(frozen=True)
class ParsedTime:
    iso: str                 # UTC, canonical
    year_assumed: bool = False
    tz_assumed: bool = False


def _build(day: int, mon: str, year: int, hms: str, tz: str) -> Optional[datetime]:
    m = _MONTHS.get(mon.lower()[:3])
    if not m:
        return None
    parts = [int(x) for x in hms.split(":")]
    h, mi, s = parts[0], parts[1], (parts[2] if len(parts) > 2 else 0)
    try:
        dt = datetime(year, m, day, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt - timedelta(hours=_TZ_HOURS.get(tz.upper(), 0))


def parse_display_time(text: str, captured_at: str) -> Optional[ParsedTime]:
    """Parse one displayed time string; None when it has no usable date."""
    t = (text or "").strip()
    if not t:
        return None
    m = _ISO.search(t)
    if m:
        tz = (m.group(3) or "").upper()
        base = f"{m.group(1)}T{m.group(2)}" + ("" if m.group(2).count(":") == 2 else ":00")
        try:
            dt = parse_ts(base + ("Z" if tz in ("", "Z", "UTC") else tz))
        except ValueError:
            return None
        return ParsedTime(norm_ts(dt.isoformat()), False, tz == "")
    cap = parse_ts(captured_at)
    m = _DMY_T.search(t)
    if m:
        tz = (m.group(5) or "")
        dt = _build(int(m.group(1)), m.group(2), int(m.group(3)), m.group(4), tz if tz.upper() in _TZ_HOURS else "")
        return ParsedTime(norm_ts(dt.isoformat()), False, tz.upper() not in _TZ_HOURS or tz == "") if dt else None
    m = _DM_T.search(t)
    if m:
        tz = (m.group(4) or "")
        dt = _build(int(m.group(1)), m.group(2), cap.year, m.group(3), tz if tz.upper() in _TZ_HOURS else "")
        if dt is None:
            return None
        if dt > cap + timedelta(days=183):
            dt = dt.replace(year=dt.year - 1)
        return ParsedTime(norm_ts(dt.isoformat()), True, tz.upper() not in _TZ_HOURS or tz == "")
    m = _DMY.search(t)
    if m:
        dt = _build(int(m.group(1)), m.group(2), int(m.group(3)), "00:00", "")
        return ParsedTime(norm_ts(dt.isoformat()), False, True) if dt else None
    m = _DM.search(t)
    if m and m.group(2).lower()[:3] in _MONTHS:
        dt = _build(int(m.group(1)), m.group(2), cap.year, "00:00", "")
        if dt is None:
            return None
        if dt > cap + timedelta(days=183):
            dt = dt.replace(year=dt.year - 1)
        return ParsedTime(norm_ts(dt.isoformat()), True, True)
    return None


def latest_displayed(texts: Iterable[str], captured_at: str) -> Optional[str]:
    times = [p.iso for p in (parse_display_time(t, captured_at) for t in texts) if p]
    return max(times) if times else None
