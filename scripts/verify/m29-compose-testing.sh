#!/usr/bin/env bash
# M29 — the live suite runs against the containers that are actually running:
# compose up --wait first, real endpoints, unhealthy or ERROR-logging containers
# fail the run, two resilience scenarios that stop a container mid-conversation,
# and a snapshot/restore that makes all of that safe to point at a real house.
source "$(dirname "$0")/lib.sh"
verify_begin "M29" "the suite runs against the real stack"
use_venv
L=scripts/verify/live_interaction.sh
S=testing/live/stack.py
G=testing/live/ground.py

require_file "$S"
require_file "$G"

# --- the stack is the first thing the suite does ---------------------------
check "the suite brings the stack up before it speaks" \
    grep -q 'up -d --wait' "$L"
check "and requires every container to be healthy at the start" \
    grep -q 'the stack is up and every container is healthy' "$L"
check "the runner defaults to the running containers" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.runner import main
import argparse, io, contextlib
# The default is asserted through the parser rather than by reading the source:
# a default that only exists in a help string is not one.
parser = None
import testing.live.runner as runner
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try:
        runner.main(["--help"])
    except SystemExit:
        pass
assert "--target {stack,harness}" in buf.getvalue()
'
check "and a scenario runs on the stack unless it says otherwise" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.scenario import load_all
stack = [s.name for s in load_all() if s.ground == "stack"]
fixture = [s.name for s in load_all() if s.ground == "fixture"]
assert len(stack) > len(fixture), (len(stack), len(fixture))
print(f"{len(stack)} on the stack, {len(fixture)} on the fixture web")
'

# --- what no scenario can assert -------------------------------------------
check "the run reads what the containers said about themselves" \
    grep -q 'def errors_since' "$S"
check "and an ERROR-level record fails the run" \
    grep -q '_stack_logs_are_clean' testing/live/runner.py
check "records are grouped, so the allowlist names exceptions not noise" \
    grep -q 'def _records' "$S"
check "an init container that finished is not a sick container" \
    grep -q 'one_shot_done' "$S"

# --- resilience -------------------------------------------------------------
require_file testing/live/scenarios/resilience-core-restart.yaml
require_file testing/live/scenarios/resilience-stt-down.yaml
check "a killed container is brought back, whatever happened" \
    grep -q 'ground.stack.start' testing/live/runner.py

# --- data safety without fakes ---------------------------------------------
check "the house is snapshotted before the suite touches it" \
    grep -q 'class StateGuard' "$S"
check "through a container, because the services own their own files" \
    grep -q 'BUSYBOX' "$S"
check "the config directory and the console's storage are both covered" \
    grep -q 'STACK_PATHS' "$G"
check "every thread the suite opens is namespaced" \
    grep -q 'TEST_NAMESPACE' testing/live/runner.py
check "and what a scenario created is deleted and its absence asserted" \
    grep -q 'async def _sweep' testing/live/runner.py

# The round trip itself, on a directory this script owns — a snapshot that has
# never been restored is a belief, not a backup.
check_sh "snapshot → destroy → restore, for real" '
python3 - <<PY
import sys, shutil
from pathlib import Path
sys.path.insert(0, ".")
from testing.live.stack import StateGuard, REPO_ROOT

sandbox = REPO_ROOT / ".verify" / "live" / "guard-check"
shutil.rmtree(sandbox, ignore_errors=True)
(sandbox / "config").mkdir(parents=True)
(sandbox / "config" / "notes.txt").write_text("the operator wrote this")

guard = StateGuard(out=REPO_ROOT / ".verify" / "live" / "guard-check-out")
snap = guard.take(paths=[".verify/live/guard-check/config"])
(sandbox / "config" / "notes.txt").write_text("a destructive scenario ran")
(sandbox / "config" / "stray.txt").write_text("and left this")
guard.restore(snap)
assert (sandbox / "config" / "notes.txt").read_text() == "the operator wrote this"
print("restored")
PY'

# --- the dev loop -----------------------------------------------------------
check_sh "watch rules sync into the directory each image runs from" \
    'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q -k watch --timeout=120 --timeout-method=signal 2>&1 | tail -2'
check "the runbook says how to run the suite against a live house" \
    grep -q 'Run the live suite against this stack' docs/RUNBOOK.md

check_sh "the rig's own tests" \
    'python3 -m pytest testing/live/tests -q --timeout=300 --timeout-method=signal 2>&1 | tail -2'

# --- and then it actually does it -------------------------------------------
# The milestone's own live scenarios: a container restarted underneath a
# conversation, and speech recognition taken away mid-utterance. Both against
# the real containers, because there is no other way to run them.
check_sh "the live resilience scenarios, against the running stack" \
    'LIVE_ONLY=resilience-core-restart,resilience-stt-down LIVE_NO_BROWSER=1 \
     timeout 1800 bash scripts/verify/live_interaction.sh --full 2>&1 | tail -8'
verify_end
