"""
Live Vermyth HTTP integration (optional).

Requires a running Vermyth adapter and:
  AXIOMURGY_VERMYTH_LIVE=1
  AXIOMURGY_VERMYTH_BASE_URL or VERMYTH_BASE_URL

Pin: document the Vermyth Git tag / PyPI version you validated against in commit messages
when updating this file. Example: Vermyth main @ tag vX.Y.Z (HTTP tools/arcane parity).

CI: run manually or via a dedicated workflow job; default pytest skips these tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests

from axiomurgy.legacy import load_spell
from axiomurgy.vermyth_export import build_semantic_program
from axiomurgy.adapters.vermyth_http import VermythHttpClient

ROOT = Path(__file__).resolve().parents[2]


def _live_enabled() -> bool:
    return os.environ.get("AXIOMURGY_VERMYTH_LIVE", "").strip() in ("1", "true", "yes")


def _base_url() -> str | None:
    v = os.environ.get("AXIOMURGY_VERMYTH_BASE_URL") or os.environ.get("VERMYTH_BASE_URL")
    return v.strip().rstrip("/") if isinstance(v, str) and v.strip() else None


pytestmark = pytest.mark.vermyth_live


@pytest.fixture(scope="module")
def client() -> VermythHttpClient:
    if not _live_enabled():
        pytest.skip("set AXIOMURGY_VERMYTH_LIVE=1")
    base = _base_url()
    if not base:
        pytest.skip("set AXIOMURGY_VERMYTH_BASE_URL")
    return VermythHttpClient(base + "/", timeout_s=15.0)


def test_server_reachable(client: VermythHttpClient) -> None:
    r = requests.get(client.base_url.rstrip("/") + "/", timeout=5.0)
    assert r.status_code < 600


def test_tools_list_or_health(client: VermythHttpClient) -> None:
    for path in ("tools", "health", ""):
        r = requests.get(client.base_url.rstrip("/") + ("/" + path if path else ""), timeout=5.0)
        if r.status_code == 200:
            return
    pytest.fail("could not GET /tools or /health from Vermyth base URL")


def test_arcane_recommend(client: VermythHttpClient) -> None:
    out = client.arcane_recommend(
        skill_id="axiomurgy.test",
        input_={
            "intent": {
                "objective": "intent test risk low",
                "scope": "axiomurgy:live_test",
                "reversibility": "PARTIAL",
                "side_effect_tolerance": "MEDIUM",
            },
        },
    )
    assert isinstance(out, dict)


def test_compile_program(client: VermythHttpClient) -> None:
    spell = load_spell(ROOT / "examples" / "inbox_triage.spell.json")
    prog = build_semantic_program(spell)
    out = client.compile_program(prog)
    assert isinstance(out, dict)
    assert "validation" in out or "nodes" in out


def test_decide(client: VermythHttpClient) -> None:
    out = client.decide(
        {
            "intent": {
                "objective": "test",
                "scope": "axiomurgy",
                "reversibility": "PARTIAL",
                "side_effect_tolerance": "MEDIUM",
            },
            "aspects": ["VOID", "FORM"],
        }
    )
    assert isinstance(out, dict)
    assert "decision" in out
