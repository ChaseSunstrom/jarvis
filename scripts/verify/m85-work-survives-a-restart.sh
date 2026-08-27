#!/usr/bin/env bash
# M85 — Work survives a restart.
#
# Four background tasks on the house ended "interrupted when Jarvis restarted"
# on 27 Aug 2026 with nothing to pick them up: the engine's `register_kind`
# had no caller, and background work was never submitted as idempotent. Now
# a background job's factory is registered, the job is idempotent by design
# (reads repeat; actions are gated by their tools), the engine puts a job it
# still has back to queued after a restart, the worker plans only what is
# left, and the completion says it was picked back up.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M85" "work survives a restart"

check "the background worker's factory is registered with the engine, and the job is idempotent" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/llm/tools.py").read_text()
assert "_engine.register_kind(" in src and "\"background\"" in src
assert "idempotent=True" in src
print("register_kind(background), idempotent=True")
'
check "the engine puts a resumable job back to queued after a restart, and the task says so" python3 -c '
from pathlib import Path
eng = Path("jarvis-core/jarvis/taskengine.py").read_text()
assert "picked back up after a restart" in eng and "RESTART_ERROR" in eng
tasks = Path("jarvis-core/jarvis/tasks.py").read_text()
assert "resumed: bool = False" in tasks and "\"resumed\": self.resumed" in tasks
note = Path("jarvis-core/jarvis/integrations/notifications/__init__.py").read_text()
assert "picked back up after a restart" in note
print("engine, task record and completion agree")
'
check_pytest "the engine suite: a resumable job is picked back up, a non-idempotent one stays errored" 'cd jarvis-core && python3 -m pytest tests/test_taskengine.py tests/test_tasks.py -q --timeout=120 --timeout-method=signal'
check "the scenario restarts the house between the two turns and expects the completion to say so" python3 -c '
import yaml
from pathlib import Path
s = yaml.safe_load(Path("testing/live/scenarios/task-survives-a-restart.yaml").read_text())
assert s["gated-on"] == "M85" and s["turns"][1]["restart"] is True
assert s["turns"][1]["expect"]["notification"]["title_contains"] == "picked back up"
print(s["name"])
'
check_sh "on the house, a background job survives a real restart and says it was picked back up" \
    'LIVE_ONLY=task-survives-a-restart bash scripts/verify/live_interaction.sh --full 2>&1 | tail -5'

verify_end
