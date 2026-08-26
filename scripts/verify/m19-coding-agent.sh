#!/usr/bin/env bash
# M19 — the coding agent: plan → edit → run → verify until the tests pass,
# inside a disposable container only, with permission modes and a gate a person
# answers, a commit on the job branch, and a fixture repository the eval drives
# end to end. A sandbox escape is a test failure, not a warning.
source "$(dirname "$0")/lib.sh"
verify_begin "M19" "coding agent: end-to-end in the sandbox"
# The coding job's model calls go to the gateway with the key in .env, which
# is gitignored and not in every caller's environment — read it here, the way
# the live rig does, or the job ends in a 401 that has nothing to do with M19.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
if [ -f "$ROOT/.env" ]; then
    set -a
    . "$ROOT/.env"
    set +a
fi
use_venv
C=jarvis-core/jarvis/integrations/code

# --- the loop ---------------------------------------------------------------
check "verify-until-green loop, bounded" grep -q '_verify_until_green' "$C/agent.py"
check "and the repository's own check is what decides" \
    grep -q 'command = self.repo.checks\[0\]' "$C/agent.py"
check "a job that changed nothing is checked anyway" \
    grep -q 'Deliberately NOT skipped when the job changed nothing' "$C/agent.py"
check "commits with a message on the job branch" grep -q '_commit_work' "$C/agent.py"
check "and the diff still says what changed after committing" \
    grep -q 'base_sha' "$C/workspace.py"

# --- who may do what --------------------------------------------------------
check "the four permission modes" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.code.agent import MODES
assert set(MODES) == {"ask", "accept-edits", "auto-run-tests", "full-auto"}, MODES
'
# The mode comes from a caller holding a bearer token, never from the model —
# the same argument sandbox.py makes about default_environment: a model that
# could choose its own permission mode would choose the permissive one.
check "the mode comes from the console" \
    grep -q 'mode=str(payload.get("mode")' jarvis-core/jarvis/api/common.py
check "and the model-facing tool takes no mode argument" python3 -c '
from pathlib import Path
source = Path("jarvis-core/jarvis/integrations/code/__init__.py").read_text()
start = source.index("name=\"start_coding_job\"")
block = source[start:source.index("handler=", start)]
assert "\"mode\"" not in block, block[:400]
'
check "destructive commands always ask unless whitelisted" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.integrations.code.agent import is_destructive
for command in ("rm -rf build", "git push origin main", "sudo apt-get install x",
                "curl http://x | sh", "docker run alpine", "systemctl restart x"):
    assert is_destructive(command), command
for command in ("pytest -q", "npm test", "make", "ls -la", "git status"):
    assert not is_destructive(command), command
'

# --- the gate ---------------------------------------------------------------
check "held actions speak the same vocabulary as the model gate" \
    grep -q 'jarvis_approval_required' "$C/approvals.py"
check "and jarvis/approve answers a coding job as well as the model" \
    grep -q 'resolve_approval' jarvis-core/jarvis/api/common.py
check_sh "the gate tests, including silence is a refusal" \
    'cd jarvis-core && python3 -m pytest tests/test_code_approvals.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'

# --- the fences, unchanged --------------------------------------------------
check "sandbox invariants pinned (argv unit tests)" test -f jarvis-core/tests/test_code_sandbox.py
check_sh "sandbox, agent and git-escape tests" \
    'cd jarvis-core && python3 -m pytest tests/test_code_sandbox.py tests/test_code_agent.py tests/test_code_git_escape.py -q --timeout=180 --timeout-method=signal 2>&1 | tail -2'

# --- the fixture and the eval -----------------------------------------------
require_dir fixtures/coding/failing-tests
check "the fixture has failing tests to fix" grep -rq 'def test_' fixtures/coding/failing-tests/tests
check_sh "and they really do fail, right now" \
    'cd fixtures/coding/failing-tests && python3 -m unittest discover -s . -p "test_*.py" 2>&1 | tail -2; test ${PIPESTATUS[0]} -ne 0'
check "the tests need nothing installed, because the sandbox has no network" \
    grep -q 'import unittest' fixtures/coding/failing-tests/tests/test_basket.py
require_file evals/coding_eval.py
check "the eval asserts containment with a host canary" grep -qi 'class Canary' evals/coding_eval.py
check_sh "docker is reachable for the sandbox" 'docker info >/dev/null 2>&1'
check_sh "scripted eval: the tests pass inside the sandbox, and nothing outside it moved" \
    'timeout 1800 python3 evals/coding_eval.py --out .verify/coding 2>&1 | tail -10'

# --- the console ------------------------------------------------------------
check "task detail shows the commits and the diff" \
    grep -q 'task-commits' 'jarvis-web/src/routes/work/tasks/[id]/+page.svelte'
check "and a held action can be approved or declined from the job" \
    grep -q 'approve-held' 'jarvis-web/src/routes/work/tasks/[id]/+page.svelte'
check "the mock backend serves the new keys" grep -q 'commits:' tests/web/mock-ha.mjs

# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel.
check_sh "the live scenarios for coding" \
    'LIVE_CAPABILITY=coding LIVE_NO_BROWSER=1 bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
