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
check "plans are registered as tasks (the UI shows the steps)" grep -qE 'TaskRegistry|async_add|tasks\.' "$CORE/llm/plan.py"
check "the conversation agent uses the planner for multi-step requests" grep -q 'plan' "$CORE/llm/agent.py"
check "rounds stay bounded" grep -q 'max_tool_rounds' "$CORE/llm/agent.py"
require_file tests/contracts/tool_tiers.json
check "jarvis-core reads the tier contract" grep -rlq tool_tiers.json jarvis-core/tests
check "the web reads the tier contract" grep -rlq tool_tiers.json jarvis-web/src
check "the Android mirror reads the tier contract" grep -rlq tool_tiers.json android-app/tools
check_not "the MCP tier comment no longer contradicts the code" grep -n '2 = confirm' jarvis-core/config/configuration.yaml
require_file jarvis-core/tests/test_agent_loop.py
for t in plan verify replan; do
    check "test_agent_loop.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_agent_loop.py
done
check_sh "agent loop unit tests" \
    'cd jarvis-core && python3 -m pytest tests/test_agent_loop.py tests/test_turn_events.py tests/test_llm_tools.py tests/test_gated_services.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_file testing/e2e/test_agent_loop.py
check_sh "agent loop e2e through the harness (real server, scripted model)" \
    'timeout 900 python3 -m pytest testing/e2e/test_agent_loop.py -q --timeout=600 --timeout-method=signal 2>&1 | tail -3'
check "documented" grep -qiE 'plan.*act.*verify' jarvis-core/docs/features.md
verify_end
