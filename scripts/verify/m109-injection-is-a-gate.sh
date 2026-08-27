#!/usr/bin/env bash
# M109 — injection is a gate, not a prompt: what a hostile page can make the model
# do is decided in ToolRegistry, per tool, and the table below holds every tool to it.
set -u
cd "$(dirname "$0")/../.."
. scripts/verify/lib.sh
verify_begin "M109" "injection is a gate, not a prompt"
use_venv

check "outbound readers are named in the gate, and held on a tainted turn" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/llm/tools.py").read_text()
assert "OUTBOUND_READERS = frozenset" in src
for name in ("web_search", "web_fetch", "web_browse", "web_crawl", "read_page", "feed_latest"):
    assert f"\"{name}\"" in src.split("OUTBOUND_READERS = frozenset", 1)[1].split(")", 1)[0], name
assert "tool.name in OUTBOUND_READERS and self._is_tainted(context)" in src
print("six outbound readers, held when tainted")
'
check_pytest "the taint table: every registered tool, on a tainted turn, does what its kind says" 'cd jarvis-core && python3 -m pytest tests/test_taint_table.py -q --timeout=120 --timeout-method=signal'
check_pytest "the security suite: fenced content, refusers, the taint that outlives the turn" 'cd jarvis-core && python3 -m pytest tests/test_security.py -q --timeout=120 --timeout-method=signal -k "taint or inject or untrusted or exfil or refus"'
check "docs/injection.md says what is enforced where" python3 -c '
from pathlib import Path
doc = Path("docs/injection.md").read_text()
for phrase in ("OUTBOUND_READERS", "REFUSE_WHEN_TAINTED", "READ_ONLY_TOOLS", "untrusted_web_content", "not the prompt"):
    assert phrase in doc, phrase
print("the page names the four mechanisms")
'
check_sh "on the house: the four red-team scenarios" \
    'LIVE_ONLY=redteam-injection-via-page,redteam-injection-via-message,redteam-secret-exfiltration,redteam-cross-conversation-leak timeout 1500 bash scripts/verify/live_interaction.sh --full 2>&1 | grep -v onnxruntime | tail -4'
verify_end
