#!/usr/bin/env bash
# M66 — Ask and answer.
#
# The operator's reports of 26 Aug 20:21: when Jarvis asks something the voice
# says the reply AND the question; a held question expires after five minutes
# and the answer then fails with "unknown, expired or already-used approval
# request"; and an answer or a confirmation should be sayable. Three claims,
# each checked where it lives: the question's own clock and the lapse sentence
# in the tool registry; the single voice through the pipeline, the registry,
# the bridge, the companion wire and the phone; the spoken answer through the
# contract table, the agent, the harness and the console's held bar.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M66" "ask and answer"
use_venv || true

require_file jarvis-core/jarvis/llm/spoken_answers.py
require_file tests/contracts/spoken_answers.json
require_file jarvis-core/tests/test_spoken_answers.py
require_file jarvis-core/tests/test_ask_and_answer.py
require_file testing/e2e/test_ask_and_answer.py

# --- the clock -------------------------------------------------------------
check "a question waits on its own clock — thirty minutes, longer than an action's five" python3 -c '
import sys; sys.path.insert(0, "jarvis-core")
from jarvis.llm.tools import DEFAULT_APPROVAL_TTL, DEFAULT_QUESTION_TTL, ToolRegistry
assert DEFAULT_QUESTION_TTL == 1800.0, DEFAULT_QUESTION_TTL
assert DEFAULT_APPROVAL_TTL == 300.0, DEFAULT_APPROVAL_TTL
r = ToolRegistry(None, approval_ttl=60.0)
assert r.question_ttl == 1800.0, "question_ttl derived from approval_ttl"
print(f"question_ttl {DEFAULT_QUESTION_TTL:.0f} s, approval_ttl {DEFAULT_APPROVAL_TTL:.0f} s")
'
check "the shipped config and the example house carry question_ttl beside approval_ttl" bash -c 'grep -q "^  question_ttl: 1800" jarvis-core/config/configuration.yaml && grep -q "^  question_ttl: 1800" jarvis-core/config/examples/house/configuration.yaml'
require_grep "configuration.md documents the knob and why it is its own" '^\| `question_ttl` \|' jarvis-core/docs/configuration.md
check "a lapsed question is answered in words, with its clock, not the three-way guess" python3 -c '
import asyncio, sys, tempfile; sys.path.insert(0, "jarvis-core")
from jarvis.core import Jarvis
from jarvis.llm.tools import EVENT_APPROVAL_EXPIRED, ToolRegistry, register_builtin_tools
async def main():
    with tempfile.TemporaryDirectory() as d:
        jarvis = Jarvis(d); await jarvis.async_setup({})
        lapsed = []; jarvis.bus.listen(EVENT_APPROVAL_EXPIRED, lambda e: lapsed.append(e.data))
        r = ToolRegistry(jarvis, question_ttl=0.0); register_builtin_tools(r)
        held = await r.call("ask_user", {"question": "Which lamp?"}, None)
        late = await r.approve_request(held["request_id"], True, "the corner one")
        assert late["expired"] is True and late["status"] == "error", late
        assert late["error"] == "That question expired after 0 seconds; ask again and I\x27ll wait.", late
        assert lapsed and lapsed[0]["expired"] is True, "no jarvis_approval_expired"
        unknown = await r.approve_request("never-raised", True)
        assert unknown["error"] == "unknown, expired or already-used approval request", unknown
        await jarvis.async_stop()
        print(late["error"])
asyncio.run(main())
'

