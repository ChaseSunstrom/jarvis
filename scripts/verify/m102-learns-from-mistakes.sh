#!/usr/bin/env bash
# M102 — Jarvis learns from its own mistakes: the day's failures on the record,
# read once a night, a note and a card, and "what did you get wrong?" answered
# from the record.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M102" "learns from its own mistakes"

check "the review integration: a day log of guard and stop events, the traces' errors, one ask, a note and a card, a tool" python3 -c '
from pathlib import Path
r = Path("jarvis-core/jarvis/integrations/review/__init__.py").read_text()
for s in ("EVENT_GUARDED = \"jarvis_turn_guarded\"", "EVENT_STOPPED = \"jarvis_run_stopped\"", "def _trace_rows", "async def review", "name=\"what_went_wrong\"", "kind=\"review\""):
    assert s in r, s
assert "\"jarvis_turn_guarded\": \"guard\"" in Path("jarvis-core/jarvis/integrations/observability/__init__.py").read_text()
assert "\"jarvis_turn_guarded\"" in Path("jarvis-core/jarvis/llm/agent.py").read_text()
assert "\"jarvis_run_stopped\"" in Path("jarvis-core/jarvis/voice/pipeline.py").read_text()
assert "\"review\")" in Path("jarvis-core/jarvis/integrations/notifications/__init__.py").read_text()
assert "\nreview:\n  at:" in Path("jarvis-core/config/configuration.yaml").read_text()
assert "## `review:`" in Path("jarvis-core/docs/configuration.md").read_text() and "## review" in Path("jarvis-core/docs/features.md").read_text()
print("record, review, tool, kind, configured, documented")
'
use_venv
check_pytest "the review suite" 'cd jarvis-core && python3 -m pytest tests/test_review.py -q --timeout=120 --timeout-method=signal'
check_pytest "the guard and the stop still hold, and are on the record" 'cd jarvis-core && python3 -m pytest tests/test_llm.py tests/test_api.py tests/test_observability.py -q --timeout=120 --timeout-method=signal -k "claim or guard or stopped_at_the_server or trace"'
check "a scenario, gated on M102, with a stop the rig sends and a review it asks for" python3 -c '
import yaml
from pathlib import Path
s = yaml.safe_load(Path("testing/live/scenarios/self-review.yaml").read_text())
assert s["gated-on"] == "M102" and s["turns"][0]["stop_after"] and s["turns"][1]["review"] is True
print("self-review")
'
check_sh "on the house: a stopped run is what went wrong, the review says so, and Jarvis answers from the record" \
    'LIVE_ONLY=self-review timeout 900 bash scripts/verify/live_interaction.sh --full 2>&1 | grep -v onnxruntime | tail -4'
verify_end
