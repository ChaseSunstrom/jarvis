#!/usr/bin/env bash
# M95 — Jarvis reads its own record: recent_moments, explain_last_turn, and finished work that speaks.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M95" "Jarvis reads its own record"

check "two read-only tools from the record, and a completion that speaks" python3 -c '
from pathlib import Path
tools = Path("jarvis-core/jarvis/llm/tools.py").read_text()
assert "name=\"explain_last_turn\"" in tools and "\"explain_last_turn\", \"recent_moments\"," in tools
notes = Path("jarvis-core/jarvis/integrations/notifications/__init__.py").read_text()
assert "name=\"recent_moments\"" in notes and "speak_completions" in notes and "SPOKEN_KINDS" in notes
print("explain_last_turn, recent_moments, speak_completions")
'
check_pytest "the notifications suite: a finished job is announced through companion, and the inbox is a tool" 'cd jarvis-core && python3 -m pytest tests/test_notifications.py -q --timeout=120 --timeout-method=signal'
check_pytest "the agent suite: explain_last_turn names the tools of the previous turn from the archive" 'cd jarvis-core && python3 -m pytest tests/test_llm.py -q --timeout=120 --timeout-method=signal -k "explain_last_turn"'
check "two scenarios, gated on M95" python3 -c '
import yaml
from pathlib import Path
for n in ("explain-yourself", "what-did-you-tell-me"):
    s = yaml.safe_load(Path(f"testing/live/scenarios/{n}.yaml").read_text())
    assert s["gated-on"] == "M95" and len(s["turns"]) == 2, n
print("explain-yourself, what-did-you-tell-me")
'
check_sh "on the house: why did you say that, and what did you tell me" \
    'LIVE_ONLY=explain-yourself,what-did-you-tell-me bash scripts/verify/live_interaction.sh --full 2>&1 | tail -6'

verify_end
