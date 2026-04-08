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
    assert "capabilities" in bundle
    assert "envelope" in bundle["capabilities"]
    assert "kinds" in bundle["capabilities"]["envelope"]
    assert bundle["capabilities"]["envelope"]["kinds"]
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

    # 2b) Deliberate overreach: shrink reviewed envelope and expect mismatch attestation.
    overreach_path = Path(tempfile.gettempdir()) / "axiomurgy_review_bundle_v09_overreach.json"
    bundle_over = dict(bundle)
    bundle_over["capabilities"] = dict(bundle_over.get("capabilities") or {})
    env = dict((bundle_over["capabilities"].get("envelope") or {}))
    kinds = list(env.get("kinds") or [])
    env["kinds"] = [k for k in kinds if k != "filesystem.write"]
    bundle_over["capabilities"]["envelope"] = env
    overreach_path.write_text(json.dumps(bundle_over, indent=2, ensure_ascii=False), encoding="utf-8")
    exec_over = run_json(
        ["spellbooks/primer_codex", "--approve", "publish", "--review-bundle-in", str(overreach_path)],
    )
    assert exec_over.get("attestation", {}).get("status") == "mismatch"
    assert "filesystem.write" in ((exec_over.get("capabilities") or {}).get("overreach") or [])

    # 2c) Enforced vessel: overreach becomes blocked before side effects.
    exec_blocked = run_json(
        ["spellbooks/primer_codex", "--approve", "publish", "--review-bundle-in", str(overreach_path), "--enforce-review-bundle"],
    )
    assert exec_blocked.get("execution_outcome") == "blocked_overreach"
    blocked_trace = Path(exec_blocked["trace_path"])
    assert blocked_trace.exists()
    raw_blocked = blocked_trace.with_suffix(".raw.json")
    assert raw_blocked.exists()
    raw_doc = json.loads(raw_blocked.read_text(encoding="utf-8"))
    diff_doc = json.loads(blocked_trace.read_text(encoding="utf-8"))
    assert raw_doc.get("capability_denials")
    assert diff_doc.get("capability_denials")

    # 2d) Ouroboros Chamber demo: accept improvement, reject regression, emit witnesses.
    cycle_cfg = {
        "max_revolutions": 3,
        "flux_budget": 3,
        "plateau_window": 2,
        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
        "mutation_target_allowlist": ["spell.inputs.score"],
        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0, 3.0]}],
        "rollback_mode": "shadow_copy",
        "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
    }
    cycle_cfg_path = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v11.json"
    cycle_cfg_path.write_text(json.dumps(cycle_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    cycle_result = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_cfg_path)])
    assert cycle_result["mode"] == "cycle"
    witness = json.loads(Path(cycle_result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert any(item.get("accepted") for item in witness.get("revolutions", []))
    assert any(item.get("rejected") for item in witness.get("revolutions", []))
    assert Path(cycle_result["ouroboros_witness_raw_path"]).exists()

    # 2e) Ouroboros v1.2: recall + mutation families (enum / numeric / string), deterministic proposals.
    cycle_v12 = ROOT / "examples" / "cycles" / "ouroboros_cycle_v12.json"
    assert cycle_v12.exists()
    cycle_v12_result = run_json(
        ["examples/ouroboros_score_fixture_v12.spell.json", "--cycle-config", str(cycle_v12)],
    )
    witness_v12 = json.loads(Path(cycle_v12_result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert "recall" in witness_v12
    assert witness_v12["recall"].get("best_score_so_far") is not None
    fams = {r.get("mutation_family") for r in witness_v12.get("revolutions", [])}
    assert "enum" in fams and "numeric" in fams
    pids = [r.get("proposal_id") for r in witness_v12.get("revolutions", [])]
    assert len(pids) == len(set(pids))

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

