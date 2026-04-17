"""Fingerprint and input/output manifest helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from .util import (
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SPELLBOOK_SCHEMA_PATH,
    ROOT,
    canonical_json,
    file_digest_entry,
    sha256_bytes,
    sha256_file,
)

if TYPE_CHECKING:
    from .legacy import ResolvedRunTarget, Spell


def extract_declared_input_paths(spell: "Spell") -> List[Path]:
    paths: List[Path] = []
    for step in list(spell.graph) + list(spell.rollback):
        if step.rune == "mirror.read":
            raw = step.args.get("input")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and not item.startswith("$"):
                        paths.append(Path(item[7:]) if item.startswith("file://") else Path(item))
            elif isinstance(raw, str) and not raw.startswith("$"):
                paths.append(Path(raw[7:]) if raw.startswith("file://") else Path(raw))
        if step.rune == "seal.assert_path_exists":
            raw = step.args.get("path")
            if isinstance(raw, str) and not raw.startswith("$"):
                paths.append(Path(raw))
    seen: Set[str] = set()
    out: List[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def classify_input_manifest(spell: "Spell") -> Dict[str, Any]:
    declared_static: List[Dict[str, Any]] = []
    declared_dynamic: List[Dict[str, Any]] = []
    unresolved_dynamic: List[Dict[str, Any]] = []

    def add_static(spec: str, step_id: str, rune: str) -> None:
        declared_static.append({"spec": spec, "step_id": step_id, "rune": rune})

    def add_declared_dynamic(spec: Any, step_id: str, rune: str, note: str) -> None:
        declared_dynamic.append({"spec": spec, "step_id": step_id, "rune": rune, "note": note})

    def add_unresolved(spec: Any, step_id: str, rune: str, note: str) -> None:
        unresolved_dynamic.append({"spec": spec, "step_id": step_id, "rune": rune, "note": note})

    for step in list(spell.graph) + list(spell.rollback):
        if step.rune == "mirror.read":
            raw = step.args.get("input")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and not item.startswith("$"):
                        add_static(item, step.step_id, step.rune)
                    elif isinstance(item, str) and item.startswith("$"):
                        add_declared_dynamic(item, step.step_id, step.rune, "mirror.read input references runtime value")
            elif isinstance(raw, str):
                if raw.startswith("$inputs"):
                    add_declared_dynamic(raw, step.step_id, step.rune, "mirror.read input references spell inputs")
                elif raw.startswith("$"):
                    add_unresolved(raw, step.step_id, step.rune, "mirror.read input references non-input runtime value")
                else:
                    add_static(raw, step.step_id, step.rune)
        if step.rune == "seal.assert_path_exists":
            raw = step.args.get("path")
            if isinstance(raw, str) and raw.startswith("$"):
                add_unresolved(raw, step.step_id, step.rune, "path is computed dynamically at runtime")
            elif isinstance(raw, str):
                add_static(raw, step.step_id, step.rune)

    return {
        "declared_static": declared_static,
        "declared_dynamic": declared_dynamic,
        "unresolved_dynamic": unresolved_dynamic,
        "summary": {
            "declared_static": len(declared_static),
            "declared_dynamic": len(declared_dynamic),
            "unresolved_dynamic": len(unresolved_dynamic),
            "unresolved_dynamic_present": bool(unresolved_dynamic),
        },
    }


def extract_output_schema_paths(spell: "Spell") -> List[Path]:
    out: List[Path] = []
    for step in list(spell.graph) + list(spell.rollback):
        if isinstance(step.output_schema, str) and step.output_schema:
            out.append(Path(step.output_schema))
    return out


def compute_spell_fingerprints(spell: "Spell", policy_path: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    repo_root = repo_root or ROOT
    files: List[Dict[str, Any]] = []
    files.append(file_digest_entry(spell.source_path, repo_root=repo_root, role="spell"))
    files.append(file_digest_entry(policy_path, repo_root=repo_root, role="policy"))
    files.append(file_digest_entry(DEFAULT_SCHEMA_PATH, repo_root=repo_root, role="schema:spell"))
    files.append(file_digest_entry(DEFAULT_SPELLBOOK_SCHEMA_PATH, repo_root=repo_root, role="schema:spellbook"))
    for schema_path in extract_output_schema_paths(spell):
        resolved = (spell.source_path.parent / schema_path).resolve() if not schema_path.is_absolute() else schema_path.resolve()
        files.append(file_digest_entry(resolved, repo_root=repo_root, role="schema:output"))

    input_manifest = classify_input_manifest(spell)
    input_files: List[Dict[str, Any]] = []
    for item in input_manifest["declared_static"]:
        spec = str(item["spec"])
        path = Path(spec[7:]) if spec.startswith("file://") else Path(spec)
        resolved = (spell.source_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        input_files.append(file_digest_entry(resolved, repo_root=repo_root, role="input"))

    required = {
        "spell_sha256": sha256_file(spell.source_path),
        "policy_sha256": sha256_file(policy_path),
        "spell_schema_sha256": sha256_file(DEFAULT_SCHEMA_PATH),
        "spellbook_schema_sha256": sha256_file(DEFAULT_SPELLBOOK_SCHEMA_PATH),
    }
    return {
        "required": required,
        "files": files,
        "input_manifest": {
            "files": input_files,
            "classification": input_manifest,
            "sha256": sha256_bytes(canonical_json({"files": input_files, "classification": input_manifest}).encode("utf-8")),
        },
    }


def compute_spellbook_fingerprints(resolved: "ResolvedRunTarget", repo_root: Optional[Path] = None) -> Dict[str, Any]:
    repo_root = repo_root or ROOT
    if resolved.spellbook is None:
        return {}
    sb = resolved.spellbook
    files: List[Dict[str, Any]] = []
    files.append(file_digest_entry(sb.source_path, repo_root=repo_root, role="spellbook:manifest"))
    files.append(file_digest_entry(resolved.spell.source_path, repo_root=repo_root, role="spellbook:entrypoint_spell"))
    spellbook_dir = sb.source_path.parent
    schemas_dir = spellbook_dir / "schemas"
    if schemas_dir.exists():
        for schema_file in sorted(schemas_dir.glob("*.json")):
            files.append(file_digest_entry(schema_file, repo_root=repo_root, role="spellbook:schema"))
    required = {
        "spellbook_manifest_sha256": sha256_file(sb.source_path),
        "spellbook_entrypoint_spell_sha256": sha256_file(resolved.spell.source_path),
    }
    return {"required": required, "files": files}


__all__ = [
    "extract_declared_input_paths",
    "classify_input_manifest",
    "extract_output_schema_paths",
    "compute_spell_fingerprints",
    "compute_spellbook_fingerprints",
]
