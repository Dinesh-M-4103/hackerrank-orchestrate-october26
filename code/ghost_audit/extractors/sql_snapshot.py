"""SQLite-compatible SQL snapshot exports.

Three layouts occur in the dataset:

* ``multi``: tables with ``(resource_id, origin_system, collection_name, record_json, updated_at)`` holding mixed
  collections - one table per database node (``customer_data_primary``, ``customer_data_replica_ap_south``, ...).
* ``named``: one table per collection (``customer_profiles_primary`` / ``customer_profiles_failover_replica``).
* ``inventory``: one table per category plus a ``snapshot_scope`` table listing the systems the snapshot covers.

The script is executed in an in-memory SQLite database; nothing is written to disk.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Dict

from ..models import EvidenceMeta, ExtractionResult, Scope
from ..taxonomy import COLLECTION_SYSTEM
from ..text_ids import identifiers_in_value
from ..timeutil import norm_ts
from .common import flatten, make_fact

_TOPOLOGY_RE = re.compile(r"--\s*Database topology:\s*(\S+)", re.I)


def table_role(table: str) -> str:
    """Name the node a table represents: 'primary', 'read_replica', 'replica_ap_south', 'failover_replica', ..."""
    name = table
    for prefix in ("customer_data_", "customer_profiles_", "search_documents_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name in ("primary", "customer_data_primary"):
        return "primary"
    if "replica" in name or name == "primary":
        return name
    return "inventory"


def load_sql(text: str) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.executescript(text)
    except sqlite3.Error:
        db.close()
        raise
    return db


def extract_sql(meta: EvidenceMeta) -> ExtractionResult:
    text = Path(meta.path).read_text(encoding="utf-8")
    res = ExtractionResult(file_id=meta.file_id, kind="sql_snapshot")
    m = _TOPOLOGY_RE.search(text)
    res.kind = "sql_snapshot:" + (m.group(1) if m else "inventory")
    try:
        db = load_sql(text)
    except sqlite3.Error as exc:
        res.warnings.append(f"sql error: {exc}")
        return res
    try:
        _fill(db, meta, res)
    finally:
        db.close()
    return res


def _fill(db: sqlite3.Connection, meta: EvidenceMeta, res: ExtractionResult) -> None:
    tables = [r["name"] for r in db.execute("select name from sqlite_master where type='table' order by name")]
    system_time: Dict[str, str] = {}
    if "snapshot_scope" in tables:
        for r in db.execute("select origin_system, captured_at from snapshot_scope"):
            ts = norm_ts(r["captured_at"])
            system_time[r["origin_system"]] = ts or meta.captured_at
            res.scopes.append(Scope(meta.file_id, meta.captured_at, r["origin_system"], None, "inventory", ts, "snapshot_scope"))

    for table in tables:
        if table == "snapshot_scope":
            continue
        role = table_role(table)
        cols = [r[1] for r in db.execute(f"pragma table_info({table})")]
        seen_scopes = set()
        for row in db.execute(f"select * from {table}"):
            d = {k: row[k] for k in cols}
            res.lines_total += 1
            if "record_json" in d:
                try:
                    rec = json.loads(d["record_json"])
                except (TypeError, ValueError):
                    res.warnings.append(f"{table}:{d.get('resource_id')}: invalid record_json")
                    continue
                rec.setdefault("resource_id", d["resource_id"])
                system, coll = d.get("origin_system"), d.get("collection_name")
                rec_for_attrs = dict(rec)
            else:
                rec = {k: v for k, v in d.items()}
                coll = table
                system = COLLECTION_SYSTEM.get(table)
                rec_for_attrs = dict(rec)
            rec_for_attrs["updated_at"] = d.get("updated_at")
            observed = system_time.get(system or "", None)
            res.facts.append(
                make_fact(
                    file_id=meta.file_id,
                    captured_at=meta.captured_at,
                    resource_id=d["resource_id"],
                    state="PRESENT",
                    system_id=system,
                    collection=coll,
                    record=rec_for_attrs,
                    identifiers=identifiers_in_value({k: v for k, v in rec.items() if k != "resource_id"}),
                    observed_at=observed,
                    source_role=role,
                    attrs=flatten(rec_for_attrs),
                    locator=f"{table}:{d['resource_id']}",
                )
            )
            res.lines_parsed += 1
            key = (system, coll)
            if key not in seen_scopes and system:
                seen_scopes.add(key)
                res.scopes.append(Scope(meta.file_id, meta.captured_at, system, coll, role, observed, table))
        if table in COLLECTION_SYSTEM and not any(s.locator == table for s in res.scopes):
            # empty collection table still enumerates its collection (an empty result is evidence of absence)
            res.scopes.append(Scope(meta.file_id, meta.captured_at, COLLECTION_SYSTEM[table], table, role,
                                    system_time.get(COLLECTION_SYSTEM[table]), table))
