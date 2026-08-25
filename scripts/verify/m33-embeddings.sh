#!/usr/bin/env bash
# M33 — embeddings and reranking as services. Off the GPU, off llama-swap, and
# measured: the number this milestone exists to move is recall on queries that
# share no word with the note that answers them.
source "$(dirname "$0")/lib.sh"
verify_begin "M33" "embeddings and reranking as services"
use_venv

require_file jarvis-core/jarvis/llm/rerank.py

check "the embedding service is in the stack, pinned" \
    grep -q 'ghcr.io/huggingface/text-embeddings-inference:cpu-1.9' jarvis-core/docker-compose.yml
check "so is the reranker, from the same image" python3 -c '
import yaml
from pathlib import Path
compose = yaml.safe_load(Path("jarvis-core/docker-compose.yml").read_text())
services = compose["services"]
for name in ("jarvis-embeddings", "jarvis-reranker"):
    assert name in services, f"{name} is not in the stack"
assert services["jarvis-embeddings"]["image"] == services["jarvis-reranker"]["image"], \
    "two images where one would do"
print("two containers, one image")
'
# The whole point of the milestone: no GPU, and not through the model server.
check_not "neither service asks for a GPU" \
    grep -qE 'jarvis-(embeddings|reranker):.*(gpus|nvidia|runtime: nvidia)' jarvis-core/docker-compose.yml
check "embeddings do not go through the chat model server" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
embedding = str((cfg.get("memory") or {}).get("embedding_url") or "")
chat = str((cfg.get("llm") or {}).get("url") or "")
assert embedding, "memory has no embedding_url"
assert not chat or embedding.split("/v1")[0] != chat.split("/v1")[0], (
    f"embeddings still point at the chat server ({embedding}) — that is the "
    "KV-cache eviction the voice path pays for"
)
print(f"embeddings: {embedding}")
'
check "research reranks, and memory says why it does not" python3 -c '
import sys
sys.path.insert(0, "jarvis-core")
from pathlib import Path
from jarvis.config import load_yaml
text = Path("jarvis-core/config/configuration.yaml").read_text()
cfg = load_yaml(Path("jarvis-core/config/configuration.yaml"), Path("jarvis-core/config"), {})
assert (cfg.get("research") or {}).get("rerank_url"), "research does not rerank"
assert not (cfg.get("memory") or {}).get("rerank_url"), (
    "memory reranks — the measurement said it should not (6/6 became 5/6)"
)
assert "6/6" in text and "5/6" in text, "the numbers behind that choice are not written down"
print("research: on. memory: off, with the measurement beside it")
'

check_sh "the rerank client, and the per-model floors" \
    'cd jarvis-core && python3 -m pytest tests/test_rerank.py tests/test_memory_vectors.py -q \
        --timeout=120 --timeout-method=signal 2>&1 | tail -2'

# The running services. A container that is up and cannot answer is the M28
# lesson, so this asks them to do the actual job.
check_sh "the embedding service answers, and knows a paraphrase when it sees one" '
python3 - <<PY
import json, math, urllib.request

def embed(texts):
    request = urllib.request.Request(
        "http://127.0.0.1:7997/v1/embeddings",
        data=json.dumps({"model": "BAAI/bge-small-en-v1.5", "input": texts}).encode(),
        headers={"content-type": "application/json"},
    )
    payload = json.loads(urllib.request.urlopen(request, timeout=30).read())
    return [row["embedding"] for row in sorted(payload["data"], key=lambda r: r["index"])]

def unit(vector):
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector]

near, far = embed([
    "Represent this sentence for searching relevant passages: where do we keep the caffeine",
    "the good coffee is in the left cupboard",
])
score = sum(a * b for a, b in zip(unit(near), unit(far)))
assert score > 0.4, f"a plain paraphrase scored {score:.3f}"
print(f"caffeine/coffee similarity {score:.3f} — the query keyword search cannot answer")
PY'
check_sh "the reranker answers, and puts the right note first" '
python3 - <<PY
import json, urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:7998/rerank",
    data=json.dumps({
        "query": "where do we keep the caffeine",
        "texts": ["the boiler was serviced in March",
                  "the good coffee is in the left cupboard",
                  "Mira is seven"],
    }).encode(),
    headers={"content-type": "application/json"},
)
rows = json.loads(urllib.request.urlopen(request, timeout=30).read())
best = max(rows, key=lambda r: r["score"])["index"]
assert best == 1, f"the cross-encoder chose {best}"
print("the cross-encoder agrees")
PY'

# The number. Two harness runs, because a claim that recall improved needs the
# before as much as the after — and the before is what this repository shipped.
check_sh "keyword-only recall is the baseline it always was" \
    'timeout 900 python3 evals/memory_eval.py \
        --out .verify/memory/keyword 2>&1 | grep -E "^  (ok|FAIL)   recall" | tail -1'
check_sh "and with the services it goes up, measurably" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     EMBEDDINGS_URL=http://127.0.0.1:7997 timeout 900 python3 evals/memory_eval.py \
        --out .verify/memory/semantic 2>&1 | grep -E "^  (ok|FAIL)   recall" | tail -1'
check "the improvement is real, not a rounding" python3 -c '
import json
from pathlib import Path
before = json.loads(Path(".verify/memory/keyword/memory_eval.json").read_text())["recall"]
after = json.loads(Path(".verify/memory/semantic/memory_eval.json").read_text())["recall"]
print(f"recall@1 {before[chr(114)+chr(101)+chr(99)+chr(97)+chr(108)+chr(108)+chr(95)+chr(97)+chr(116)+chr(95)+chr(49)]:.0%} -> {after[chr(114)+chr(101)+chr(99)+chr(97)+chr(108)+chr(108)+chr(95)+chr(97)+chr(116)+chr(95)+chr(49)]:.0%}")
assert after["recall_at_1"] >= 0.8, f"recall@1 is only {after[chr(114)+chr(101)+chr(99)+chr(97)+chr(108)+chr(108)+chr(95)+chr(97)+chr(116)+chr(95)+chr(49)]:.0%}"
assert after["recall_at_1"] > before["recall_at_1"], "the services changed nothing"
'
check_sh "the research eval still passes with the reranker choosing pages" \
    'set -a; . ./.env 2>/dev/null; set +a; \
     RERANK_URL=http://127.0.0.1:7998 timeout 3000 python3 evals/research_eval.py \
        --backend fixture --out .verify/research 2>&1 | grep -v onnxruntime | tail -2'
verify_end
