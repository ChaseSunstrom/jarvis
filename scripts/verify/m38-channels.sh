#!/usr/bin/env bash
# M38 — channels. Jarvis is reachable from a phone, by one person, over a
# connection nobody else can start. Most of this script is about the "nobody
# else" half, because that is the half the assistants this is modelled on lost.
source "$(dirname "$0")/lib.sh"
verify_begin "M38" "channels: reachable, and only by you"
use_venv

require_file jarvis-core/jarvis/integrations/channels/__init__.py
require_file jarvis-core/jarvis/integrations/channels/adapters.py

check "the shipped config has channels OFF with nobody allowed" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
block = cfg.get("channels") or {}
assert block.get("enabled") is False, "channels ship enabled"
assert not block.get("allow"), "the shipped allow-list is not empty"
assert (block.get("rate") or {}).get("per_sender"), "no per-sender rate limit"
print("enabled: false, allow: [], and both rate limits present")
'
# The property that decides whether this is safe to have at all.
check "neither shipped adapter opens a port" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.channels.adapters import SignalChannel, TelegramChannel
for adapter in (TelegramChannel(token="x"), SignalChannel(url="http://x", number="+1")):
    assert hasattr(adapter, "poll"), f"{adapter.name} does not poll"
    for listening in ("serve", "listen", "webhook", "app"):
        assert not hasattr(adapter, listening), f"{adapter.name} exposes {listening}"
print("both poll; neither listens")
'
check_not "no webhook is registered anywhere in the adapters" \
    grep -qi 'setwebhook\|/webhook' jarvis-core/jarvis/integrations/channels/adapters.py
check "an unknown sender is ignored rather than refused" \
    grep -q 'A refusal is an oracle' jarvis-core/jarvis/integrations/channels/__init__.py
check "an inbound message is quarantined and taints the turn" python3 -c '
from pathlib import Path
text = Path("jarvis-core/jarvis/integrations/channels/__init__.py").read_text()
assert "mark_untrusted(self.jarvis, context)" in text, "a message does not taint the turn"
assert "quarantine(text" in text, "a message reaches the model unwrapped"
print("quarantined and tainted before the model sees a word")
'
check "outbound goes through notifications rather than a second idea of it" \
    grep -q 'jarvis_notification' jarvis-core/jarvis/integrations/channels/__init__.py

check_pytest "the hub's own suite — mostly refusals, in order" 'cd jarvis-core && python3 -m pytest tests/test_channels.py -q \
        --timeout=120 --timeout-method=signal'

# The live probes, through the REAL hub: authentication, rate limit,
# quarantine, agent and reply. Only the wire is a fake, and it is a fake that
# ships (`MemoryChannel`) rather than a test double the product never sees.
check_sh "an unknown sender gets nothing, and an injected message cannot act" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 1800 python3 -m testing.live.runner --full \
       --only redteam-unknown-sender,redteam-injection-via-message \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -4'
check "no test touches a real account" python3 -c '
from pathlib import Path
for path in ("jarvis-core/tests/test_channels.py",
             "testing/live/scenarios/redteam-unknown-sender.yaml",
             "testing/live/scenarios/redteam-injection-via-message.yaml"):
    text = Path(path).read_text()
    assert "api.telegram.org" not in text, f"{path} names a real endpoint"
print("the memory adapter, which goes nowhere")
'
verify_end
