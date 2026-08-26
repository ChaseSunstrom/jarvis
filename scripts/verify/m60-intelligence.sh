#!/usr/bin/env bash
# M60 — Intelligence and speed.
#
# The operator's numbers: the chat model is fast (≈75 tok/s); the wait on a
# voice turn is recognising the audio, prefilling a large prompt into a 256k
# window, and starting synthesis. This gate measures the parts the repository
# can change and refuses to lower an eval to pass. Fails first: none of the
# mechanisms below exist yet.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M60" "intelligence and speed"

require_file jarvis-core/jarvis/llm/agent.py
require_file evals/test_routing.py

# --- the prompt, measured -----------------------------------------------------
check "the system prompt has a token budget, and a test that measures it" python3 -c '
from pathlib import Path
src = Path("jarvis-core/tests/test_llm.py").read_text()
assert "PROMPT_TOKEN_BUDGET" in src, "no test pins the system prompt to a token budget"
print("the prompt is measured against a budget")
'
check "the prompt prefix is cached across turns (cache_prompt on every model request)" python3 -c '
from pathlib import Path
import re
client = Path("jarvis-core/jarvis/llm/openai_compat.py").read_text()
assert "cache_prompt" in client, "the client never asks the server to keep the prompt prefix"
tests = Path("jarvis-core/tests/test_openai_compat.py").read_text() + Path("jarvis-core/tests/test_llm.py").read_text()
assert "cache_prompt" in tests, "nothing asserts the request carries cache_prompt"
print("prefix caching requested and pinned")
'
check "the stable part of the prompt comes first, the turn-varying part last" python3 -c '
from pathlib import Path
src = Path("jarvis-core/tests/test_llm.py").read_text()
assert "test_the_prompt_prefix_is_stable_across_turns" in src, "no test pins the prefix order"
print("prefix order pinned")
'

# --- speech, streamed --------------------------------------------------------
check "the first sentence is spoken before the model has finished" python3 -c '
from pathlib import Path
src = Path("jarvis-core/tests/test_voice.py").read_text() + Path("jarvis-core/tests/test_pipeline.py").read_text() if Path("jarvis-core/tests/test_pipeline.py").exists() else Path("jarvis-core/tests/test_voice.py").read_text()
assert "test_the_first_sentence_is_spoken_before_the_reply_is_finished" in src, "sentence-streamed speech is not pinned"
print("sentence streaming pinned")
'
check "whisper is sized to the CPU and the choice is written down" python3 -c '
from pathlib import Path
compose = Path("jarvis-core/docker-compose.yml").read_text()
assert "compute-type" in compose or "WHISPER_COMPUTE" in compose or "--compute-type" in compose, "whisper has no compute type set"
doc = Path("docs/TOOLING_DECISIONS.md").read_text()
assert "int8" in doc, "the whisper sizing decision is not in docs/TOOLING_DECISIONS.md"
print("whisper sized, decision recorded")
'

# --- small-model reliability --------------------------------------------------
check "tool calls can be grammar-constrained for a small model" python3 -c '
from pathlib import Path
src = Path("jarvis-core/jarvis/llm/openai_compat.py").read_text() + Path("jarvis-core/jarvis/llm/agent.py").read_text()
assert "response_format" in src or "json_schema" in src or "grammar" in src, "no constrained decoding path"
tests = Path("jarvis-core/tests/test_llm.py").read_text()
assert "test_a_constrained_tool_call_is_schema_shaped" in tests
print("constrained tool calls available and pinned")
'
check "the task planner batches read-only steps" python3 -c '
from pathlib import Path
src = Path("jarvis-core/tests/test_taskengine.py").read_text() if Path("jarvis-core/tests/test_taskengine.py").exists() else ""
import glob
src += "".join(Path(p).read_text() for p in glob.glob("jarvis-core/tests/test_task*.py"))
assert "test_read_only_steps_run_as_one_round" in src, "batching is not pinned"
print("read-only batching pinned")
'

# --- the evals, never lowered -------------------------------------------------
check "the core suite is green" bash -c 'cd jarvis-core && python3 -m pytest tests -q --timeout=120 --timeout-method=signal -x -p no:cacheprovider 2>&1 | tail -1 | grep -q " passed"'
check_sh "the routing table and its two mirrors agree (make eval-routing, offline)" \
    'cd evals && python3 -m pytest test_routing.py -q --timeout=600 2>&1 | tail -2'
check "the intelligence eval floors are what they were — never lowered to pass" python3 -c '
import sys; sys.path.insert(0, "evals/intelligence")
from run import FLOORS
pinned = {"context_retention": 0.75, "routing": 0.85, "reasoning": 0.60, "instructions": 0.80, "graceful_failure": 0.80}
assert FLOORS == pinned, f"the floors moved: {FLOORS}"
print("floors unchanged")
'
check_sh "the planner, the pipeline and the client suites" \
    'cd jarvis-core && python3 -m pytest tests/test_task_plan_batching.py tests/test_voice.py tests/test_llm.py tests/test_openai_compat.py -q --timeout=120 --timeout-method=signal -p no:cacheprovider 2>&1 | tail -1'
verify_end
