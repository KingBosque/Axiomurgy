"""Vermyth HTTP adapter error contracts (no live server)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from axiomurgy.adapters.vermyth_http import VermythHttpClient, VermythHttpError


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
                c.arcane_recommend(skill_id="s", input_="x")

    def test_connection_error_propagates_from_requests(self) -> None:
        with patch("axiomurgy.adapters.vermyth_http.requests.post") as post:
            post.side_effect = requests.ConnectionError("refused")
            c = VermythHttpClient("http://127.0.0.1:9/", timeout_s=0.1)
            with self.assertRaises(requests.ConnectionError):
                c.decide({"intent": {}})
