#!/usr/bin/env bash
# M30 — the toolbelt contract: a decision per slot, checked against current
# sources, and a tape measure that can tell whether adding a service helped.
source "$(dirname "$0")/lib.sh"
verify_begin "M30" "the toolbelt contract and its measurements"
use_venv

require_file docs/TOOLING_DECISIONS.md
require_file scripts/verify/toolbelt_baseline.py

# Every slot M31–M37 proposes has to be answered here, or the milestone that
# builds it has no contract to build against.
check "every toolbelt slot has a decision written down" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text().lower()
slots = {
    "browser": "browser",
    "crawling/extraction": "extraction",
    "embeddings": "embedding",
    "reranking": "rerank",
    "vector store": "vector store",
    "speech": "speech",
    "observability": "observability",
    "n8n": "n8n",
}
missing = sorted(name for name, needle in slots.items() if needle not in text)
assert not missing, f"no decision for: {missing}"
print(f"{len(slots)} slots answered")
'

# A decision doc that only says yes is a shopping list. Each slot names what it
# turned down and why, and the rejections are the part that saves the RAM.
check "the rejected options are named, not only the chosen ones" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
for word in ("Rejected", "Decision, provisional"):
    assert word in text, f"no {word!r} in the decisions doc"
print("rejections and provisional decisions are recorded")
'

check "the VRAM rule is written, and says what it protects" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
assert "VRAM justification rule" in text
assert "KV cache" in text
assert "embeddings must not go through llama-swap" in text.lower() or \
       "embeddings must not go through llama-swap" in text
print("the VRAM rule names the KV cache and the embedding path")
'

# Sources, not memory: the doc says when it was checked and against what.
check "the decisions cite current sources with a date" python3 -c '
import re
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
assert re.search(r"Checked on 20\d\d-\d\d-\d\d", text), "no date on the sources"
links = re.findall(r"<https?://[^>]+>", text)
assert len(links) >= 6, f"only {len(links)} source link(s)"
print(f"{len(links)} sources, dated")
'

check_sh "the tape measure runs and reads the evals that exist" \
    'python3 scripts/verify/toolbelt_baseline.py 2>&1 | tail -3'
check_sh "it refuses to snapshot when an eval has not been run" \
    'out=$(python3 scripts/verify/toolbelt_baseline.py --out /dev/null 2>&1); \
     status=$?; \
     if [ -f .verify/live/scorecard.json ] && [ -f .verify/live/results.json ]; then \
        test $status -eq 0 || { echo "$out"; exit 1; }; echo "both evals present, snapshot written"; \
     else \
        test $status -ne 0 || { echo "it wrote a snapshot with a missing eval"; exit 1; }; \
        echo "refused, as it should"; \
     fi'
check_pytest "worse numbers exit non-zero, and noise does not" 'python3 -m pytest testing/tools -q --timeout=60 --timeout-method=signal'

# The comparison end to end, on two snapshots this check makes itself: the unit
# tests exercise `compare()`, and this exercises the command a person types.
#
# The output is captured BEFORE it is grepped. `check_sh` runs snippets under
# `pipefail`, and this command exits non-zero exactly when it has something to
# say — so `compare | grep WORSE` reports "no regression found" at the moment
# it finds one.
check_sh "a real before/after comparison names the regression and fails" '
set -e
work=$(mktemp -d)
trap "rm -rf $work" EXIT
cat > "$work/before.json" <<JSON
{"metrics": {"intelligence.routing": 1.0, "latency.idle_total": 9.0}, "gaps": [], "unknown": []}
JSON
cat > "$work/after.json" <<JSON
{"metrics": {"intelligence.routing": 0.75, "latency.idle_total": 9.5}, "gaps": [], "unknown": []}
JSON
set +e
said=$(python3 scripts/verify/toolbelt_baseline.py --compare "$work/before.json" "$work/after.json")
status=$?
set -e
echo "$said" | grep -q "WORSE: intelligence.routing" || {
    echo "a dropped rate was not reported: $said"; exit 1; }
echo "$said" | grep -q "WORSE: latency" && {
    echo "a 5% latency move was reported as a regression"; exit 1; }
test "$status" -ne 0 || { echo "it exited 0 on a regression"; exit 1; }
echo "the regression is named, the noise is not, and it exits $status"
'

# The scorecard this all hangs off has to exist, or the tape measure measures
# nothing. M26 writes it.
require_file .verify/live/scorecard.json
verify_end
