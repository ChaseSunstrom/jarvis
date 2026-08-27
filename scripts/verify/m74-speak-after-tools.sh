#!/usr/bin/env bash
# M74 — Speak after tools, sentence by sentence.
#
# "it took forever after it spit out text for piper to do the TTS": M60's
# early speech switched off for the turn once a tool ran. It resumes per
# segment after each tool call, the tail goes out as the last chunk before
# the whole-reply clip, and the rig records the first sentence's audio.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M74" "speak after tools"

P=jarvis-core/jarvis/voice/pipeline.py
check "a tool call opens a new segment instead of ending early speech" grep -q "_segment_reset = True" "$P"
check_not "the M60 kill switch on tools is gone" grep -q "or self._tools_ran:" "$P"
check "the tail is the last chunk, before the whole-reply clip" grep -q "await self._speak_tail(text)" "$P"
check "the remainder is found by text, never by an index into the stream" grep -q "def _unspoken_tail" "$P"
check "the rig records first_audio from the first tts-chunk" grep -q '"tts-chunk": "first_audio"' testing/live/transport.py
check_pytest "the voice suite: the after-tools case, the tail chunk, the M60 cases" 'cd jarvis-core && python3 -m pytest tests/test_voice.py -q --timeout=120 --timeout-method=signal -k "early or chunk or spoken or tool"'
# Its own run, so the check does not depend on whatever slice ran last: the
# quiet pass read a results file with no spoken research turn in it.
check_sh "on the house, the spoken briefing is asked for by voice" \
    'LIVE_ONLY=research-spoken-briefing timeout 1200 bash scripts/verify/live_interaction.sh --full 2>&1 | grep -v onnxruntime | tail -4'
check "on the house, a spoken research turn had its first audio before the whole clip" python3 -c '
import json, pathlib
r = json.loads(pathlib.Path(".verify/live/results-m74.json").read_text())
turns = [t for s in r["scenarios"] for t in (s.get("turns") or []) if t.get("variant") == "voice" and "research" in s.get("name", "")]
assert turns, "no spoken research turn in the last live run — run LIVE_ONLY=research-spoken-briefing"
lat = [t["latency"] for t in turns if t.get("latency", {}).get("first_audio") is not None]
seen = [t.get("latency") for t in turns]
assert lat, f"no first_audio recorded (a one-sentence reply has no chunk before its clip; the briefing has several): {seen}"
for l in lat:
    assert l["first_audio"] <= l["tts"], l
    fa, tt, tot = l["first_audio"], l["tts"], l["total"]
    print(f"first audio {fa:.2f}s, whole clip {tt:.2f}s, total {tot:.2f}s")
'

verify_end
