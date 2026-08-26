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
check_sh "packaging pins compose and the example to one number in range" \
    'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "length_scale" 2>&1 | tail -1'
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
