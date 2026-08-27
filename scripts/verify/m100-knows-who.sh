#!/usr/bin/env bash
# M100 — Jarvis knows who it is talking to: memory per person, consolidated.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M100" "knows who it is talking to"

check "the archive's turns carry the speaker, a memory entry carries the person, the tools and extraction pass it" python3 -c '
from pathlib import Path
turn = Path("jarvis-core/jarvis/llm/memory.py").read_text()
assert "speaker: str = \"\"" in turn
mem = Path("jarvis-core/jarvis/integrations/memory/__init__.py").read_text()
assert "person: str = \"\"" in mem and "person=" in mem and "def speaker_of" in Path("jarvis-core/jarvis/api/devices.py").read_text()
agent = Path("jarvis-core/jarvis/llm/agent.py").read_text()
assert "remember_speaker(" in agent
print("Turn.speaker, MemoryEntry.person, speaker_of, remember_speaker")
'
check "recall prefers the speaker and labels another person; the reflection consolidates and attributes" python3 -c '
from pathlib import Path
mem = Path("jarvis-core/jarvis/integrations/memory/__init__.py").read_text()
assert "person: str | None = None" in mem.split("def get_context_block", 1)[1][:800]
refl = Path("jarvis-core/jarvis/integrations/memory/reflect.py").read_text()
assert "async def consolidate" in refl and "person" in refl
print("recall by person, consolidation")
'
check "the console shows the person and filters by it; the mock carries it" python3 -c '
from pathlib import Path
page = Path("jarvis-web/src/lib/sections/Memory.svelte").read_text()
assert "memory-person-" in page and "person" in page
assert "person:" in Path("tests/web/mock-ha.mjs").read_text().split("memoryEntries = [", 1)[1][:2000]
print("Memory page: person pill and filter; mock")
'
use_venv
check_pytest "the memory suite" 'cd jarvis-core && python3 -m pytest tests/test_memory.py -q --timeout=120 --timeout-method=signal'
check_pytest "the reflection suite" 'cd jarvis-core && python3 -m pytest tests/test_memory_reflection.py -q --timeout=120 --timeout-method=signal'
check_pytest "the agent remembers who spoke" 'cd jarvis-core && python3 -m pytest tests/test_llm.py -q --timeout=120 --timeout-method=signal -k "speaker"'
check "a scenario, gated on M100" python3 -c '
import yaml
from pathlib import Path
assert yaml.safe_load(Path("testing/live/scenarios/memory-per-person.yaml").read_text())["gated-on"] == "M100"
print("memory-per-person")
'
ensure_web_build
run_playwright "the Memory page: a person on the row, a filter by person" memory.spec.ts
check_sh "on the house: the rig enrols its voice as Rig, says a preference, and the entry carries the person" \
    'timeout 1200 bash scripts/verify/live_interaction.sh --full --only memory-per-person --no-browser 2>&1 | grep -v onnxruntime | tail -3'
verify_end
