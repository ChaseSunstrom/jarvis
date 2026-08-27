#!/usr/bin/env bash
# M93 — Pick up where you left off: a conversation has a URL.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M93" "pick up where you left off"

check "the voice screen opens ?conversation=<id>, follows the open thread in the address bar, and carries the id on the page" python3 -c '
from pathlib import Path
page = Path("jarvis-web/src/routes/+page.svelte").read_text()
assert "params.get(\x27conversation\x27)" in page and "function syncUrl" in page
assert "data-conversation-id={openConversationId" in page
chat = Path("jarvis-web/src/lib/components/ChatPanel.svelte").read_text()
assert "data-conversation-id={conversationId" in chat
print("open, follow, carry")
'
check "the rig's browser transport names the thread, and the one-turn rule is lifted" python3 -c '
from pathlib import Path
assert "params.set(\x27conversation\x27, job.conversation)" in Path("testing/live/browser_turn.cjs").read_text()
assert "\"conversation\": conversation_id or \"\"" in Path("testing/live/transport.py").read_text()
tests = Path("testing/live/tests/test_rig.py").read_text()
assert "test_the_browser_transport_carries_the_thread" in tests
assert "test_a_browser_scenario_is_one_turn_until" not in tests
import yaml
s = yaml.safe_load(Path("testing/live/scenarios/interactions-thread-continuity.yaml").read_text())
assert "text-ui" in s["variants"] and len(s["turns"]) > 1
print("the thread rides in the job; thread-continuity runs through the console")
'
check "clients.md says so" bash -c 'grep -q "A conversation has a URL (M93)" jarvis-core/docs/clients.md'
check_pytest "the rig tests" 'python3 -m pytest testing/live/tests -q --timeout=120'
ensure_web_build
run_playwright "a link reopens a thread with its transcript; a link to an unstarted thread starts it under that id" e2e/conversation-link.spec.ts

check_sh "on the house, through the real console: two turns on one thread (text-ui)" \
    'LIVE_ONLY=interactions-thread-continuity bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'

verify_end
