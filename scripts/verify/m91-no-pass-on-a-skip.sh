#!/usr/bin/env bash
# M91 — A gate cannot pass on a skip.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M91" "a gate cannot pass on a skip"

check "lib.sh has check_pytest and exports the gate's id; the rig writes results-<gate>.json; test-web swallows nothing" python3 -c '
from pathlib import Path
lib = Path("scripts/verify/lib.sh").read_text()
assert "check_pytest() {" in lib and "export VERIFY_GATE=" in lib
assert "results-{gate}.json" in Path("testing/live/runner.py").read_text()
mk = Path("Makefile").read_text()
assert "playwright skipped/failed" not in mk
print("check_pytest, VERIFY_GATE, results-<gate>.json, honest test-web")
'
check "check_pytest fails a suite that skips, and one that runs nothing" bash -c '
tmp=$(mktemp -d); cat > "$tmp/test_skip.py" <<PY
import pytest
def test_a(): pass
@pytest.mark.skip(reason="not here")
def test_b(): pass
PY
. scripts/verify/lib.sh
_V_ID=M91; _V_TITLE=t
out=$(check_pytest "a skipping suite" "cd $tmp && python3 -m pytest -q test_skip.py" 2>&1)
echo "$out" | grep -q "FAIL" || { echo "a skip passed: $out"; exit 1; }
out=$(check_pytest "a skipping suite, one allowed" "cd $tmp && python3 -m pytest -q test_skip.py" 1 2>&1)
echo "$out" | grep -q "ok " || { echo "an allowed skip failed: $out"; exit 1; }
out=$(check_pytest "nothing" "cd $tmp && python3 -m pytest -q -k nothing_matches test_skip.py" 2>&1)
echo "$out" | grep -q "FAIL" || { echo "no tests ran passed: $out"; exit 1; }
rm -rf "$tmp"; echo "a skip fails, an allowed skip passes, no tests ran fails"
'
check "every gate that runs a pytest suite piped to tail has moved to check_pytest" python3 -c '
import re
from pathlib import Path
left = []
for gate in sorted(Path("scripts/verify").glob("m[0-9][0-9]-*.sh")):
    text = gate.read_text()
    for m in re.finditer(r"check_sh \"([^\"]+)\" \\\\?\n?\s*\x27[^\x27]*python3 -m pytest [^\x27]*\| tail -[0-9]+\x27", text):
        left.append(f"{gate.name}: {m.group(1)[:50]}")
assert not left, f"{len(left)} pytest checks still read the exit status only: " + "; ".join(left[:6])
print("every pytest check reads its summary")
'

verify_end
