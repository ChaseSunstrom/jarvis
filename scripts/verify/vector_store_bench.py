#!/usr/bin/env python3
"""How big does the note store get before the JSON sidecar stops being enough?

M34 asks one question and refuses to accept a shrug: keep memory and notes in
`memory-vectors.json` with a cosine scan in pure Python, or promote them to a
vector database. Either answer is fine. An unexamined one is not — so this
measures the thing that would decide it.

    python3 scripts/verify/vector_store_bench.py --sizes 500,2000,10000

What it measures, at each size:

  build      seconds to embed and index N notes (once, ever, per note)
  query      milliseconds for one semantic search — the number a person waits
  memory     resident kilobytes of the vectors themselves
  file       bytes of the sidecar on disk

The embeddings come from the real service when one is running, because a
benchmark of retrieval speed over random floats is a benchmark of `math.sqrt`.
With `--synthetic` it uses deterministic pseudo-vectors instead, which measures
the scan honestly and says nothing about recall.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "jarvis-core"))

from jarvis.integrations.memory.vectors import (  # noqa: E402
    VectorIndex,
    normalise,
    pack,
    prefixes_for,
)

#: Sentences to build a store out of. Real English, because embedding random
#: characters produces vectors that cluster in a way real notes do not.
SUBJECTS = [
    "the boiler", "the spare key", "my daughter", "the coffee machine",
    "the car insurance", "the bins", "the wifi password", "the loft hatch",
    "the water stopcock", "the fuse box", "the garden gate", "the freezer",
]
PLACES = [
    "in the blue tin", "under the stairs", "in the utility cupboard",
    "on the shelf above the sink", "in the garage", "behind the sofa",
    "in the top drawer", "in the shed", "by the front door",
]
FACTS = [
    "was serviced in March", "needs doing before winter", "renews in September",
    "is due for a check", "was replaced last year", "goes out on Tuesdays",
]


def notes(count: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return [
        f"{rng.choice(SUBJECTS)} {rng.choice(rng.choice([PLACES, FACTS]))} "
        f"(note {index})"
        for index in range(count)
    ]


def embed_remote(url: str, model: str, texts: list[str], batch: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(texts), batch):
        chunk = texts[start : start + batch]
        request = urllib.request.Request(
            f"{url.rstrip('/')}/embeddings",
            data=json.dumps({"model": model, "input": chunk}).encode(),
            headers={"content-type": "application/json"},
        )
        payload = json.loads(urllib.request.urlopen(request, timeout=300).read())
        out.extend(
            row["embedding"] for row in sorted(payload["data"], key=lambda r: r["index"])
        )
    return out


def embed_synthetic(texts: list[str], dims: int = 384) -> list[list[float]]:
    """Deterministic pseudo-vectors. Measures the scan, not the model."""
    out = []
    for text in texts:
        rng = random.Random(hash(text) & 0xFFFFFFFF)
        out.append([rng.gauss(0.0, 1.0) for _ in range(dims)])
    return out


def bench(size: int, url: str, model: str, synthetic: bool) -> dict:
    texts = notes(size)
    document_prefix, query_prefix = prefixes_for(model)
    started = time.perf_counter()
    if synthetic:
        vectors = embed_synthetic(texts)
    else:
        vectors = embed_remote(url, model, [document_prefix + t for t in texts])
    embed_seconds = time.perf_counter() - started

    index = VectorIndex(model=model)
    started = time.perf_counter()
    for position, vector in enumerate(vectors):
        index._vectors[f"n{position}"] = normalise(vector)
    build_seconds = time.perf_counter() - started

    # The query: one embedding call plus a scan of everything. The scan is what
    # scales, so it is timed on its own.
    probe_text = query_prefix + "where do we keep the spare key"
    probe = normalise(
        (embed_synthetic([probe_text]) if synthetic else embed_remote(url, model, [probe_text]))[0]
    )
    timings = []
    for _ in range(7):
        started = time.perf_counter()
        scored = {
            entry_id: float(sum(x * y for x, y in zip(probe, vector)))
            for entry_id, vector in index._vectors.items()
        }
        top = max(scored.values()) if scored else 0.0
        timings.append((time.perf_counter() - started) * 1000)
    packed = sum(len(pack(vector)) for vector in list(index._vectors.values())[:64])
    file_bytes = int(packed / min(64, size) * size) if size else 0
    return {
        "entries": size,
        "embed_seconds": round(embed_seconds, 2),
        "index_seconds": round(build_seconds, 3),
        "query_ms_median": round(statistics.median(timings), 2),
        "query_ms_worst": round(max(timings), 2),
        "vector_kib": round(size * len(vectors[0]) * 4 / 1024, 1),
        "sidecar_kib": round(file_bytes / 1024, 1),
        "top_similarity": round(top, 3),
        "dims": len(vectors[0]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="500,2000,10000")
    parser.add_argument("--url", default="http://127.0.0.1:7997/v1")
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--synthetic", action="store_true",
                        help="skip the embedding server (measures the scan only)")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    synthetic = args.synthetic
    if not synthetic:
        try:
            urllib.request.urlopen(f"{args.url.rstrip('/v1')}/health", timeout=5).read()
        except Exception as err:  # noqa: BLE001 - say which, then measure what we can
            print(f"no embedding service at {args.url} ({err}); measuring the scan only")
            synthetic = True

    rows = []
    print(f"{'entries':>8} {'embed':>8} {'index':>8} {'query':>10} {'worst':>8} "
          f"{'vectors':>10} {'sidecar':>10}")
    for size in [int(s) for s in args.sizes.split(",") if s.strip()]:
        row = bench(size, args.url, args.model, synthetic)
        rows.append(row)
        print(
            f"{row['entries']:>8} {row['embed_seconds']:>7.1f}s {row['index_seconds']:>7.2f}s "
            f"{row['query_ms_median']:>9.2f}ms {row['query_ms_worst']:>7.2f}ms "
            f"{row['vector_kib']:>9.0f}K {row['sidecar_kib']:>9.0f}K"
        )
    payload = {"synthetic": synthetic, "model": args.model, "rows": rows}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
