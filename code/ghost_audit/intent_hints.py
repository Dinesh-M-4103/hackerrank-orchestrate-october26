"""Corroborating (not deciding) signals that a request is not a deletion request.

``CLASSIFY_REQUEST`` activity rows say "Privacy intake classified request for <resource> as non-deletion; no
destructive action was authorized." The resource is tied to a person through the owner map; the hint attaches to every
case of that person audited at or after the activity. It is evidence for the final NOT_APPLICABLE decision, which
rests on reading the request text (a later milestone) - activity rows are claims, not proof.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

from .identity import IdentityResolver
from .models import Activity, Case
from .owners import OwnerMap

CLASSIFY_EVENT = "CLASSIFY_REQUEST"
NON_DELETION_MARKER = "non-deletion"


def nondeletion_hints(cases: Iterable[Case], activities: Iterable[Activity], owners: OwnerMap,
                      resolver: IdentityResolver, strict_cutoff: bool = True) -> Dict[str, List[str]]:
    """case_id -> [activity_id, ...] for cases whose person has a 'classified as non-deletion' activity row.

    ``strict_cutoff=False`` (diagnostics only) also accepts ownership links captured after ``audit_at``."""
    classify = [a for a in activities if a.event_type == CLASSIFY_EVENT and NON_DELETION_MARKER in a.details.lower()]
    out: Dict[str, List[str]] = defaultdict(list)
    for case in cases:
        user = resolver.resolve_email(case.email).user_id
        if user is None:
            continue
        for a in classify:
            if a.event_time <= case.audit_at and owners.users_of(a.resource_id, case.audit_at if strict_cutoff else None).get(user):
                out[case.case_id].append(a.activity_id)
    return dict(out)
