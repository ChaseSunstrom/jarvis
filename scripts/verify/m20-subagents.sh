#!/usr/bin/env bash
# M20 — subagents: drop-in markdown agent definitions, a model-call pool with a
# per-task concurrency limit and queue, context budgets, a live tree in the
# task UI, and a fixture task that provably needs two parallel subagents.
source "$(dirname "$0")/lib.sh"
verify_begin "M20" "subagents & orchestration"
use_venv
CORE=jarvis-core/jarvis

require_file "$CORE/agents/__init__.py"
check "markdown agent definitions are loaded (frontmatter: name, role, tools, model, budget)" grep -qE 'context_budget' "$CORE/agents/__init__.py"
for a in researcher coder verifier summarizer; do
    require_file "jarvis-core/config/agents/$a.md"
done
require_file "$CORE/llm/pool.py"
check "per-task concurrency limit + queue" grep -qiE 'Semaphore|queue' "$CORE/llm/pool.py"
check "config: llm.max_concurrent" grep -q 'max_concurrent' "$CORE/integrations/llm/__init__.py"
check "context budget enforced before the call" grep -qi 'budget' "$CORE/llm/pool.py"
check "subagents are child tasks with their own context" grep -qE 'child' "$CORE/tasks.py"
check "parent/child task events for the tree" grep -q 'jarvis_task_child_added' tests/contracts/task_events.json
check "the task UI renders the tree" grep -rqiE 'tree|children' "jarvis-web/src/routes/work/tasks/[id]/+page.svelte"
check "the orchestrator delegates through core" grep -qE 'agents|subagent' "$CORE/integrations/orchestrator/__init__.py"
require_file jarvis-core/tests/test_agents.py
require_file jarvis-core/tests/test_llm_pool.py
check_pytest "agents + pool unit tests" 'cd jarvis-core && python3 -m pytest tests/test_agents.py tests/test_llm_pool.py -q --timeout=120 --timeout-method=signal'
require_file evals/subagents_eval.py
check_sh "fixture task: two parallel subagents + roll-up, with log evidence of concurrency (harness, scripted model)" \
    'timeout 900 python3 evals/subagents_eval.py --out .verify/subagents 2>&1 | tail -6'
check "the roll-up artefact exists" test -f .verify/subagents/rollup.json
check "concurrency evidence recorded" grep -q 'overlap' .verify/subagents/rollup.json
# This milestone's own live scenarios. A capability is not done until it works
# when a person talks to it — which is a different claim from "its unit tests
# pass", and the only one an operator can feel. Its scenarios are gated on this
# milestone, so they run here for the first time.
check_sh "the live scenarios for subagents" \
    'LIVE_CAPABILITY=subagents bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'
verify_end
