#!/usr/bin/env bash
# M24 — the voice loopback rig: synthesise a user, deliver the audio through
# both real entry points, hear the answer back through the same Whisper the
# system uses, and measure what happened. Everything real: no fake model, no
# fake voice services, no phone.
source "$(dirname "$0")/lib.sh"
verify_begin "M24" "voice loopback rig"
use_venv
LIVE=testing/live

require_file "$LIVE/voice.py"
require_file "$LIVE/audio.py"
require_file "$LIVE/transport.py"
require_file "$LIVE/judge.py"
require_file "$LIVE/report.py"
require_file "$LIVE/runner.py"
require_file "$LIVE/browser_turn.cjs"
require_file "$LIVE/fetch_voice.py"

check "the user's voice is not Jarvis's" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live.voice import JARVIS_VOICE
from testing.live.fetch_voice import VOICE
assert VOICE != JARVIS_VOICE, "the user and Jarvis would be indistinguishable"
print(f"user={VOICE} jarvis={JARVIS_VOICE}")
'
check "the voice is fetched (60 MB, gitignored like the other models)" \
    python3 testing/live/fetch_voice.py --check
check_not "the voice is not committed" git ls-files --error-unmatch testing/live/voices/en_US-amy-low.onnx
check "the browser path uses a real microphone, not a stub" \
    grep -q 'use-file-for-fake-audio-capture' "$LIVE/browser_turn.cjs"
check_not "the browser path does not use the mock backend's ?e2e=1 shortcut" \
    grep -n "e2e=1" "$LIVE/browser_turn.cjs"
check "the API path streams audio on the run's own binary channel" \
    grep -q 'run_pipeline' "$LIVE/transport.py"
check "replies are transcribed back, not read off the screen" \
    grep -q 'hear_wav' "$LIVE/transport.py"
check "noise, silence and clipping are all available" python3 -c '
import sys; sys.path.insert(0, ".")
from testing.live import audio
assert audio.add_noise and audio.silence and audio.room_tone and audio.clip
print("snr/silence/room-tone/clip")
'
check "the judge is local-only" grep -q 'LLM_URL' "$LIVE/judge.py"
check_not "nothing here reaches a cloud model" \
    grep -rniE 'api\.openai\.com|api\.anthropic\.com|generativelanguage' "$LIVE"

check_sh "the rig's own unit tests" \
    'python3 -m pytest testing/live/tests -q --timeout=300 --timeout-method=signal 2>&1 | tail -2'

# The real services this rig cannot work without. A failure here names which
# one is down rather than reporting a scenario failure somewhere else.
check_sh "the voice services are up (whisper 10300, piper 10200, wake 10400)" '
python3 -c "
import asyncio, sys; sys.path.insert(0, \".\")
from testing.live.voice import services_are_up
up = asyncio.run(services_are_up())
missing = [k for k, v in up.items() if not v]
assert not missing, f\"down: {missing}\"
print(up)
"'
check_sh "one real turn, end to end, spoken and heard" '
set -a; . ./.env 2>/dev/null; set +a
LIVE_NO_BROWSER=1 LIVE_ONLY=house-light-on timeout 900 \
  python3 -m testing.live.runner --implemented-only --no-browser --only house-light-on \
  2>&1 | grep -v pthread_setaffinity | tail -4'
verify_end
