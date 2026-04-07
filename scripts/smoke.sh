#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pytest -q

python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish >/tmp/axiomurgy_primer_run.json
python - <<'PY'
import json
from pathlib import Path
root = Path('/mnt/data/axiomurgy_v0_4') if Path('/mnt/data/axiomurgy_v0_4').exists() else Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_primer_run.json').read_text())
assert run['status'] == 'succeeded', run
assert (root / 'artifacts' / 'primer_to_axioms_v0_4.md').exists()
assert (root / 'artifacts' / 'primer_to_axioms_v0_4.trace.json').exists()
PY

python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage >/tmp/axiomurgy_mcp_run.json
python - <<'PY'
import json
from pathlib import Path
root = Path('/mnt/data/axiomurgy_v0_4') if Path('/mnt/data/axiomurgy_v0_4').exists() else Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_mcp_run.json').read_text())
assert run['status'] == 'succeeded', run
assert (root / 'axiomurgy_workspace' / 'relay' / 'primer_via_mcp_v0_4.md').exists()
PY

python adapters/mock_issue_server.py >/tmp/axiomurgy_issue_server.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT
sleep 1

python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket >/tmp/axiomurgy_openapi_run.json
python - <<'PY'
import json
from pathlib import Path
root = Path('/mnt/data/axiomurgy_v0_4') if Path('/mnt/data/axiomurgy_v0_4').exists() else Path.cwd()
run = json.loads(Path('/tmp/axiomurgy_openapi_run.json').read_text())
assert run['status'] == 'failed', run
trace = json.loads((root / 'artifacts' / 'openapi_ticket_then_fail_v0_4.trace.json').read_text())
assert trace['status'] == 'failed', trace
assert trace['compensations'], trace
assert any(item['status'] == 'compensated' for item in trace['compensations']), trace
PY

echo "Axiomurgy smoke passed."
