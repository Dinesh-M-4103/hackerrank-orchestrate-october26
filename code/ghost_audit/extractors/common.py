"""Helpers shared by the format-specific extractors."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import Fact
from ..taxonomy import category_for_resource, system_for_resource
from ..text_ids import Identifiers, identifiers_in_value
from ..timeutil import norm_ts


def flatten(value: Any, prefix: str = "", out: Optional[Dict[str, Any]] = None, depth: int = 0) -> Dict[str, Any]:
    """Flatten nested dicts into dotted keys; lists are kept as values (they are not enumerated)."""
    out = {} if out is None else out
    if isinstance(value, dict) and depth < 6:
        for k, v in value.items():
            flatten(v, f"{prefix}{k}." if isinstance(v, dict) else f"{prefix}{k}", out, depth + 1)
    else:
        out[prefix.rstrip(".")] = value
    return out


def make_fact(
    *,
    file_id: str,
    captured_at: str,
    resource_id: Optional[str],
    state: str,
    system_id: Optional[str] = None,
    category: Optional[str] = None,
    collection: Optional[str] = None,
    record: Optional[dict] = None,
    identifiers: Optional[Identifiers] = None,
    observed_at: Optional[str] = None,
    source_role: Optional[str] = None,
    attrs: Optional[dict] = None,
    refs: tuple = (),
    locator: str = "",
) -> Fact:
    """Build a Fact, filling system/category from the resource ID and collection when the source does not say."""
    if system_id is None and resource_id:
        system_id = system_for_resource(resource_id)
    if category is None and resource_id:
        category = category_for_resource(resource_id, collection, record)
    if identifiers is None:
        identifiers = identifiers_in_value(record) if record is not None else identifiers_in_value({})
    return Fact(
        file_id=file_id,
        captured_at=captured_at,
        system_id=system_id,
        resource_id=resource_id,
        category=category,
        state=state,
        identifiers=identifiers,
        observed_at=norm_ts(observed_at),
        source_role=source_role,
        attrs=attrs if attrs is not None else (flatten(record) if record is not None else {}),
        refs=refs,
        locator=locator,
    )
