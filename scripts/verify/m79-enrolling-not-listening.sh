#!/usr/bin/env bash
# M79 — Not listening while you enrol.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M79" "not listening while you enrol"

check "the speaker API marks an enrolment in progress" grep -q "def mark_enrolling" jarvis-core/jarvis/api/speaker.py
check "a sample and a test refresh the mark" bash -c '[ $(grep -c "    mark_enrolling(jarvis)" jarvis-core/jarvis/api/speaker.py) -ge 2 ]'
check "the heartbeat route sits on the token-gated router" grep -q '@api_router.post("/voice/speaker/enrolling")' jarvis-core/jarvis/api/rest.py
check "a pipeline turn inside the window yields and says why" grep -q '"enrolling": True' jarvis-core/jarvis/voice/pipeline.py
check "the console says recording-now before its microphone opens" grep -q "fetch('/api/voice/speaker/enrolling', { method: 'POST' })" jarvis-web/src/lib/components/EnrolVoice.svelte
check "the console relays it by the same rule as enrol" test -f jarvis-web/src/routes/api/voice/speaker/enrolling/+server.ts
check "the phone says recording-now before it opens the microphone" grep -q "Thread { client.enrolling() }.start()" android-app/app/src/main/kotlin/ai/jarvis/app/VoiceIdentityActivity.kt
check_pytest "the voice and speaker-gate suites" 'cd jarvis-core && python3 -m pytest tests/test_voice.py tests/test_speaker_gate.py -q --timeout=120 --timeout-method=signal -k "enrol"'
check_sh "the phone's mirror" 'python3 android-app/tools/enrolment_flow_test.py 2>&1 | tail -1'
check_sh "the console's server-side tests (the relay)" 'cd jarvis-web && npx vitest run src/lib/server 2>&1 | grep -E "Tests " | tail -1'

verify_end
