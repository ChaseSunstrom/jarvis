#!/usr/bin/env bash
# M78 — One utterance, one turn.
#
# "I asked it to set an alarm, why did it do it twice? and why did I hear
# jarvis twice": two listeners, one sentence, two turns. The second device
# bringing the same words inside the window yields; and `schedule` refuses an
# identical job made a moment ago.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M78" "one utterance, one turn"

check "the house keeps the last seconds of utterances by device" grep -q "class RecentListeners" jarvis-core/jarvis/api/devices.py
check "the pipeline yields before the model, the tools and the voice" grep -q "already_heard_from(text, me)" jarvis-core/jarvis/voice/pipeline.py
check "the surface is told which listener is answering" grep -q '"duplicate_of": other' jarvis-core/jarvis/voice/pipeline.py
check "the window is seconds" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.api.devices import RECENT_LISTENER_WINDOW
assert 2.0 <= RECENT_LISTENER_WINDOW <= 8.0; print(f"{RECENT_LISTENER_WINDOW:g}s")
'
check "schedule refuses an identical job made a moment ago" grep -q "already scheduled" jarvis-core/jarvis/integrations/schedule/__init__.py
check_pytest "the voice suite: the second listener yields, the same device may repeat itself" 'cd jarvis-core && python3 -m pytest tests/test_voice.py -q --timeout=120 --timeout-method=signal -k "listener"'
check_pytest "the schedule suite: the duplicate is refused and named" 'cd jarvis-core && python3 -m pytest tests/test_schedule.py -q --timeout=120 -k "duplicate or twice"'

verify_end
