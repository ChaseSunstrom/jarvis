#!/usr/bin/env bash
# M70 — A faster voice.
#
# "can you have jarvis speak slightly faster". Piper's pace is its length
# scale, taken at start, so the knob is the container's: PIPER_LENGTH_SCALE in
# compose, documented in .env.example, noted beside the voice in
# configuration.yaml, and pinned to one number by test_packaging. The proof it
# is still understood is the rig's WER, measured after the rebuild.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M70" "a faster voice"

check "compose passes Piper its length scale, defaulting to 0.9" grep -q -- '--length-scale ${PIPER_LENGTH_SCALE:-0.9}' jarvis-core/docker-compose.yml
check ".env.example documents the knob at the same number" grep -q '^PIPER_LENGTH_SCALE=0.9$' jarvis-core/.env.example
check "the config says where the knob is, beside the voice" grep -q 'PIPER_LENGTH_SCALE in .env' jarvis-core/config/configuration.yaml
check_pytest "packaging pins compose and the example to one number in range" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "length_scale"'
check "the settings registry has the pace, as a number applied on restart" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.settings import APPLY_RESTART, SETTINGS_BY_KEY
spec = SETTINGS_BY_KEY["voice.tts.length_scale"]; assert spec.type == "number" and spec.apply == APPLY_RESTART
assert "PIPER_LENGTH_SCALE" in spec.note; print(spec.label, "-", spec.note[:60])
'
check "the config reads the same variable into that key" grep -q 'length_scale: !env_var PIPER_LENGTH_SCALE' jarvis-core/config/configuration.yaml
check "the console plan puts Pace on Settings › Voice with the knob named" bash -c "grep -q \"key: 'voice.tts.length_scale'\" jarvis-web/src/lib/sections/settingsPlan.ts && grep -q 'PIPER_LENGTH_SCALE' jarvis-web/src/lib/sections/settingsPlan.ts"
check "the mock backend serves the row (or the console tests pass while the console breaks)" grep -q "key: 'voice.tts.length_scale'" tests/web/mock-ha.mjs
check_pytest "the settings suites pin it" 'cd jarvis-core && python3 -m pytest tests/test_settings.py tests/test_settings_api.py -q --timeout=120 -k "pace or settings"'
ensure_web_deps
ensure_web_build
check "Settings › Voice shows the pace row with the knob named (Playwright)" bash -c 'cd jarvis-web && E2E_PORT=${E2E_PORT:-8299} npx playwright test e2e/settings.spec.ts -g "voice pace" --reporter=line 2>&1 | tail -3 | grep -q " passed"'
check "the running Piper was started at that scale (rebuilt after M70)" bash -c \
    'docker inspect wyoming-piper --format "{{join .Args \" \"}}" 2>/dev/null | grep -q -- "--length-scale 0.9"'
check "the last smoke run understood it: WER mean under the threshold" python3 -c '
import json, pathlib
r = json.loads(pathlib.Path(".verify/live/results.json").read_text())
t = r["totals"]; n = int(t.get("wer_samples") or 0)
assert n > 0, "the last live run had no spoken turns, so nothing measured the pace — run the smoke set with its voice variants"
wer = float(t.get("wer_mean") or 0.0); assert wer <= 0.10, f"WER {wer} over {n} samples"
print(f"WER mean {wer:.3f} over {n} spoken samples")
'

verify_end
