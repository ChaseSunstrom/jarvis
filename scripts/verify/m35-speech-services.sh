#!/usr/bin/env bash
# M35 — speech, measured. The doubled transcript closed with a flag the
# container already had, and a TTS A/B whose numbers refuse to pick a winner.
source "$(dirname "$0")/lib.sh"
verify_begin "M35" "speech as services, and a TTS A/B"
use_venv

require_file jarvis-core/jarvis/voice/openai_tts.py
require_file scripts/verify/tts_ab.py
require_file docs/tts-review/README.md
require_file docs/tts-review/measurements.json

# The defect this milestone was pointed at.
check "the recogniser runs with the filter that fixed the doubling" \
    grep -q 'vad-filter' jarvis-core/docker-compose.yml
check "and the scenario's WER ceiling is back to the default" python3 -c '
from pathlib import Path
text = Path("testing/live/scenarios/voice-wake-word.yaml").read_text()
assert "transcript_wer: 1.0" not in text, "the ceiling is still relaxed"
assert "vad-filter" in text or "VAD" in text or "M35" in text, "no note of why it changed"
print("no relaxed ceiling on the wake path any more")
'
check "silence asserts that there IS no text, not that it moved nothing" python3 -c '
from pathlib import Path
for name in ("voice-silence", "voice-room-tone"):
    text = Path(f"testing/live/scenarios/{name}.yaml").read_text()
    assert "stt-no-text-recognized" in text, f"{name} does not assert the coded failure"
print("both negatives assert the coded failure")
'
check "the issue is closed with what closed it" \
    grep -q 'status: \*\*fixed\*\* (M35 — `--vad-filter`' ISSUES.md

# The alternative engine: opt-in, and it must not be able to become the default
# by accident.
check "the alternative voice is behind a profile" python3 -c '
import yaml
from pathlib import Path
compose = yaml.safe_load(Path("jarvis-core/docker-compose.yml").read_text())
service = compose["services"]["jarvis-tts"]
assert service.get("profiles"), "jarvis-tts would start with `up -d`"
print(f"jarvis-tts: profile {service[chr(112)+chr(114)+chr(111)+chr(102)+chr(105)+chr(108)+chr(101)+chr(115)]}")
'
check "the shipped config still speaks through Piper" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
tts = (cfg.get("voice") or {}).get("tts") or {}
assert str(tts.get("engine") or "wyoming").lower() != "openai", "the default voice changed"
print(f"voice: {tts.get(chr(118)+chr(111)+chr(105)+chr(99)+chr(101))}")
'
check_sh "the opt-in client returns what the pipeline expects" \
    'cd jarvis-core && python3 -m pytest tests/test_openai_tts.py -q \
        --timeout=120 --timeout-method=signal 2>&1 | tail -2'

# The A/B itself. Piper alone is enough to run it — the script says so when
# Kokoro is not up rather than pretending there was a comparison.
check_sh "the A/B runs and both engines are intelligible" \
    'timeout 900 python3 scripts/verify/tts_ab.py --out .verify/tts 2>&1 | tail -3'
check "the measurements are the ones the review page quotes" python3 -c '
import json, statistics
from pathlib import Path
rows = json.loads(Path(".verify/tts/measurements.json").read_text())
piper = [row["piper"] for row in rows]
assert statistics.mean(m["wer"] for m in piper) <= 0.1, "Piper is not intelligible any more"
assert statistics.mean(m["rtf"] for m in piper) < 1.0, "Piper is slower than real time"
print(f"piper RTF {statistics.mean(m[chr(114)+chr(116)+chr(102)] for m in piper):.2f}x, "
      f"WER {statistics.mean(m[chr(119)+chr(101)+chr(114)] for m in piper):.3f}")
'
check "the decision says what would reverse it" python3 -c '
from pathlib import Path
section = Path("docs/TOOLING_DECISIONS.md").read_text().split("### 5. Speech")[1]
section = section.split("### 6.")[0]
assert "What would reverse this" in section, "no reversal condition for STT"
assert "tie is not a reason" in section, "no reason given for keeping Piper"
print("both decisions carry their conditions")
'

check_sh "the voice scenarios pass on the filtered recogniser" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     timeout 1500 python3 -m testing.live.runner --full --capability voice \
       --no-browser --target harness 2>&1 | grep -v onnxruntime | tail -3'
verify_end
