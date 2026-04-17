"""Vermyth HTTP adapter error contracts (no live server)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from axiomurgy.adapters.vermyth_http import VermythHttpClient, VermythHttpError


def _sample_recommend_input() -> dict:
    return {
        "intent": {
            "objective": "test objective",
            "scope": "axiomurgy:t",
            "reversibility": "PARTIAL",
            "side_effect_tolerance": "MEDIUM",
        },
    }


class TestVermythHttpAdapter(unittest.TestCase):
    def test_http_400_raises(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 503
            r.text = "unavailable"
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            with self.assertRaises(VermythHttpError):
                c.decide({"intent": {}})

    def test_invalid_json_raises(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.side_effect = ValueError("bad json")
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            with self.assertRaises(VermythHttpError):
                c.compile_program({"name": "x", "nodes": []})

    def test_non_object_json_raises(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = [1, 2, 3]
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            with self.assertRaises(VermythHttpError):
                c.arcane_recommend(skill_id="s", input_=_sample_recommend_input())

    def test_connection_error_propagates_from_requests(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            post.side_effect = requests.ConnectionError("refused")
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=0.1)
            with self.assertRaises(requests.ConnectionError):
                c.decide({"intent": {}})

    def test_decide_unwraps_http_result_envelope(self) -> None:
        inner = {"decision": {"action": "ALLOW", "rationale": "ok"}, "cast": {}}
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"result": inner}
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            out = c.decide({"intent": {"objective": "x"}})
            self.assertEqual(out, inner)
            self.assertEqual(out["decision"]["action"], "ALLOW")

    def test_decide_passes_through_when_no_result_key(self) -> None:
        """Backward compatibility if a mock or proxy returns tool-shaped JSON without an envelope."""
        inner = {"decision": {"action": "DENY", "rationale": "no"}, "cast": {}}
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = inner
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            out = c.decide({"intent": {"objective": "x"}})
            self.assertEqual(out, inner)

    def test_compile_program_unwraps_http_result_envelope(self) -> None:
        inner = {"program_id": "p1", "validation": {"ok": True, "errors": [], "warnings": []}}
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"result": inner}
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            out = c.compile_program({"name": "n", "nodes": []})
            self.assertEqual(out, inner)
            self.assertIn("validation", out)

    def test_arcane_recommend_does_not_unwrap(self) -> None:
        body = {"skill_id": "k", "recommendations": []}
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = body
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            out = c.arcane_recommend(skill_id="k", input_=_sample_recommend_input())
            self.assertEqual(out, body)

    def test_posts_json_body_for_arcane_recommend(self) -> None:
        inp = _sample_recommend_input()
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"recommendations": []}
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            c.arcane_recommend(skill_id="axiomurgy.test", input_=inp)
            args, kwargs = post.call_args
            self.assertEqual(kwargs["json"]["skill_id"], "axiomurgy.test")
            self.assertEqual(kwargs["json"]["input"], inp)

    def test_bearer_header_from_explicit_token(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"decision": {}, "cast": {}}
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0, http_token="secret-token")
            c.decide({"intent": {"objective": "x"}})
            _args, kwargs = post.call_args
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-token")

    @patch.dict("os.environ", {"VERMYTH_HTTP_TOKEN": "from-env"}, clear=False)
    def test_bearer_header_from_vermyth_http_token_env(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"result": {"decision": {}, "cast": {}}}
            post.return_value = r
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=1.0)
            c.decide({"intent": {"objective": "x"}})
            _args, kwargs = post.call_args
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer from-env")
