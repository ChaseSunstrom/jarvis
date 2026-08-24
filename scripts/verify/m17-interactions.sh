#!/usr/bin/env bash
# M17 — user interactions: threads that survive and resume, continuity across
# surfaces, proactive moments from hooks with a retrievable record, and a
# visible "why am I seeing this" trace.
source "$(dirname "$0")/lib.sh"
verify_begin "M17" "user interactions: threads, continuity, proactive moments"
use_venv
CORE=jarvis-core/jarvis

check "threads are searchable (jarvis/conversation/search)" grep -q '"jarvis/conversation/search"' "$CORE/api/websocket.py"
check "the archive keeps tool results" grep -qE 'result' "$CORE/llm/history.py"
require_file "$CORE/integrations/notifications/__init__.py"
check "a notification record is an event and a store" grep -qE 'jarvis_notification' "$CORE/integrations/notifications/__init__.py"
check "WS: jarvis/notifications/list" grep -q '"jarvis/notifications/list"' "$CORE/api/websocket.py"
check "task completion/failure produces a notification" grep -qE 'task_(completed|failed)' "$CORE/integrations/notifications/__init__.py"
check "the briefing is configurable from the console" grep -qE 'briefing' "$CORE/api/websocket.py"
check "the reply carries its memory trace" grep -q 'memory_used' "$CORE/llm/agent.py"
require_file jarvis-web/src/lib/components/Notifications.svelte
require_file jarvis-web/src/lib/components/Moment.svelte
check "the console shows why a reply used memory" grep -rqi 'memory_used\|why am I seeing' jarvis-web/src
check "Android renders the notification record" grep -rq 'jarvis_notification\|notifications/list' android-app/app/src/main/kotlin
check "mock backend serves notifications + conversation search" grep -q 'jarvis/notifications/' tests/web/mock-ha.mjs
require_file testing/e2e/test_threads.py
require_file testing/e2e/test_continuity.py
require_file jarvis-core/tests/test_notifications.py
check_sh "thread persistence (create → restart → resume with prior context) + continuity (two clients, one thread)" \
    'timeout 900 python3 -m pytest testing/e2e/test_threads.py testing/e2e/test_continuity.py -q --timeout=600 --timeout-method=signal 2>&1 | tail -3'
check_sh "proactive trigger (fire a hook → a notification record exists and is retrievable)" \
    'cd jarvis-core && python3 -m pytest tests/test_notifications.py tests/test_history.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -2'
require_file jarvis-web/e2e/moments.spec.ts
ensure_web_deps
ensure_web_build
run_playwright "proactive moments + thread search e2e" e2e/moments.spec.ts e2e/chat.spec.ts
verify_end
