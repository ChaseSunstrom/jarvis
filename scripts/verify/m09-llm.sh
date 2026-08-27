#!/usr/bin/env bash
# M09 — the local model is reached through one OpenAI-compatible endpoint
# (llama-swap) everywhere: jarvis-core (already), the orchestrator (still
# Ollama-native), the smoke script and the sensors; hermes-style tool-call
# recovery stays proven; a local-only guard refuses public model hosts.
source "$(dirname "$0")/lib.sh"
verify_begin "M09" "LLM: llama-swap OpenAI-compatible endpoint + hermes tool parser"
use_venv
LLM=jarvis-core/jarvis/llm

require_file "$LLM/openai_compat.py"
require_file "$LLM/toolcalls.py"
check "hermes <tool_call> recovery exists" grep -q '<tool_call>' "$LLM/toolcalls.py"
check "LLM_URL is the first-class model setting" grep -qE '!env_var LLM_URL' jarvis-core/config/configuration.yaml
check ".env.example leads with LLM_URL / LLM_MODEL" grep -qE '^LLM_URL=' jarvis-core/.env.example
check "local-only guard exists (llm.local_only)" grep -q 'local_only' jarvis-core/jarvis/integrations/llm/__init__.py
check "llama-swap is documented as the endpoint" grep -qi 'llama-swap' jarvis-core/docs/openai-compat.md
check "README names llama-swap" grep -qi 'llama-swap' README.md
check_not "no sensor polls Ollama's /api/ps any more" grep -n '/api/ps' jarvis-core/config/configuration.yaml
check "a model-server sensor reads the OpenAI-compatible surface" grep -qE '/v1/models|/running' jarvis-core/config/configuration.yaml
check "check-model-server.py recognises llama-swap" grep -qi 'llama-swap' scripts/check-model-server.py
check "e2e-smoke probes /v1/models, not /api/tags" grep -q '/v1/models' scripts/e2e-smoke.sh
check_not "e2e-smoke no longer assumes Ollama" grep -n '/api/tags' scripts/e2e-smoke.sh
check "orchestrator fan-out uses /v1/chat/completions" grep -q '/v1/chat/completions' jarvis-orchestrator/app/fanout.py
# Code, not prose: the modules explain what they used to do, and a check that
# cannot tell a comment from a call would forbid the explanation.
check_sh_not "orchestrator no longer calls Ollama-native /api/chat" \
    'python3 - <<'"'"'PY'"'"'
import ast, pathlib, sys
hits = []
for path in pathlib.Path("jarvis-orchestrator/app").rglob("*.py"):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "/api/chat" in node.value and node.col_offset >= 0:
                # A docstring is a statement of its own; a URL is not.
                hits.append(f"{path}:{node.lineno}")
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            if "/api/chat" in text:
                hits.append(f"{path}:{node.lineno}")
docstrings = set()
for path in pathlib.Path("jarvis-orchestrator/app").rglob("*.py"):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc and "/api/chat" in doc:
                docstrings.add(f"{path}:{node.body[0].lineno}")
real = [h for h in hits if h not in docstrings]
print("\n".join(real))
sys.exit(0 if real else 1)
PY'
check_sh_not "orchestrator no longer hardcodes the ollama/ model prefix" \
    'grep -rn "\"ollama/\|f\"ollama/\|'"'"'ollama/" jarvis-orchestrator/app'
check_pytest "llm client + tool-call tests" 'cd jarvis-core && python3 -m pytest tests/test_openai_compat.py tests/test_tool_call_recovery.py tests/test_narrated_tool_calls.py tests/test_llm.py -q --timeout=120 --timeout-method=signal'
check_pytest "local-only guard tests" 'cd jarvis-core && python3 -m pytest tests/test_llm_local_only.py -q --timeout=120 --timeout-method=signal'
check_pytest "orchestrator tests" 'python3 -m pytest jarvis-orchestrator/tests -q --timeout=120 --timeout-method=signal'
verify_end