# --- the single voice ------------------------------------------------------
check "the pipeline tells the agent whether the reply is spoken" grep -q 'wants_spoken = "spoken" in params or takes_var_kw' jarvis-core/jarvis/voice/pipeline.py
check "the registry stamps spoken and the conversation on the request it holds" bash -c 'grep -q "conversation_id, spoken = self._turn_facts(context)" jarvis-core/jarvis/llm/tools.py && grep -q "remember_turn(self.jarvis, context, conversation.id, spoken)" jarvis-core/jarvis/llm/agent.py'
check "the bridge hands spoken to companion.ask, and the wire carries it" bash -c 'grep -q "\"spoken\": bool(data.get(\"spoken\"))" jarvis-core/jarvis/integrations/llm/__init__.py && grep -q "\"spoken\": self.spoken" jarvis-core/jarvis/integrations/companion/__init__.py'
check "the phone parses spoken, shows the card, and does not read it out" bash -c 'grep -q "val spoken: Boolean = false" android-app/app/src/main/kotlin/ai/jarvis/app/companion/CompanionMessage.kt && grep -q "if (!spoken) askAloud()" android-app/app/src/main/kotlin/ai/jarvis/app/companion/CompanionAskActivity.kt && grep -q "message.spoken && host != null && host.isForeground" android-app/app/src/main/kotlin/ai/jarvis/app/companion/CompanionMessageHandler.kt'
check_sh "the phone's mirrors agree (the protocol, and the question's voice)" 'python3 android-app/tools/presence_signals_test.py 2>&1 | tail -1 && python3 android-app/tools/prompt_reaches_the_user_test.py 2>&1 | tail -1'
require_grep "cross-device.md documents the field" '^`spoken` \(optional, default `false`\)' docs/cross-device.md

# --- the answer, said ------------------------------------------------------
check "the contract pins the operator's case, the refusals, and the taint boundary" python3 -c '
import json
c = json.load(open("tests/contracts/spoken_answers.json"))
by = {case["name"]: case for case in c["cases"]}
assert by["the operator\x27s case"]["expect"] == {"kind": "answer", "index": 0, "answer": "turn everything off"}
assert by["a yes with more attached is not a yes"]["expect"]["kind"] == "none"
assert by["two actions and a yes is ambiguous"]["expect"]["kind"] == "ambiguous"
assert by["a tainted action is never approved by voice"]["expect"]["kind"] == "tainted"
assert by["a tainted question is never answered by voice"]["expect"]["kind"] == "tainted"
assert by["free text is the answer, verbatim"]["expect"]["answer"] == "http://printer.lan"
print(len(c["cases"]), "cases;", len(c["affirmations"]), "affirmations,", len(c["denials"]), "denials")
'
check_sh "every case in the table decides as the table says" 'cd jarvis-core && python3 -m pytest tests/test_spoken_answers.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check "the agent asks the rules before the model, and never for a tainted request" bash -c 'grep -q "note, settled = await self._answer_pending(conversation.id, message, context, result, emit)" jarvis-core/jarvis/llm/agent.py && grep -q "if verdict.kind == KIND_TAINTED:" jarvis-core/jarvis/llm/agent.py'
check_sh "the registry, the bridge, the companion, the pipeline and the agent, in the core suite" 'cd jarvis-core && python3 -m pytest tests/test_ask_and_answer.py tests/test_ask_user.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check_sh "end to end through the real server: a question answered by the next turn, and an expired one told so" 'python3 -m pytest testing/e2e/test_ask_and_answer.py -q --timeout=300 --timeout-method=signal -k "question or expired" 2>&1 | tail -1'
require_grep "security.md states the rule and the taint boundary" '^## An answer can be said, and the taint boundary decides when it may not be' docs/security.md
require_grep "clients.md documents ttl, conversation_id, spoken and jarvis_approval_expired" 'jarvis_approval_expired' jarvis-core/docs/clients.md

# --- the banner ------------------------------------------------------------
check "the held bar keeps a lapsed card, reads the server clock, and shows minutes" bash -c 'grep -q "lapsed-clear" jarvis-web/src/lib/components/Approvals.svelte && grep -q "function clockText" jarvis-web/src/lib/components/Approvals.svelte && grep -q "jarvis_approval_expired" jarvis-web/src/lib/components/Approvals.svelte'
check "the mock answers a late press with the server's sentence" grep -q "function expiredSentence" tests/web/mock-ha.mjs
ensure_web_deps
ensure_web_build
run_playwright "a question that lapses says so instead of vanishing, in a browser" 'e2e.spec.ts -g "lapsed|ask a question"'

verify_end
