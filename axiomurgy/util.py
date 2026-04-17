"""Shared utility helpers for JSON, hashing, IO, and path portability."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml

# Directory containing this package (schemas/policy ship inside the wheel).
PACKAGE_ROOT = Path(__file__).resolve().parent
# Parent of the package directory (repository root in editable installs; site-packages parent when installed).
ROOT = PACKAGE_ROOT.parent
# Bundled contracts (path-independent; see axiomurgy/bundled/).
DEFAULT_SCHEMA_PATH = PACKAGE_ROOT / "bundled" / "spell.schema.json"
DEFAULT_SPELLBOOK_SCHEMA_PATH = PACKAGE_ROOT / "bundled" / "spellbook.schema.json"
DEFAULT_POLICY_PATH = PACKAGE_ROOT / "bundled" / "policies" / "default.policy.json"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def canonical_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def file_digest_entry(path: Path, repo_root: Optional[Path] = None, role: Optional[str] = None) -> Dict[str, Any]:
    resolved = path.resolve()
    rel = None
    if repo_root is not None:
        try:
            rel = resolved.relative_to(repo_root.resolve()).as_posix()
        except Exception:
            rel = None
    return {
        "role": role,
        "path": str(resolved),
        "repo_relpath": rel,
        "size_bytes": resolved.stat().st_size if resolved.exists() else None,
        "sha256": sha256_file(resolved) if resolved.exists() else None,
    }


def extract_references(value: Any) -> Set[str]:
    refs: Set[str] = set()
    if isinstance(value, str):
        if value.startswith("$"):
            refs.add(value[1:].split(".", 1)[0])
    elif isinstance(value, list):
        for item in value:
            refs.update(extract_references(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(extract_references(item))
    return refs


def load_json(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    elif raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return json.loads(text)


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema(schema_ref: Any, base_dir: Path) -> Dict[str, Any]:
    if isinstance(schema_ref, dict):
        return schema_ref
    if isinstance(schema_ref, str):
        path = Path(schema_ref)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return json.loads(path.read_text(encoding="utf-8"))
    raise TypeError("schema must be an object or path string")


def _looks_like_path(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(("http://", "https://", "mcp://", "upload://")):
        return False
    if lower.startswith("file://"):
        return True
    if re.match(r"^[a-zA-Z]:[\\\\/]", text) or text.startswith("\\\\"):
        return True
    if text.startswith("/"):
        return True
    return False


def _portable_path_token(text: str, repo_root: Path) -> str:
    raw = text
    if raw.lower().startswith("file://"):
        raw = raw[7:]
    try:
        p = Path(raw)
    except Exception:
        return "<opaque_path>"
    try:
        resolved = p.resolve()
    except Exception:
        return "<opaque_path>"
    try:
        rel = resolved.relative_to(repo_root.resolve()).as_posix()
        return f"repo:{rel}"
    except Exception:
        return "<opaque_path>"


def normalize_paths_for_portability(value: Any, repo_root: Path) -> Any:
    if isinstance(value, str):
        return _portable_path_token(value, repo_root) if _looks_like_path(value) else value
    if isinstance(value, list):
        return [normalize_paths_for_portability(item, repo_root) for item in value]
    if isinstance(value, dict):
        return {k: normalize_paths_for_portability(v, repo_root) for k, v in value.items()}
    return value


__all__ = [
    "PACKAGE_ROOT",
    "ROOT",
    "DEFAULT_SCHEMA_PATH",
    "DEFAULT_SPELLBOOK_SCHEMA_PATH",
    "DEFAULT_POLICY_PATH",
    "DEFAULT_ARTIFACT_DIR",
    "utc_now",
    "json_dumps",
    "canonical_json",
    "sha256_bytes",
    "sha256_file",
    "file_digest_entry",
    "load_json",
    "load_yaml",
    "load_schema",
    "extract_references",
    "_looks_like_path",
    "_portable_path_token",
    "normalize_paths_for_portability",
]
