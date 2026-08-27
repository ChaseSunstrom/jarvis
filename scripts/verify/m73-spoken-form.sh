#!/usr/bin/env bash
# M73 — Said, not shown.
#
# "can you have it pronounce characters correctly? not just say what it says?"
# The reply the synthesiser gets is words: markdown and the symbols the model
# writes for a screen are expanded at the pipeline's one door to TTS
# (`speakable`), and the transcript keeps the reply as written.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M73" "said, not shown"

check "the spoken form lives in one module" test -f jarvis-core/jarvis/voice/speech_text.py
check "speakable() — the one door to the synthesiser — goes through it" grep -q "spoken_form(str(text" jarvis-core/jarvis/voice/pipeline.py
check "the operator's own line comes out as words" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.voice.pipeline import speakable
said = speakable("**Price:** ~$78,721, up 0.15% over 24 hours. Sentiment is neutral (49/100). A database shrank by 40 GB.")
assert said == "Price: about 78,721 dollars, up 0.15 percent over 24 hours. Sentiment is neutral (49 out of 100). A database shrank by 40 gigabytes.", said
print(said)
'
check "the transcript is untouched: response_text is what was written" grep -q "self.response_text\` keeps\|keeps the reply as written\|reply as written" jarvis-core/jarvis/voice/pipeline.py
check_pytest "the spoken-form suite" 'cd jarvis-core && python3 -m pytest tests/test_speech_text.py -q --timeout=60'
check_pytest "the voice suite still passes (speakable is on every path)" 'cd jarvis-core && python3 -m pytest tests/test_voice.py -q --timeout=120 --timeout-method=signal'

verify_end
