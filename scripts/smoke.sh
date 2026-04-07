#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pytest -q

python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish >/tmp/axiomurgy_primer_run_v05.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_primer_run_v05.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 2, run
assert (root / 'artifacts' / 'primer_to_axioms_v0_5.md').exists()
assert Path(run['proof_path']).exists()
PY

python axiomurgy.py spellbooks/primer_codex --approve publish >/tmp/axiomurgy_spellbook_run_v05.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_spellbook_run_v05.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 4, run
assert run['spellbook']['name'] == 'primer_codex', run
assert (root / 'spellbooks' / 'primer_codex' / 'artifacts' / 'primer_codex_v0_5.md').exists()
assert Path(run['proof_path']).exists()
PY

python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage >/tmp/axiomurgy_mcp_run_v05.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_mcp_run_v05.json').read_text())
assert run['status'] == 'succeeded', run
assert run['proofs']['passed'] >= 1, run
assert (root / 'axiomurgy_workspace' / 'relay' / 'primer_via_mcp_v0_5.md').exists()
PY

python adapters/mock_issue_server.py >/tmp/axiomurgy_issue_server_v05.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT
sleep 1

python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket >/tmp/axiomurgy_openapi_run_v05.json
python - <<'PY'
import json
from pathlib import Path
root = Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_openapi_run_v05.json').read_text())
assert run['status'] == 'failed', run
trace = json.loads((root / 'artifacts' / 'openapi_ticket_then_fail_v0_5.trace.json').read_text())
assert trace['status'] == 'failed', trace
assert trace['compensations'], trace
assert any(item['status'] == 'compensated' for item in trace['compensations']), trace
assert 'proofs' in trace, trace
assert Path(run['proof_path']).exists(), run
PY

echo "Axiomurgy smoke passed."
