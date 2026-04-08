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
    assert cycle_result.get("run_id")
    assert cycle_result.get("run_artifact_root")
    assert cycle_result.get("run_manifest_path")
    assert "run_capsule" in witness
    assert Path(cycle_result["run_manifest_path"]).exists()

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

    # 2f) Ouroboros v1.3: proposal_plan artifacts, review-aware preflight, skip before veil when envelope forbids.
    bundle_ouro = run_json(["examples/ouroboros_score_fixture.spell.json", "--review-bundle"])
    full_bundle_path = Path(tempfile.gettempdir()) / "axiomurgy_review_bundle_ouroboros_full.json"
    full_bundle_path.write_text(json.dumps(bundle_ouro, indent=2, ensure_ascii=False), encoding="utf-8")
    cycle_v13_path = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v13.json"
    cycle_v13_path.write_text(
        json.dumps(
            {
                "max_revolutions": 3,
                "flux_budget": 3,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0, 3.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v13_ok = run_json(
        [
            "examples/ouroboros_score_fixture.spell.json",
            "--cycle-config",
            str(cycle_v13_path),
            "--review-bundle-in",
            str(full_bundle_path),
        ]
    )
    assert res_v13_ok.get("proposal_plan_path")
    assert Path(res_v13_ok["proposal_plan_path"]).exists()
    assert Path(res_v13_ok.get("proposal_plan_raw_path", "")).exists()
    wit_ok = json.loads(Path(res_v13_ok["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_ok.get("flux_attempts", 0) >= 1
    plan_ok = json.loads(Path(res_v13_ok["proposal_plan_path"]).read_text(encoding="utf-8"))
    assert plan_ok.get("counts", {}).get("admissible", 0) >= 1

    shrunk = dict(bundle_ouro)
    shrunk["capabilities"] = dict(shrunk.get("capabilities") or {})
    shrunk["capabilities"]["envelope"] = dict((shrunk["capabilities"].get("envelope") or {}))
    kinds = list(shrunk["capabilities"]["envelope"].get("kinds") or [])
    shrunk["capabilities"]["envelope"]["kinds"] = [k for k in kinds if k != "filesystem.write"]
    shrunk_path = Path(tempfile.gettempdir()) / "axiomurgy_review_bundle_ouroboros_shrunk.json"
    shrunk_path.write_text(json.dumps(shrunk, indent=2, ensure_ascii=False), encoding="utf-8")
    res_v13_skip = run_json(
        [
            "examples/ouroboros_score_fixture.spell.json",
            "--cycle-config",
            str(cycle_v13_path),
            "--review-bundle-in",
            str(shrunk_path),
        ]
    )
    wit_skip = json.loads(Path(res_v13_skip["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_skip.get("flux_attempts") == 0
    assert wit_skip.get("preflight_skips")
    assert len(wit_skip["preflight_skips"]) >= 1

    # 2g) Ouroboros v1.4+: proposal_plan diversification (v12 spell: score + note preserves metric channel).
    cycle_v14_path = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v14.json"
    cycle_v14_path.write_text(
        json.dumps(
            {
                "max_revolutions": 2,
                "flux_budget": 2,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score", "spell.inputs.note"],
                "mutation_targets": [
                    {"path": "spell.inputs.score", "choices": [1.0, 2.0]},
                    {"path": "spell.inputs.note", "choices": ["a", "b"]},
                ],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v14 = run_json(
        ["examples/ouroboros_score_fixture_v12.spell.json", "--cycle-config", str(cycle_v14_path)]
    )
    plan_v14 = json.loads(Path(res_v14["proposal_plan_path"]).read_text(encoding="utf-8"))
    assert plan_v14.get("proposal_plan_version") == "1.5.0"
    assert plan_v14.get("diversification_summary")
    assert plan_v14.get("score_channel_contract", {}).get("score_channel_status") == "aligned"
    assert plan_v14.get("score_channel_summary")
    ranked_v14 = plan_v14.get("ranked_proposals") or []
    adm_v14 = [r for r in ranked_v14 if r.get("admissibility_status") == "admissible"]
    assert len({str(r["effect_signature_id"]) for r in adm_v14}) >= 2

    # 2h) Ouroboros v1.5: score-channel clear-break preflight skip + preserved proposal executes.
    cycle_v15_path = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v15.json"
    cycle_v15_path.write_text(
        json.dumps(
            {
                "max_revolutions": 3,
                "flux_budget": 3,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score", "spell.inputs.score_path"],
                "mutation_targets": [
                    {"path": "spell.inputs.score", "choices": [2.0]},
                    {"path": "spell.inputs.score_path", "choices": ["artifacts/elsewhere.json"]},
                ],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v15 = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_v15_path)])
    plan_v15 = json.loads(Path(res_v15["proposal_plan_path"]).read_text(encoding="utf-8"))
    assert plan_v15.get("proposal_plan_version") == "1.5.0"
    assert plan_v15.get("score_channel_summary", {}).get("clear_break_inadmissible", 0) >= 1
    wit_v15 = json.loads(Path(res_v15["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_v15.get("score_channel_contract")
    assert wit_v15.get("score_channel_summary")
    skips = wit_v15.get("preflight_skips") or []
    assert any(s.get("skip_reason") == "score_channel_clear_break" for s in skips)
    assert wit_v15.get("flux_attempts", 0) >= 1

    # 2i) Ouroboros v1.6: optional acceptance_contract, seal_decision, acceptance_summary.
    assert "acceptance_contract" in witness and "acceptance_summary" in witness
    cycle_v16_accept = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v16_accept.json"
    cycle_v16_accept.write_text(
        json.dumps(
            {
                "max_revolutions": 2,
                "flux_budget": 2,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                "acceptance_contract": {"tie_breakers": ["lower_ordering_index"]},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v16 = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_v16_accept)])
    wit_v16 = json.loads(Path(res_v16["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    wit_v16_raw = json.loads(Path(res_v16["ouroboros_witness_raw_path"]).read_text(encoding="utf-8"))
    assert wit_v16.get("acceptance_contract", {}).get("primary_metric") == "maximize"
    assert "accepted_by_contract" in wit_v16.get("acceptance_summary", {})
    assert any(r.get("seal_decision") for r in wit_v16.get("revolutions", []))
    assert wit_v16_raw["lineage_summary"]["final_active_baseline_id"] == wit_v16["lineage_summary"]["final_active_baseline_id"]
    cycle_v16_guard = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v16_guard.json"
    cycle_v16_guard.write_text(
        json.dumps(
            {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [99.0]}],
                "acceptance_contract": {
                    "guardrails": [
                        {
                            "metric_path": "ouroboros_score.json",
                            "comparator": "<=",
                            "baseline_source": "initial_baseline",
                        }
                    ]
                },
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v16g = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_v16_guard)])
    wit_v16g = json.loads(Path(res_v16g["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_v16g.get("acceptance_summary", {}).get("rejected_by_guardrail", 0) >= 1

    # 2j) Ouroboros v1.7: baseline registry, promotions, deterministic lineage ids (diffable + raw).
    assert "baseline_registry" in witness and "promotion_records" in witness and "lineage_summary" in witness
    assert witness["baseline_registry"][0]["baseline_id"] == "bl_0001"
    assert witness["lineage_summary"]["final_active_baseline_id"].startswith("bl_")
    assert wit_v16["lineage_summary"]["total_baselines_created"] >= 2
    assert wit_v16["lineage_summary"]["total_promotions"] >= 1
    assert wit_v16["promotion_records"][0]["from_baseline_id"] == "bl_0001"
    for r in wit_v16.get("revolutions", []):
        if r.get("seal_decision"):
            assert "baseline_reference_used_id" in r["seal_decision"]
    cycle_v17_reject = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v17_reject.json"
    cycle_v17_reject.write_text(
        json.dumps(
            {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [0.1]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    res_v17r = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_v17_reject)])
    wit_v17r = json.loads(Path(res_v17r["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_v17r["lineage_summary"]["total_promotions"] == 0
    assert wit_v17r["lineage_summary"]["final_active_baseline_id"] == "bl_0001"

    # 2k) Ouroboros v1.8: two cycle runs under the same --artifact-dir use distinct run_id / capsule roots.
    smoke_adir = Path(tempfile.gettempdir()) / "axiomurgy_smoke_v18_runs"
    smoke_adir.mkdir(parents=True, exist_ok=True)
    cr_a = run_json(
        [
            "examples/ouroboros_score_fixture.spell.json",
            "--artifact-dir",
            str(smoke_adir),
            "--cycle-config",
            str(cycle_v17_reject),
        ]
    )
    cr_b = run_json(
        [
            "examples/ouroboros_score_fixture.spell.json",
            "--artifact-dir",
            str(smoke_adir),
            "--cycle-config",
            str(cycle_v17_reject),
        ]
    )
    assert cr_a["run_id"] != cr_b["run_id"]
    assert cr_a["run_artifact_root"] != cr_b["run_artifact_root"]
    mf_a = json.loads(Path(cr_a["run_manifest_path"]).read_text(encoding="utf-8"))
    assert mf_a.get("run_capsule", {}).get("run_id") == cr_a["run_id"]
    assert Path(cr_a["ouroboros_witness_path"]).exists() and Path(cr_b["ouroboros_witness_path"]).exists()

    # 2l) Ouroboros v1.9: multiple revolutions in one run use distinct revolution artifact dirs; witness + manifest list capsules.
    cycle_v19 = Path(tempfile.gettempdir()) / "axiomurgy_cycle_config_v19.json"
    cycle_v19.write_text(
        json.dumps(
            {
                "max_revolutions": 3,
                "flux_budget": 3,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0, 3.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cr_v19 = run_json(["examples/ouroboros_score_fixture.spell.json", "--cycle-config", str(cycle_v19)])
    assert cr_v19.get("revolution_count_total", 0) >= 1
    assert cr_v19.get("revolution_count_executed", 0) + cr_v19.get("revolution_count_skipped", 0) == cr_v19[
        "revolution_count_total"
    ]
    wit_v19 = json.loads(Path(cr_v19["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_v19.get("revolution_capsules")
    assert wit_v19["run_capsule"].get("revolution_capsules")
    mf_v19 = json.loads(Path(cr_v19["run_manifest_path"]).read_text(encoding="utf-8"))
    assert mf_v19.get("revolution_capsules") and mf_v19.get("proposal_id_to_revolution_id") is not None
    run_root = Path(cr_v19["run_artifact_root"])
    rev_dirs = sorted([p for p in (run_root / "revolutions").iterdir() if p.is_dir()]) if (run_root / "revolutions").is_dir() else []
    assert len(rev_dirs) >= 2
    trace_a = rev_dirs[0] / "ouroboros_score_fixture.trace.json"
    trace_b = rev_dirs[1] / "ouroboros_score_fixture.trace.json"
    assert trace_a.exists() and trace_b.exists()
    assert trace_a.resolve() != trace_b.resolve()
    skipped_caps = [c for c in wit_v19["revolution_capsules"] if not c.get("executed")]
    for c in skipped_caps:
        assert c.get("artifact_root_relative") is None
    shrunk_v19 = run_json(["examples/ouroboros_score_fixture.spell.json", "--review-bundle"])
    shrunk_v19["capabilities"] = dict(shrunk_v19.get("capabilities") or {})
    shrunk_v19["capabilities"]["envelope"] = dict((shrunk_v19["capabilities"].get("envelope") or {}))
    kinds_v19 = list(shrunk_v19["capabilities"]["envelope"].get("kinds") or [])
    shrunk_v19["capabilities"]["envelope"]["kinds"] = [k for k in kinds_v19 if k != "filesystem.write"]
    shrunk_v19_path = Path(tempfile.gettempdir()) / "axiomurgy_review_bundle_v19_shrunk.json"
    shrunk_v19_path.write_text(json.dumps(shrunk_v19, indent=2, ensure_ascii=False), encoding="utf-8")
    cr_skip = run_json(
        [
            "examples/ouroboros_score_fixture.spell.json",
            "--cycle-config",
            str(cycle_v19),
            "--review-bundle-in",
            str(shrunk_v19_path),
        ]
    )
    wit_skip_v19 = json.loads(Path(cr_skip["ouroboros_witness_path"]).read_text(encoding="utf-8"))
    assert wit_skip_v19["flux_attempts"] == 0
    assert all(not c.get("executed") for c in wit_skip_v19["revolution_capsules"])
    skip_root = Path(cr_skip["run_artifact_root"])
    if (skip_root / "revolutions").is_dir():
        assert not list((skip_root / "revolutions").iterdir())

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

