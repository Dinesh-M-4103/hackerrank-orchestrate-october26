"""Assemble the loaded dataset, extracted facts, identity resolver and owner map into one object."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from .extractors import extract_all
from .identity import IdentityResolver
from .loaders import load_activity, load_audit_log, load_identities, load_manifest, load_requests, load_rules
from .models import Activity, EvidenceMeta, ExtractionResult, Fact, Rule
from .owners import OwnerMap, build_owner_map
from .paths import DATASET_DIR


@dataclass
class Context:
    dataset_dir: Path
    manifest: List[EvidenceMeta]
    extractions: Dict[str, ExtractionResult]
    facts: List[Fact]
    activities: List[Activity]
    rules: List[Rule]
    resolver: IdentityResolver
    owners: OwnerMap

    def facts_for_file(self, file_id: str) -> List[Fact]:
        return self.extractions[file_id].facts


def build_context(dataset_dir: Optional[Union[str, Path]] = None, interpretations: Optional[dict] = None) -> Context:
    """``interpretations``: optional {file_id: ImageInterpretation} from the (feature-flagged) screenshot interpreter. Images
    without a usable interpretation stay 'unsupported', exactly as when vision is disabled."""
    base = Path(dataset_dir) if dataset_dir is not None else DATASET_DIR
    manifest = load_manifest(base)
    extractions = extract_all(m for m in manifest if m.path.exists())
    activities = load_activity(base)
    if interpretations:
        from .owners import LINK_EVENT
        from .vision.facts import to_extraction
        linked = {a.resource_id for a in activities if a.event_type == LINK_EVENT}
        for m in manifest:
            if m.file_id in interpretations:
                extractions[m.file_id] = to_extraction(m, interpretations[m.file_id], corroborated=linked)
    facts = [f for r in extractions.values() for f in r.facts]
    resolver = IdentityResolver(load_identities(base), load_audit_log(base))
    owners = build_owner_map(facts, activities, resolver)
    return Context(base, manifest, extractions, facts, activities, load_rules(base), resolver, owners)
