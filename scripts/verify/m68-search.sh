#!/usr/bin/env bash
# M68 — Search that answers.
#
# "FAILED … nothing was found for 4 searches": the operator's SEARXNG_URL names
# a tailnet instance whose every engine times out, and an empty answer was read
# as no results. Now the client tells "could not search" from "nothing matched",
# asks the stack's own instance after an instance that could not, says which one
# answered, and research names the cause. Never a cloud engine.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M68" "search that answers"

C=jarvis-core/jarvis/integrations/web/client.py
R=jarvis-core/jarvis/integrations/research/__init__.py
check "the web config knows a second SearXNG, derived from the first" grep -q 'searxng_fallback_url' "$C"
check "an instance with every engine unresponsive is 'answered nothing', not 'no results'" grep -q 'answered nothing' "$C"
check "the engines and their reasons are read from SearXNG's own list" grep -q 'unresponsive_engines' "$C"
check "the result says which instance answered and what happened first" grep -q '"instance": answer.instance' jarvis-core/jarvis/integrations/web/__init__.py
check "a research step shows the note beside its count" grep -q 'notes\[0\]' "$R"
check_not "research no longer says 'nothing was found' for a search that could not run" grep -q 'nothing was found for' "$R"
check_not "no cloud search engine anywhere in the integration (M18's rule, kept)" grep -rniE 'duckduckgo\.com|bing\.com|google\.com/search|serpapi|brave\.com/api' jarvis-core/jarvis/integrations/web
check "the config documents the knob" grep -q 'searxng_fallback_url' jarvis-core/config/configuration.yaml
check "the docs say what the one fallback is" grep -q 'The one fallback there is' jarvis-core/docs/search.md
check_pytest "the web suite, with the eight second-instance tests" 'cd jarvis-core && python3 -m pytest tests/test_web_integration.py -q --timeout=120'

# Live: the branch's client against the house's real instances — the one in
# jarvis-core/.env (read, never written) and the stack's own. The house is the
# operator's, so what the first one does is reported; what the gate demands is
# that a search answers, and says from where.
check "a search with the house's SEARXNG_URL answers with results (either instance)" bash -c '
cd jarvis-core && SEARXNG_URL="$(grep "^SEARXNG_URL=" .env 2>/dev/null | head -1 | cut -d= -f2-)" python3 - <<'"'"'PY'"'"'
import asyncio, os, sys
sys.path.insert(0, ".")
import httpx
from jarvis.integrations.web.client import SearxngClient, WebConfig
cfg = WebConfig.from_config({"searxng_url": os.environ.get("SEARXNG_URL") or "http://127.0.0.1:8888", "timeout": 20})
async def main():
    async with httpx.AsyncClient() as client:
        answer = await SearxngClient(cfg, client).search_answer("bitcoin news this week", 5)
    print(f"instances: {cfg.search_instances}")
    for note in answer.notes: print(f"note: {note}")
    print(f"{answer.instance} answered {len(answer.results)} result(s)")
    assert answer.results, "no result from either instance"
asyncio.run(main())
PY'

verify_end
