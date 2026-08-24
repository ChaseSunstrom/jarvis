#!/usr/bin/env bash
# M19 — the coding agent: plan → edit → run → verify until tests pass, inside a
# disposable container only, with approval gates and permission modes, commits
# on the job branch, and a fixture repo the harness drives — a sandbox escape is
# a test failure, not a warning.
source "$(dirname "$0")/lib.sh"
verify_begin "M19" "coding agent: end-to-end in the sandbox"
use_venv
C=jarvis-core/jarvis/integrations/code

check "verify-until-green loop (run_tests as a check that continues the loop)" grep -qE 'run_tests' "$C/agent.py"
check "commits with messages on the job branch" grep -qE 'git.*commit|def commit' "$C/workspace.py"
check "permission modes per task" grep -qE 'accept-edits|full-auto' "$C/__init__.py"
check "destructive commands always ask unless whitelisted" grep -qiE 'destructive|whitelist' "$C/agent.py"
check "edits and commands surface as approval_required" grep -qE 'approval_required' "$C/agent.py"
check "sandbox invariants pinned (argv unit tests)" test -f jarvis-core/tests/test_code_sandbox.py
check_sh "sandbox + agent unit tests" 'cd jarvis-core && python3 -m pytest tests/test_code_sandbox.py tests/test_code_agent.py tests/test_code_git_escape.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_dir fixtures/coding/failing-tests
check "the fixture repo has a failing test to fix" grep -rqE 'def test_|it\(' fixtures/coding/failing-tests
require_file evals/coding_eval.py
check "the eval asserts containment (host canary)" grep -qiE 'canary|containment' evals/coding_eval.py
check_sh "docker is reachable for the sandbox (prerequisite on this host: docker group / rootless docker)" 'docker info >/dev/null 2>&1'
check_sh "scripted eval: the agent makes the fixture's tests pass inside the sandbox; nothing written outside it" \
    'timeout 1800 python3 evals/coding_eval.py --out .verify/coding 2>&1 | tail -8'
check "task detail shows commits and diffs" grep -rqiE 'commit' jarvis-web/src/routes/tasks
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for coding" \
    'LIVE_CAPABILITY=coding bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
