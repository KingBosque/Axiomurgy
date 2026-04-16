"""Shared utility helpers for JSON, hashing, IO, and path portability."""

from .legacy import (
    _looks_like_path,
    _portable_path_token,
    canonical_json,
    extract_references,
    file_digest_entry,
    json_dumps,
    load_json,
    load_schema,
    load_yaml,
    normalize_paths_for_portability,
    sha256_bytes,
    sha256_file,
    utc_now,
)

__all__ = [
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
