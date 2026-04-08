from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run_json(args: list[str], *, env: Optional[dict[str, str]] = None) -> Dict[str, Any]:
    proc = subprocess.run(
        [PY, str(ROOT / "axiomurgy.py"), *args],
        cwd=str(ROOT),
        env=env or os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {args}\n{proc.stdout}")
    return json.loads(proc.stdout)


def wait_http_ready(url: str, *, timeout_s: float = 3.0) -> None:
    import requests

    start = time.time()
    while time.time() - start < timeout_s:
        try:
            requests.get(url, timeout=0.2)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"server not ready: {url}")


def main() -> int:
    # 1) Review bundle generation + verify
    bundle_path = Path(tempfile.gettempdir()) / "axiomurgy_review_bundle_v08.json"
    bundle = run_json(["spellbooks/primer_codex", "--review-bundle"])
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    verified = run_json(["spellbooks/primer_codex", "--verify-review-bundle", str(bundle_path)])
    assert verified["mode"] == "verify"
    assert verified["status"] in ("exact", "partial")

    # 2) Attested execution (spellbook)
    exec_result = run_json(
        ["spellbooks/primer_codex", "--approve", "publish", "--review-bundle-in", str(bundle_path)],
    )
    assert exec_result["status"] == "succeeded"
    assert exec_result.get("attestation", {}).get("status") in ("exact", "partial")
    proof_path = Path(exec_result["proof_path"])
    assert proof_path.exists()
    # diffable + raw artifacts exist
    trace_path = Path(exec_result["trace_path"])
    assert trace_path.exists()
    assert trace_path.with_suffix(".raw.json").exists()

    # 3) Rollback demo (OpenAPI) + raw+diff artifacts
    with tempfile.TemporaryDirectory() as tmpdir:
        port = 8951
        env = os.environ.copy()
        env["AXIOMURGY_ISSUE_PORT"] = str(port)
        env["AXIOMURGY_ISSUE_DB"] = str(Path(tmpdir) / "issues.json")
        server = subprocess.Popen(
            [PY, str(ROOT / "adapters" / "mock_issue_server.py")],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_http_ready(f"http://127.0.0.1:{port}/tickets/does-not-exist")
            result = run_json(["examples/openapi_ticket_then_fail.spell.json", "--approve", "create_ticket"], env=env)
            assert result["status"] == "failed"
            trace = Path(result["trace_path"])
            assert trace.exists()
            assert trace.with_suffix(".raw.json").exists()
        finally:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)

    print("Axiomurgy smoke.py passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

