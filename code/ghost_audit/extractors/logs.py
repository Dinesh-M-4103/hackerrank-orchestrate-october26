"""Service/operations logs in the dialects found in the dataset.

Supported line shapes (all start with an ISO timestamp):

* key=value:            ``ts level=INFO service=x trace_id=tr-1 event=e resource_id=r ...``
* JSON line:            ``ts {"severity":"INFO","service":"x","trace_id":"tr-1","event":"e",...}``
* pipe separated:       ``ts | LEVEL | service | trace | event | k="v" ...``
* bracketed:            ``ts [LEVEL] [service] [trace] event: k="v" ...``
* syslog:               ``ts <15>1 node svc trace - - level=... k="v" ...``
* cloud runtime:        ``ts requestId=tr-1 level=INFO function=svc message="k=\\"v\\" ..."``

Identifiers that appear on a line without a resource (``lookup_key=<email>`` on a ``*_started`` line) are tied to
the resource in the same trace, so ``trace_id`` grouping links an email to the resource it was looking up.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..models import EvidenceMeta, ExtractionResult
from ..taxonomy import resource_prefix
from ..text_ids import EMPTY_IDS, identifiers_in_value
from ..timeutil import norm_ts
from .common import make_fact

# contact-index lookups describe secondary identifier records (rule_02), whatever the resource ID prefix says
SECONDARY_ID_EVENTS = {"contact_query_completed": "SECONDARY_IDENTIFIER", "contact_lookup_completed": "SECONDARY_IDENTIFIER"}

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*(.*)$")
KV_RE = re.compile(r'([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|[^\s"]+)')
BRACKET_RE = re.compile(r"^\[([^\]]+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*(.*)$")


@dataclass
class LogLine:
    ts: str
    service: Optional[str]
    trace: Optional[str]
    event: Optional[str]
    attrs: Dict[str, str] = field(default_factory=dict)
    lineno: int = 0


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def parse_kv(text: str) -> Dict[str, str]:
    return {k: _unquote(v) for k, v in KV_RE.findall(text)}


def parse_log_line(line: str, lineno: int = 0) -> Optional[LogLine]:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, rest = m.group(1), m.group(2).strip()
    service = trace = event = None
    attrs: Dict[str, str] = {}
    if rest.startswith("{"):
        try:
            obj = json.loads(rest)
        except ValueError:
            return None
        attrs = {str(k): (v if isinstance(v, str) else json.dumps(v)) for k, v in obj.items()}
    elif rest.startswith("|"):
        parts = [p.strip() for p in rest.strip("| ").split(" | ")]
        if len(parts) < 4:
            return None
        attrs = parse_kv(" ".join(parts[4:]))
        attrs.update({"level": parts[0], "service": parts[1], "trace_id": parts[2], "event": parts[3]})
        if len(parts) >= 5 and "=" not in parts[3]:
            attrs["event"] = parts[3]
    elif rest.startswith("<"):
        toks = rest.split(None, 6)
        if len(toks) < 6:
            return None
        attrs = parse_kv(toks[6] if len(toks) > 6 else "")
        attrs.update({"service": toks[2], "trace_id": toks[3]})
    elif rest.startswith("["):
        b = BRACKET_RE.match(rest)
        if not b:
            return None
        attrs = parse_kv(b.group(5))
        attrs.update({"level": b.group(1), "service": b.group(2), "trace_id": b.group(3), "event": b.group(4)})
    else:
        attrs = parse_kv(rest)
        if not attrs:
            return None
    if "message" in attrs and "=" in attrs["message"]:
        for k, v in parse_kv(attrs["message"]).items():
            attrs.setdefault(k, v)
    service = attrs.get("service") or attrs.get("logger") or attrs.get("function")
    trace = attrs.get("trace_id") or attrs.get("requestId")
    event = attrs.get("event")
    return LogLine(ts=norm_ts(ts) or ts, service=service, trace=trace, event=event, attrs=attrs, lineno=lineno)


def derive_log_state(attrs: Dict[str, str]) -> str:
    """Map a log line's outcome fields to the vocabulary used by the other sources (FOUND / NOT_FOUND / ...)."""
    result = attrs.get("result")
    if result:
        r = result.strip().upper()
        return {"HIT": "FOUND", "MISS": "NOT_FOUND"}.get(r, r)
    if attrs.get("deletion_block", "").lower() == "true":
        return "BLOCKED"
    if attrs.get("eligible", "").lower() == "true" and attrs.get("deletion_block", "").lower() == "false":
        return "RESTORABLE"
    status = attrs.get("http_status")
    rows = attrs.get("rows")
    if status == "404" or rows == "0":
        return "NOT_FOUND"
    if status == "200" or (rows and rows.isdigit() and int(rows) > 0):
        return "FOUND"
    return "OBSERVED"


def extract_log(meta: EvidenceMeta) -> ExtractionResult:
    res = ExtractionResult(file_id=meta.file_id, kind="log")
    lines: List[LogLine] = []
    for n, raw in enumerate(Path(meta.path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        res.lines_total += 1
        parsed = parse_log_line(raw, n)
        if parsed is None:
            res.warnings.append(f"line {n}: unparsed")
            continue
        res.lines_parsed += 1
        lines.append(parsed)

    trace_ids = defaultdict(lambda: EMPTY_IDS)
    for ln in lines:
        if ln.trace:
            ids = identifiers_in_value({k: v for k, v in ln.attrs.items() if k != "resource_id"})
            trace_ids[ln.trace] = trace_ids[ln.trace].union(ids)

    for ln in lines:
        rid = ln.attrs.get("resource_id")
        if not rid or not resource_prefix(rid):
            continue
        own = identifiers_in_value({k: v for k, v in ln.attrs.items() if k != "resource_id"})
        ids = own.union(trace_ids[ln.trace]) if ln.trace else own
        res.facts.append(
            make_fact(
                file_id=meta.file_id,
                captured_at=meta.captured_at,
                resource_id=rid,
                state=derive_log_state(ln.attrs),
                system_id=ln.attrs.get("system") or None,
                category=ln.attrs.get("record_type") or SECONDARY_ID_EVENTS.get(ln.event or ""),
                identifiers=ids,
                observed_at=ln.ts,
                attrs={k: v for k, v in ln.attrs.items() if k not in ("message",)},
                locator=f"line {ln.lineno}",
            )
        )
    return res
