#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pytest -q

python axiomurgy.py spellbooks/primer_codex --describe >/tmp/axiomurgy_describe_v06.json
cp /tmp/axiomurgy_describe_v06.json spellbooks/primer_codex/artifacts/primer_codex_publish_v0_6.describe.json
python - <<'PY'
import json
from pathlib import Path
run = json.loads(Path('/tmp/axiomurgy_describe_v06.json').read_text())
assert run['mode'] == 'describe', run
assert run['spellbook']['name'] == 'primer_codex', run
assert run['spellbook']['resolved_entrypoint'] == 'publish_codex', run
PY

python axiomurgy.py spellbooks/primer_codex --lint >/tmp/axiomurgy_lint_v06.json
cp /tmp/axiomurgy_lint_v06.json spellbooks/primer_codex/artifacts/primer_codex_publish_v0_6.lint.json
python - <<'PY'
import json
from pathlib import Path
run = json.loads(Path('/tmp/axiomurgy_lint_v06.json').read_text())
assert run['kind'] == 'spellbook', run
assert run['ok'] is True, run
assert not run['errors'], run
PY

python axiomurgy.py spellbooks/primer_codex --plan \
  --manifest-out spellbooks/primer_codex/artifacts/primer_codex_publish_v0_6.approval_manifest.json \
  >/tmp/axiomurgy_plan_v06.json
cp /tmp/axiomurgy_plan_v06.json spellbooks/primer_codex/artifacts/primer_codex_publish_v0_6.plan.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_plan_v06.json').read_text())
assert run['mode'] == 'plan', run
assert run['required_approvals'], run
assert any(item['step_id'] == 'publish' for item in run['write_steps']), run
manifest_path = root / 'spellbooks' / 'primer_codex' / 'artifacts' / 'primer_codex_publish_v0_6.approval_manifest.json'
assert manifest_path.exists(), manifest_path
manifest = json.loads(manifest_path.read_text())
assert manifest['required_approvals'], manifest
assert manifest['write_steps'], manifest
PY

python axiomurgy.py spellbooks/primer_codex --review-bundle >/tmp/axiomurgy_review_bundle_v07.json
cp /tmp/axiomurgy_review_bundle_v07.json spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
python axiomurgy.py spellbooks/primer_codex --verify-review-bundle /tmp/axiomurgy_review_bundle_v07.json >/tmp/axiomurgy_verify_v07.json
cp /tmp/axiomurgy_verify_v07.json spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.verify.json
python - <<'PY'
import json
from pathlib import Path
verify = json.loads(Path('/tmp/axiomurgy_verify_v07.json').read_text())
assert verify['mode'] == 'verify', verify
assert verify['status'] in ('exact','partial'), verify
PY

python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish >/tmp/axiomurgy_primer_run_v06.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_primer_run_v06.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 2, run
assert (root / 'artifacts' / 'primer_to_axioms_v0_6.md').exists()
assert Path(run['proof_path']).exists()
PY

python axiomurgy.py spellbooks/primer_codex --approve publish --review-bundle-in /tmp/axiomurgy_review_bundle_v07.json >/tmp/axiomurgy_spellbook_run_v06.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_spellbook_run_v06.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 4, run
assert run['spellbook']['name'] == 'primer_codex', run
assert (root / 'spellbooks' / 'primer_codex' / 'artifacts' / 'primer_codex_v0_6.md').exists()
assert Path(run['proof_path']).exists()
assert run.get('attestation', {}).get('status') in ('exact','partial'), run.get('attestation')
PY

python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage >/tmp/axiomurgy_mcp_run_v06.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_mcp_run_v06.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 1, run
assert (root / 'axiomurgy_workspace' / 'relay' / 'primer_via_mcp_v0_6.md').exists()
PY

python adapters/mock_issue_server.py >/tmp/axiomurgy_issue_server_v06.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT
sleep 1

python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket >/tmp/axiomurgy_openapi_run_v06.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_openapi_run_v06.json').read_text())
assert run['status'] == 'failed', run
trace = json.loads((root / 'artifacts' / 'openapi_ticket_then_fail_v0_6.trace.json').read_text())
assert trace['status'] == 'failed', trace
assert trace['compensations'], trace
assert any(item['status'] == 'compensated' for item in trace['compensations']), trace
assert 'proofs' in trace, trace
assert Path(run['proof_path']).exists(), run
# Raw trace should exist for debugging (wall-clock timings preserved).
assert (root / 'artifacts' / 'openapi_ticket_then_fail_v0_6.trace.raw.json').exists()
PY

echo "Axiomurgy smoke passed."
