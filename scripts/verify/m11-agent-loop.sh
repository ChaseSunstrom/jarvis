#!/usr/bin/env bash
# M11 — plan → act → verify: a multi-step request becomes a plan the UI can
# show, each step acts through tool calls, each outcome is verified, and a
# failed step is re-planned within a bound. Tier semantics are one shared
# contract instead of a comment that disagrees with the code.
source "$(dirname "$0")/lib.sh"
verify_begin "M11" "agent loop: plan → act → verify"
use_venv
CORE=jarvis-core/jarvis

require_file "$CORE/llm/plan.py"
check "plan phase" grep -qE 'def (plan|_plan|make_plan)' "$CORE/llm/plan.py"
check "verify phase" grep -qE 'def (verify|_verify)' "$CORE/llm/plan.py"
check "re-plan on a failed verification, bounded" grep -qiE 'replan|max_replans|MAX_REPLANS' "$CORE/llm/plan.py"
# Asked of agent.py, not plan.py: plan.py is deliberately pure — it builds
# prompts and parses answers and touches neither the registry nor the model —
# so the wiring that puts a plan's steps on a task lives in `plan_and_run`.
check "plans land on the task before they are acted on (the UI shows the steps)" \
    grep -qE 'add_steps=made\.titles' "$CORE/llm/agent.py"
check "the conversation agent uses the planner for multi-step requests" grep -q 'plan' "$CORE/llm/agent.py"
check "rounds stay bounded" grep -q 'max_tool_rounds' "$CORE/llm/agent.py"
require_file tests/contracts/tool_tiers.json
check "jarvis-core reads the tier contract" grep -rlq tool_tiers.json jarvis-core/tests
check "the web reads the tier contract" grep -rlq tool_tiers.json jarvis-web/src
check "the Android mirror reads the tier contract" grep -rlq tool_tiers.json android-app/tools
# Anchored to the legend line itself. Grepping the whole file for "confirm"
# also matched the note explaining that the old wording was wrong, so the check
# failed on its own fix.
check "the MCP tier legend says what tier 2 does" \
    grep -qE '^ *# *2 +run it, and say so' jarvis-core/config/configuration.yaml
check_not "no legend line promises a confirmation tier 2 has never done" \
    grep -nE '^ *# *2 +.*(confirm|ask first|approval)' jarvis-core/config/configuration.yaml
require_file jarvis-core/tests/test_agent_loop.py
for t in plan verif replan; do
    check "test_agent_loop.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_agent_loop.py
done
check_sh "agent loop unit tests" \
    'cd jarvis-core && python3 -m pytest tests/test_agent_loop.py tests/test_turn_events.py tests/test_llm_tools.py tests/test_gated_services.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_file testing/e2e/test_agent_loop.py
check_sh "agent loop e2e through the harness (real server, scripted model)" \
    'timeout 900 python3 -m pytest testing/e2e/test_agent_loop.py -q --timeout=600 --timeout-method=signal 2>&1 | tail -3'
check "documented" grep -qiE 'plan.*act.*verify' jarvis-core/docs/features.md
verify_end
