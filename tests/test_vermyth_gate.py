"""Unit tests for Vermyth gate modes (mocked HTTP; no live server)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import tempfile

import requests

from axiomurgy.adapters.vermyth_http import VermythHttpError
from axiomurgy.execution import evaluate_policy, RuneContext
from axiomurgy.legacy import Spell, SpellValidationError, Step, load_json
from axiomurgy.vermyth_integration import (
    VermythGateTransportFailureKind,
    _classify_gate_transport_failure,
    run_vermyth_gate,
    vermyth_gate_policy_notes,
)

ROOT = Path(__file__).resolve().parents[1]


def _minimal_spell() -> Spell:
    return Spell(
        name="gate_test",
        intent="test intent",
        inputs={},
        constraints={},
        graph=[Step(step_id="s1", rune="mirror.read", effect="read", args={"input": "x"})],
        rollback=[],
        witness={"record": False},
        source_path=Path("t.spell.json"),
    )


def _policy_gate(**kwargs: object) -> dict:
    base = {
        "version": "2.0.0",
        "requires_approval": [],
        "deny": [],
        "vermyth_gate": {
            "enabled": True,
            "mode": "advisory",
            "timeout_ms": 2000,
            "on_timeout": "allow",
            "on_incoherent": "allow",
        },
    }
    vg = dict(base["vermyth_gate"])  # type: ignore[index]
    for k, v in kwargs.items():
        vg[k] = v
    base["vermyth_gate"] = vg
    return base


class TestVermythGateModes(unittest.TestCase):
    def setUp(self) -> None:
        self.spell = _minimal_spell()

    def _decide_deny(self) -> dict:
        return {"decision": {"action": "DENY", "rationale": "no"}, "cast": {}}

    def _decide_allow(self) -> dict:
        return {"decision": {"action": "ALLOW", "rationale": "ok"}, "cast": {}}

    def _decide_reshape(self) -> dict:
        return {"decision": {"action": "RESHAPE", "rationale": "reshape"}, "cast": {}}

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_advisory_deny_does_not_raise(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_deny()  # type: ignore[assignment]
        pol = _policy_gate(mode="advisory")
        r = run_vermyth_gate(self.spell, pol)
        self.assertEqual(r.get("status"), "ok")
        self.assertEqual(r.get("action"), "DENY")
        self.assertEqual(vermyth_gate_policy_notes(r), [])

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_policy_input_den_merges_notes(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_deny()  # type: ignore[assignment]
        pol = _policy_gate(mode="policy_input")
        r = run_vermyth_gate(self.spell, pol)
        notes = vermyth_gate_policy_notes(r)
        self.assertTrue(any(x.startswith("vermyth_gate:DENY") for x in notes))

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_hard_stop_deny_raises_when_on_incoherent_deny(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_deny()  # type: ignore[assignment]
        pol = _policy_gate(mode="hard_stop", on_incoherent="deny")
        with self.assertRaises(SpellValidationError) as ctx:
            run_vermyth_gate(self.spell, pol)
        self.assertIn("hard_stop", str(ctx.exception))

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_hard_stop_deny_allowed_when_on_incoherent_allow(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_deny()  # type: ignore[assignment]
        pol = _policy_gate(mode="hard_stop", on_incoherent="allow")
        r = run_vermyth_gate(self.spell, pol)
        self.assertEqual(r.get("action"), "DENY")

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_reshape_not_hard_stop(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_reshape()  # type: ignore[assignment]
        pol = _policy_gate(mode="hard_stop", on_incoherent="deny")
        r = run_vermyth_gate(self.spell, pol)
        self.assertEqual(r.get("action"), "RESHAPE")

    @patch.dict("os.environ", {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.adapters.vermyth_http.VermythHttpClient.decide")
    def test_evaluate_policy_merges_notes_once(self, mock_decide: object) -> None:
        mock_decide.return_value = self._decide_deny()  # type: ignore[assignment]
        pol = _policy_gate(mode="policy_input")
        r = run_vermyth_gate(self.spell, pol)
        notes = vermyth_gate_policy_notes(r)
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        policy_doc = load_json(policy_path)
        ad = Path(tempfile.mkdtemp())
        ctx = RuneContext(
            self.spell,
            ["read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"],
            set(),
            True,
            ad,
            policy_doc,
        )
        ctx.vermyth_policy_notes = list(notes)
        step = self.spell.graph[0]
        d1 = evaluate_policy(ctx, step)
        d2 = evaluate_policy(ctx, step)
        self.assertTrue(any("vermyth_gate" in x for x in d1.reasons))
        self.assertEqual(len([x for x in d1.reasons if "vermyth_gate" in x]), 1)
        vg_count = sum(1 for x in d2.reasons if "vermyth_gate" in x)
        self.assertEqual(vg_count, 0)


class TestGateTransportClassification(unittest.TestCase):
    def test_classify_transport_failures(self) -> None:
        self.assertEqual(
            _classify_gate_transport_failure(VermythHttpError("HTTP 500")),
            VermythGateTransportFailureKind.HTTP_ADAPTER,
        )
        self.assertEqual(
            _classify_gate_transport_failure(requests.Timeout("t")),
            VermythGateTransportFailureKind.REQUESTS_TIMEOUT,
        )
        self.assertEqual(
            _classify_gate_transport_failure(requests.ConnectionError("c")),
            VermythGateTransportFailureKind.REQUESTS_CONNECTION,
        )
        self.assertEqual(
            _classify_gate_transport_failure(requests.HTTPError()),
            VermythGateTransportFailureKind.REQUESTS_HTTP,
        )
        self.assertEqual(
            _classify_gate_transport_failure(requests.RequestException("x")),
            VermythGateTransportFailureKind.REQUESTS_OTHER,
        )
        self.assertEqual(_classify_gate_transport_failure(OSError(9, "bad")), VermythGateTransportFailureKind.OS_ERROR)
        self.assertEqual(_classify_gate_transport_failure(ValueError("v")), VermythGateTransportFailureKind.VALUE_ERROR)
        self.assertEqual(_classify_gate_transport_failure(RuntimeError("r")), VermythGateTransportFailureKind.OTHER)


class TestVermythGateTransport(unittest.TestCase):
    def _spell(self) -> Spell:
        return _minimal_spell()

    @patch("axiomurgy.vermyth_integration._env_base_url", return_value=None)
    def test_on_timeout_deny_raises_on_missing_url(self, _mock_url: object) -> None:
        pol = {
            "version": "2.0.0",
            "requires_approval": [],
            "deny": [],
            "vermyth_gate": {
                "enabled": True,
                "mode": "advisory",
                "timeout_ms": 2000,
                "on_timeout": "deny",
                "on_incoherent": "allow",
            },
        }
        with self.assertRaises(SpellValidationError):
            run_vermyth_gate(self._spell(), pol)
