"""Repository-relative paths. Nothing here is hardcoded to a user directory."""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CODE_DIR = PACKAGE_DIR.parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
EVIDENCE_DIR = DATASET_DIR / "evidence"
