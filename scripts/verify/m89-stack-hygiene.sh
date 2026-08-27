#!/usr/bin/env bash
# M89 — Stack hygiene, from the services audit of 27 Aug 2026.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
verify_begin "M89" "stack hygiene"

check_pytest "packaging pins: the live sandbox, SearXNG's bind under granian, the console not root, the orchestrator's model server" 'cd jarvis-core && python3 -m pytest tests/test_packaging.py -q --timeout=120 -k "sandbox_is_pinned or searxng_binds or not_run_as_root or same_model_server"'
check "the docs say what is true: RUNBOOK names stack.py, DEVIATIONS §7 the root compose, the matrix the console's user" python3 -c '
from pathlib import Path
assert "testing/live/volumes.py" not in Path("docs/RUNBOOK.md").read_text()
assert "testing/live/stack.py" in Path("docs/RUNBOOK.md").read_text()
dev = Path("DEVIATIONS.md").read_text()
assert "commented out in `jarvis-core/docker-compose.yml`" not in dev and "--profile agents" in dev
sec = Path("docs/security.md").read_text()
assert "| jarvis-web | host (:8199), LAN/WG via ufw | node |" in sec
print("three documents agree with the tree")
'
check_sh "the egress audit is a verdict: PASS on the running sandbox" 'bash scripts/egress-audit.sh 2>&1 | tail -1 | grep -q "EGRESS AUDIT: PASS"'

# On the running stack, after the recreate.
check "on the stack, SearXNG listens on loopback only" bash -c '
inside=$(docker exec searxng sh -c "env | grep ^GRANIAN_HOST=" 2>/dev/null); echo "  $inside"
[ "$inside" = "GRANIAN_HOST=127.0.0.1" ] || { echo "the container was not recreated with GRANIAN_HOST"; exit 1; }
lan=$(ip -4 -o addr show scope global | awk "{print \$4}" | cut -d/ -f1 | head -1)
if [ -n "$lan" ]; then
  if curl -s -m 3 -o /dev/null -w "%{http_code}" "http://$lan:8888/healthz" | grep -q "^[0-9]"; then
    code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://$lan:8888/healthz")
    [ "$code" = "000" ] || { echo "SearXNG answers on the LAN address $lan ($code)"; exit 1; }
  fi
fi
curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/healthz | grep -q "^200"
'
check "on the stack, the console runs as node" bash -c 'u=$(docker exec jarvis-web id -un 2>/dev/null); echo "  uid: $u"; [ "$u" = "node" ]'

check_sh "the live smoke scenarios still pass" \
    'LIVE_ONLY=house-light-on,chat-context-retention,lock-needs-a-human bash scripts/verify/live_interaction.sh --implemented-only 2>&1 | tail -4'
verify_end
