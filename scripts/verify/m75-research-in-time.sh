#!/usr/bin/env bash
# M75 — Research that reads in time.
#
# Two research runs at 22:3x on 26 Aug: every page read "timed out after 20s
# on /fetch", and every search paid the dead tailnet instance's timeout first.
# Now the search client asks the fallback first for ten minutes after a
# failure, jarvis-browser fetches a page as text before it renders it, and
# research reads a bounded few pages at once.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M75" "research that reads in time"

check "the search client remembers an instance that could not search" grep -q "_fallback_first_until" jarvis-core/jarvis/integrations/web/client.py
check "jarvis-browser fetches as text first" grep -q "def _plain_page" jarvis-browser/jarvis_browser/app.py
check "the plain fetch re-checks every redirect hop (no SSRF through the shortcut)" grep -q "get_with_checked_redirects(client, url, allowlist=s.lan_allowlist)" jarvis-browser/jarvis_browser/app.py
check "research reads a bounded few pages at once" grep -q "read_slots = asyncio.Semaphore" jarvis-core/jarvis/integrations/research/__init__.py
check "the config names the knob" grep -q "parallel_reads" jarvis-core/jarvis/integrations/research/__init__.py
check_sh "the web suite: fallback first after a failure, restored on an answer" \
    'cd jarvis-core && python3 -m pytest tests/test_web_integration.py -q --timeout=120 -k "fallback or remote or unresponsive" 2>&1 | tail -1'
check_sh "the browser suite: text first, JavaScript pages fall back, a LAN redirect is refused" \
    'python3 -m pytest jarvis-browser/tests -q --timeout=120 2>&1 | tail -1'
check_sh "the research suite" 'cd jarvis-core && python3 -m pytest tests/test_research.py -q --timeout=120 --timeout-method=signal 2>&1 | tail -1'
check "the running jarvis-browser reads a real news page as text in under ten seconds (rebuilt after M75)" bash -c '
TOKEN=$(grep "^JARVIS_BROWSER_TOKEN=" jarvis-core/.env | cut -d= -f2-)
START=$(date +%s.%N)
OUT=$(curl -s -m 30 -X POST http://127.0.0.1:8210/fetch -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" -d "{\"url\":\"https://news.bitcoin.com/\"}")
END=$(date +%s.%N)
python3 - "$OUT" "$START" "$END" <<'"'"'PY'"'"'
import json, sys
payload = json.loads(sys.argv[1]); took = float(sys.argv[3]) - float(sys.argv[2])
print(f"fetched={payload.get(\"fetched\", \"browser\")} status={payload.get(\"status\")} text={len(payload.get(\"text\", \"\"))} chars in {took:.1f}s")
assert payload.get("status") == 200 and len(payload.get("text", "")) > 500, payload.get("text", "")[:200]
assert took < 10, f"{took:.1f}s"
PY'

verify_end
