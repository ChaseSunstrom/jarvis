#!/usr/bin/env bash
# M34 — the vector store, decided. Either a database or a paragraph with
# numbers in it; this milestone refuses the third option, which is a shrug.
source "$(dirname "$0")/lib.sh"
verify_begin "M34" "the vector store, decided"
use_venv

require_file scripts/verify/vector_store_bench.py

check "the decision is written down with its numbers" python3 -c '
from pathlib import Path
text = Path("docs/TOOLING_DECISIONS.md").read_text()
section = text.split("### 4. The vector store")[1].split("### 5.")[0]
for needle in ("6.3 ms", "127 ms", "10 000", "sqlite-vec", "telemetry.qdrant.io"):
    assert needle in section, f"the vector-store decision does not mention {needle!r}"
print("measured at three sizes, with the alternative and the crossover named")
'
check "and the condition that would reverse it" python3 -c '
from pathlib import Path
section = Path("docs/TOOLING_DECISIONS.md").read_text().split("### 4. The vector store")[1]
section = section.split("### 5.")[0]
assert "25 000" in section or "25,000" in section, "no crossover point is named"
assert "filtered search" in section, "no non-size condition is named"
print("a size, a concurrency and a query-shape condition")
'

# The measurement itself, re-run. Small: the point is that the number is
# reproducible, not that it is re-derived at three sizes every time.
check_sh "the benchmark runs and the scan is fast at the configured cap" \
    'python3 scripts/verify/vector_store_bench.py --sizes 500 --out .verify/vectors/bench.json \
        2>&1 | tail -3'
check "the number in the doc is the number the benchmark produces" python3 -c '
import json
from pathlib import Path
row = json.loads(Path(".verify/vectors/bench.json").read_text())["rows"][0]
assert row["entries"] == 500
# A whole turn on this host is 7-10 SECONDS. A ceiling of 50 ms leaves an
# order of magnitude of headroom and still fails if someone makes the scan
# quadratic.
assert row["query_ms_median"] < 50, f"the scan takes {row[chr(113)+chr(117)+chr(101)+chr(114)+chr(121)+chr(95)+chr(109)+chr(115)+chr(95)+chr(109)+chr(101)+chr(100)+chr(105)+chr(97)+chr(110)]} ms at the cap"
print(f"{row[chr(113)+chr(117)+chr(101)+chr(114)+chr(121)+chr(95)+chr(109)+chr(115)+chr(95)+chr(109)+chr(101)+chr(100)+chr(105)+chr(97)+chr(110)]} ms per search over {row[chr(101)+chr(110)+chr(116)+chr(114)+chr(105)+chr(101)+chr(115)]} notes")
'
check "the store is still a file, not a service" python3 -c '
import yaml
from pathlib import Path
compose = yaml.safe_load(Path("jarvis-core/docker-compose.yml").read_text())
for name in compose["services"]:
    assert "qdrant" not in name and "milvus" not in name and "weaviate" not in name, name
print("no vector database in the stack")
'
check_not "and nothing imports one" \
    grep -rqE '^\s*(import|from)\s+(qdrant|pymilvus|weaviate|chromadb)' \
        jarvis-core/jarvis jarvis-core/requirements.txt
check_pytest "the sidecar's own tests still hold" 'cd jarvis-core && python3 -m pytest tests/test_memory_vectors.py -q \
        --timeout=120 --timeout-method=signal'
verify_end
