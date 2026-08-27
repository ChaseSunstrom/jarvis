#!/usr/bin/env bash
# M82 — A coding job says when nobody can run it.
#
# "this just gets stuck in queued": the orchestrator had answered
# `status: error — opencode binary not installed` from the first poll, and the
# core's watcher read the wrapper's "ok" instead of the job's state, so the
# card said running · queued for the whole poll budget. And the binary was
# missing because the image lacked unzip and the installer's failure was
# swallowed.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M82" "a coding job says when nobody can run it"

O=jarvis-core/jarvis/integrations/orchestrator/__init__.py
check "the watcher reads the remote job's own state, and a wrapper error is an error" bash -c "grep -q 'status.get(\"job_status\")' $O && grep -q 'state = \"error\"' $O"
check "the orchestrator image unpacks OpenCode (unzip) and proves the binary at build time" bash -c 'grep -q "ca-certificates unzip" jarvis-orchestrator/Dockerfile && grep -q "opencode --version" jarvis-orchestrator/Dockerfile'
check_pytest "the orchestrator suite: a failed remote job fails the task within a poll" 'cd jarvis-core && python3 -m pytest tests/test_orchestrator.py -q --timeout=120 --timeout-method=signal'
check "the running orchestrator has OpenCode on its PATH (rebuilt after M82)" bash -c 'docker exec jarvis-orchestrator sh -c "opencode --version" 2>&1 | grep -E "^[0-9]+\.[0-9]+"'
check "the running orchestrator answers a code task without the missing-binary error" bash -c '
TOK=$(grep "^ORCHESTRATOR_TOKEN=" jarvis-core/.env | cut -d= -f2-)
OUT=$(curl -s -m 20 -X POST http://127.0.0.1:8188/code_task -H "Authorization: Bearer $TOK" -H "content-type: application/json" -d "{\"repo\":\"m82-probe\",\"instruction\":\"say hello\"}")
echo "$OUT" | head -c 240; echo
! echo "$OUT" | grep -q "opencode binary not installed"'

verify_end
