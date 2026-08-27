#!/usr/bin/env bash
# M10 — a task engine, not a task record: a queue with a concurrency cap,
# workers that actually run background work, retries with backoff, cooperative
# cancel, recovery across restarts, and history that survives a restart.
source "$(dirname "$0")/lib.sh"
verify_begin "M10" "task engine: queue, scheduled/recurring, background, retries, persistent history"
use_venv
CORE=jarvis-core/jarvis

require_file "$CORE/taskengine.py"
check "TaskEngine class" grep -q 'class TaskEngine' "$CORE/taskengine.py"
check "a bounded FIFO queue with a concurrency cap" grep -qiE 'max_concurrent|concurrency' "$CORE/taskengine.py"
check "retries with backoff" grep -qi 'backoff' "$CORE/taskengine.py"
check "cooperative cancel reaches the worker" grep -q 'raise_if_cancelled' "$CORE/taskengine.py"
check "queued work is persisted and resumed after a restart" grep -qiE 'resume|recover' "$CORE/taskengine.py"
check "run_background_task really runs work (through the engine)" grep -q 'TaskEngine\|taskengine' "$CORE/llm/tools.py"
check "scheduled jobs go through the engine" grep -q 'TaskEngine\|taskengine' "$CORE/integrations/schedule/__init__.py"
# The cap stayed (a store is not unbounded either); what changed is that the
# runs are written down. The check is that they survive, not that a constant
# was deleted.
check "code job results are written down, not memory-only" \
    grep -qE 'async def async_load_results' "$CORE/integrations/code/__init__.py"
check "…and are loaded again at startup" grep -qE 'await async_load_results' "$CORE/integrations/code/__init__.py"
check "orchestrator jobs are reloaded after a restart" grep -q 'load_persisted()' jarvis-orchestrator/app/main.py
check "WS/REST can retry a failed task" grep -q '"jarvis/tasks/retry"' "$CORE/api/websocket.py"
check "task engine documented" test -f jarvis-core/docs/tasks.md
require_file jarvis-core/tests/test_taskengine.py
for t in queue retry backoff restart cancel; do
    check "test_taskengine.py covers: $t" grep -qE "def test_[a-z_]*$t" jarvis-core/tests/test_taskengine.py
done
check_pytest "task engine + schedule + tasks tests" 'cd jarvis-core && python3 -m pytest tests/test_taskengine.py tests/test_tasks.py tests/test_schedule.py tests/test_schedule_plan.py -q --timeout=120 --timeout-method=signal'
verify_end
