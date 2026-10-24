"""JSON evidence: snapshot bundles, ledgers, collection exports and per-service exports.

Every dict that carries a ``resource_id`` becomes one Fact. Containers that enumerate a collection (a snapshot's
``records`` list, a service export list) also produce a Scope, so an *absent* resource can be proven by a source
that demonstrably covers the collection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import EvidenceMeta, ExtractionResult, Scope
from ..taxonomy import COLLECTION_SYSTEM
from ..text_ids import identifiers_in_value
from ..timeutil import norm_ts
from .common import make_fact


def _ledger_state(rec: dict) -> Optional[str]:
    if isinstance(rec.get("result"), str):
        return rec["result"].upper()
    return None


def _item_state(rec: dict) -> str:
    st = _ledger_state(rec)
    if st:
        return st
    code = rec.get("status_code")
    if code is not None:
        try:
            return "NOT_FOUND" if int(code) == 404 else ("FOUND" if int(code) == 200 else f"HTTP_{int(code)}")
        except (TypeError, ValueError):
            pass
    return "PRESENT"


def _fact_from_record(meta: EvidenceMeta, rec: dict, *, system: Optional[str], collection: Optional[str], time: Optional[str],
                      locator: str):
    ledger = "record_type" in rec and "result" in rec
    ids_source = {k: v for k, v in rec.items() if k not in ("resource_id",)}
    return make_fact(
        file_id=meta.file_id,
        captured_at=meta.captured_at,
        resource_id=rec["resource_id"],
        state=_item_state(rec),
        system_id=rec.get("system") if isinstance(rec.get("system"), str) else system,
        category=rec["record_type"] if ledger and isinstance(rec.get("record_type"), str) else None,
        collection=collection,
        record=rec,
        identifiers=identifiers_in_value(ids_source),
        observed_at=rec.get("observed_at") or rec.get("recorded_at") or time,
        locator=locator,
    )


def _records(container: Any) -> List[dict]:
    return [r for r in container if isinstance(r, dict) and isinstance(r.get("resource_id"), str)] if isinstance(container, list) else []


def extract_json(meta: EvidenceMeta) -> ExtractionResult:
    res = ExtractionResult(file_id=meta.file_id, kind="json")
    data = json.loads(Path(meta.path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        res.warnings.append("top-level JSON is not an object")
        return res

    def add(rec: dict, system, collection, time, locator):
        res.lines_total += 1
        res.facts.append(_fact_from_record(meta, rec, system=system, collection=collection, time=time, locator=locator))
        res.lines_parsed += 1

    if isinstance(data.get("snapshots"), list):
        res.kind = "json_bundle"
        for i, snap in enumerate(data["snapshots"]):
            system, coll, cap = snap.get("data_store"), snap.get("collection"), norm_ts(snap.get("captured_at"))
            if system and cap:
                res.scopes.append(Scope(meta.file_id, meta.captured_at, system, coll, None, cap, f"snapshots[{i}]", subject_scoped=True))
            for j, rec in enumerate(_records(snap.get("records"))):
                add(rec, system, coll, cap, f"snapshots[{i}].records[{j}]")
            res.lines_total += sum(1 for r in snap.get("records", []) if not (isinstance(r, dict) and "resource_id" in r))
    elif isinstance(data.get("service_exports"), dict):
        res.kind = "json_service_export"
        exported = norm_ts(data.get("exported_at"))
        for system, body in data["service_exports"].items():
            for list_name, items in (body or {}).items():
                res.scopes.append(Scope(meta.file_id, meta.captured_at, system, list_name, None, exported, f"service_exports.{system}.{list_name}"))
                for j, rec in enumerate(_records(items)):
                    add(rec, system, list_name, exported, f"service_exports.{system}.{list_name}[{j}]")
    elif isinstance(data.get("records"), list):
        res.kind = "json_collection" if "collection" in data else "json_ledger"
        time = norm_ts(data.get("captured_at") or data.get("exported_at"))
        coll = data.get("collection")
        for j, rec in enumerate(_records(data["records"])):
            add(rec, None, coll, time, f"records[{j}]")
        if coll:
            system = COLLECTION_SYSTEM.get(coll)
            if system:
                res.scopes.append(Scope(meta.file_id, meta.captured_at, system, coll, None, time, "records"))
    else:
        res.warnings.append("unrecognized JSON shape: " + ",".join(sorted(data)[:6]))
    return res
