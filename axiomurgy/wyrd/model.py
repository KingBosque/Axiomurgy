"""Wyrd v1 operational kinds (causal memory; not execution authority)."""

from __future__ import annotations

from typing import Final

# Durable node kinds persisted from the reasoning stack + refs.
NODE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "telos",
        "governor_tradeoff",
        "dialectic_episode",
        "correspondence_cluster",
        "friction_bottleneck",
        "outcome",
        "review_bundle_ref",
        "witness_ref",
    }
)

# Directed causal / support edges (append-only records).
EDGE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "supports",
        "constrains",
        "motivates",
        "derives_from",
        "leads_to",
        "contradicts",
        "mitigates",
        "records",
    }
)

WYRD_SCHEMA_VERSION: Final[str] = "1"
