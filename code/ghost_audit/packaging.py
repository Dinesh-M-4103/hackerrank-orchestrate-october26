"""Build and verify ``code.zip`` according to the official packaging instructions (README.md, "Packaging code.zip").

Included: README.md, requirements files (configuration) and everything under ``code/``.
Never included: ``dataset/``, virtual environments, caches (``__pycache__``, ``.cache``), ``.env*`` files, key/credential
files, run artifacts that may hold model output (``vision_interpretations.json``), and nested archives.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

ROOT_FILES = ["README.md", "requirements.txt", "requirements-gemini.txt"]
REQUIRED = ["README.md", "code/main.py", "code/evaluation/main.py", "code/evaluation/evaluation_report.md",
            "code/evaluation/usage_report.md"]
EXCLUDE_DIRS = {"__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "node_modules", ".git", "dataset"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")
SECRET_FILE_RE = re.compile(r"(^\.env)|(\.(key|pem|pfx|p12|dpapi)$)|(^vision_interpretations\.json$)|(^\.DS_Store$)|(\.pyc$)", re.I)
SECRET_TEXT_RES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                                # Google API key shape
    re.compile(r"AQ\.[A-Za-z0-9_\-]{30,}"),                               # newer Google key shape
    re.compile(r"(?i)GEMINI_API_KEY\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]
_FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def _skip(rel: PurePosixPath) -> bool:
    return any(p in EXCLUDE_DIRS for p in rel.parts[:-1]) or bool(SECRET_FILE_RE.search(rel.name)) or rel.name.lower().endswith(ARCHIVE_SUFFIXES)


def collect(repo_root: Path) -> List[PurePosixPath]:
    files: List[PurePosixPath] = []
    for name in ROOT_FILES:
        if (repo_root / name).is_file():
            files.append(PurePosixPath(name))
    for p in sorted((repo_root / "code").rglob("*")):
        if p.is_file():
            rel = PurePosixPath(p.relative_to(repo_root).as_posix())
            if not _skip(rel):
                files.append(rel)
    return files


def build_code_zip(repo_root: Path, out: Path) -> List[str]:
    names = [str(f) for f in collect(repo_root)]
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            info = zipfile.ZipInfo(n, date_time=_FIXED_DATE)       # fixed timestamps: the archive is reproducible
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (repo_root / n).read_bytes())
    return names


@dataclass
class ZipProblem:
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


def verify_zip(path: Path, secret_value: Optional[str] = None) -> List[ZipProblem]:
    """Check an existing archive against the official rules. ``secret_value`` (optional) is a live secret that must not appear."""
    problems: List[ZipProblem] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for req in REQUIRED:
            if req not in names:
                problems.append(ZipProblem("missing required file", req))
        for n in names:
            rel = PurePosixPath(n)
            if "\\" in n:
                problems.append(ZipProblem("backslash in entry name", n))
            if rel.parts[0] == "dataset" or any(p in EXCLUDE_DIRS for p in rel.parts[:-1]):
                problems.append(ZipProblem("forbidden directory", n))
            if SECRET_FILE_RE.search(rel.name):
                problems.append(ZipProblem("forbidden file", n))
            if n.lower().endswith(ARCHIVE_SUFFIXES):
                problems.append(ZipProblem("nested archive", n))
            if rel.parts[0] not in ("code", "README.md", "requirements.txt", "requirements-gemini.txt"):
                problems.append(ZipProblem("unexpected top-level entry", n))
        for n in names:
            data = zf.read(n)
            if secret_value and secret_value.encode("utf-8") in data:
                problems.append(ZipProblem("contains a live secret", n))
            if n.lower().endswith((".py", ".md", ".txt", ".json", ".csv", ".yml", ".yaml", ".toml", ".cfg", ".ini", "")) and b"\x00" not in data[:512]:
                text = data.decode("utf-8", errors="ignore")
                for rx in SECRET_TEXT_RES:
                    m = rx.search(text)
                    if m and not _is_documented_placeholder(n, text, m):
                        problems.append(ZipProblem("secret-looking text", f"{n} ({rx.pattern[:24]}...)"))
    return problems


def _is_documented_placeholder(name: str, text: str, m: "re.Match") -> bool:
    """The secret-shape regexes also match test fixtures that deliberately contain fake values."""
    return name.startswith("code/tests/") and "SECRET" in text.upper()


# ====================================================================================================================
# agent-trace.zip (per agent_trace_instructions.md): staged from the real session files, never fabricated
# ====================================================================================================================
import json  # noqa: E402

TRACE_ROOT = "agent-trace"
TRACE_FORBIDDEN_NAMES = re.compile(r"(^\.env)|(^auth\.json$)|(^config\.toml$)|(^settings(\.local)?\.json$)|(^mcp(_config)?\.json$)|(credentials)|(\.key$)|(\.pem$)", re.I)


def find_claude_sessions(repo_root: Path, home: Optional[Path] = None) -> List[Path]:
    """Claude Code session transcripts for this repository: ``~/.claude/projects/<folder that ends with the repo name>/*.jsonl``."""
    home = home or Path.home()
    base = home / ".claude" / "projects"
    if not base.is_dir():
        return []
    want = repo_root.name.lower()
    out: List[Path] = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and d.name.lower().endswith(want):
            out += sorted(d.glob("*.jsonl"))          # the sibling `memory/` folder is deliberately not included
    return out


def _secret_hits(data: bytes, secret: Optional[str]) -> List[str]:
    hits = []
    if secret and secret.encode("utf-8") in data:
        hits.append("live secret value")
    text = data.decode("utf-8", errors="ignore")
    for rx in SECRET_TEXT_RES:
        if rx.search(text):
            hits.append(rx.pattern[:28] + "...")
    return hits


def build_trace_zip(out: Path, sessions: List[Path], exports: List[Path], secret: Optional[str] = None) -> List[str]:
    """Write ``agent-trace.zip`` containing ``agent-trace/claude-code/<session>.jsonl`` and the given ``/export`` transcripts, unchanged.
    Refuses (raises ValueError) if any file contains a secret: a trace is never silently altered or redacted by this tool."""
    entries = [(f"{TRACE_ROOT}/claude-code/{p.name}", p) for p in list(sessions) + list(exports)]
    if not sessions:
        raise ValueError("no session transcript found")
    names = [n for n, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError("two files would have the same name inside the archive")
    for name, p in entries:
        if TRACE_FORBIDDEN_NAMES.search(p.name):
            raise ValueError(f"{p.name} must not be included (credential/settings file)")
        hits = _secret_hits(p.read_bytes(), secret)
        if hits:
            raise ValueError(f"{p.name} contains secret-looking content ({', '.join(hits)}); review and redact it manually, then retry")
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, p in entries:
            zf.write(p, name)                       # forward-slash entry names, original bytes
    return names


def verify_trace_zip(path: Path, secret: Optional[str] = None, live_sessions: Optional[List[Path]] = None) -> List[ZipProblem]:
    problems: List[ZipProblem] = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        jsonl = [n for n in names if n.lower().endswith(".jsonl")]
        others = [n for n in names if not n.lower().endswith(".jsonl") and not n.endswith("/")]
        if not jsonl:
            problems.append(ZipProblem("no session transcript", "expected agent-trace/claude-code/<session>.jsonl"))
        if not others:
            problems.append(ZipProblem("no readable export", "run /export in the session and give the saved file to package-trace (see agent_trace_instructions.md)"))
        for n in names:
            rel = PurePosixPath(n)
            if "\\" in n:
                problems.append(ZipProblem("backslash in entry name", n))
            if (rel.parts[0] != TRACE_ROOT or len(rel.parts) < 3) and not n.endswith("/"):
                problems.append(ZipProblem("unexpected location", n))
            if TRACE_FORBIDDEN_NAMES.search(rel.name) or "memory" in rel.parts or "dataset" in rel.parts:
                problems.append(ZipProblem("forbidden entry", n))
            if n.lower().endswith(ARCHIVE_SUFFIXES):
                problems.append(ZipProblem("nested archive", n))
        for n in names:
            if n.endswith("/"):
                continue
            data = zf.read(n)
            for h in _secret_hits(data, secret):
                problems.append(ZipProblem("secret in trace", f"{n}: {h}"))
            if n.lower().endswith(".jsonl"):
                bad = 0
                for line in data.decode("utf-8", errors="replace").splitlines():
                    if line.strip():
                        try:
                            json.loads(line)
                        except ValueError:
                            bad += 1
                if bad:
                    problems.append(ZipProblem("invalid JSONL", f"{n}: {bad} non-JSON line(s)"))
        for live in live_sessions or []:
            inside = f"{TRACE_ROOT}/claude-code/{live.name}"
            if inside not in names:
                problems.append(ZipProblem("session missing from the trace", live.name))
            elif zf.getinfo(inside).file_size < live.stat().st_size:
                problems.append(ZipProblem("stale copy", f"{live.name}: archive has {zf.getinfo(inside).file_size:,} bytes, the live file has {live.stat().st_size:,}"))
    return problems
