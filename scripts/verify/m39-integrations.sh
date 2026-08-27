#!/usr/bin/env bash
# M39 — calendar, mail, and the tool-plugin interface they are the first two
# users of. Proved against a real CalDAV server and a real mailbox, because a
# client that passes against a mock of itself has proved nothing.
source "$(dirname "$0")/lib.sh"
verify_begin "M39" "calendar, mail, and a tool-plugin interface"
use_venv

require_file jarvis-core/jarvis/integrations/plugins/__init__.py
require_file jarvis-core/jarvis/integrations/calendar/__init__.py
require_file jarvis-core/jarvis/integrations/mail/__init__.py
require_file testing/fixtures/integrations_probe.py

check "both ship off, and mail ships writing to nobody" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
assert not cfg.get("calendar"), "a calendar ships configured"
assert not cfg.get("mail"), "a mailbox ships configured"
print("calendar and mail are both unconfigured until somebody points them somewhere")
'
check "neither integration adds a dependency" python3 -c '
from pathlib import Path
requirements = Path("jarvis-core/requirements.txt").read_text().lower()
for forbidden in ("caldav", "lxml", "icalendar", "imapclient"):
    assert forbidden not in requirements, f"{forbidden} was added"
print("CalDAV is httpx and xml.etree; mail is imaplib and smtplib")
'
check "a tool nobody classified needs a human" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from jarvis.integrations.plugins import PluginTool
from jarvis.llm.tools import TIER_APPROVAL
assert PluginTool("x", "", {}, lambda a: None).resolved_tier() == TIER_APPROVAL
print("the default is Tier 3, which is the safe direction")
'
check "credentials are read when the tool runs" \
    grep -q 'def secret' jarvis-core/jarvis/integrations/plugins/__init__.py
check "every plugin call lands in the trace" \
    grep -q 'EVENT_PLUGIN_CALL' jarvis-core/jarvis/integrations/plugins/__init__.py
check "an email body is quarantined and taints the turn" python3 -c '
from pathlib import Path
text = Path("jarvis-core/jarvis/integrations/mail/__init__.py").read_text()
assert "quarantine(message.body" in text, "a mail body reaches the model unwrapped"
assert "mark_untrusted(self.jarvis, context)" in text, "reading mail does not taint the turn"
print("an email is a web page with a stamp on it")
'
check "an address nobody allow-listed is refused rather than asked about" \
    grep -q 'is a prompt somebody clicks yes on' jarvis-core/jarvis/integrations/mail/__init__.py

check_pytest "the interface and both clients" 'cd jarvis-core && python3 -m pytest tests/test_integrations_plugins.py -q \
        --timeout=120 --timeout-method=signal'

# The three things the brief asks to be SHOWN. The fixtures are started here
# rather than assumed: a probe that silently skips is a probe that passes.
check_sh "the fixture calendar and mailbox are up" \
    'cd jarvis-core && timeout 600 docker compose --profile fixtures up -d --wait \
        jarvis-radicale jarvis-mailsink 2>&1 | tail -2'
check_sh "an event appears on a real calendar, a mail lands in a real inbox, and an unapproved write is refused" \
    'timeout 900 python3 testing/fixtures/integrations_probe.py 2>&1 | tail -8'
verify_end
