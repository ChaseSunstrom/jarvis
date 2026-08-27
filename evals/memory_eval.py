#!/usr/bin/env python3
"""Memory, end to end, against a real jarvis-core: does it actually keep?

The store has unit tests. What they cannot prove is the claim a person cares
about — that a fact told to Jarvis on Tuesday is still there on Wednesday,
after the process died and came back. A memory that lives in a process is not a
memory, and the only honest way to test that is to kill the process.

    store → RESTART → retrieve → forget → export → wipe

Every step is asserted against the running server through its API, and the exit
code is the result. Uses the harness, so it boots its own jarvis-core with its
own throwaway config and needs nothing running beforehand.

    python3 evals/memory_eval.py --out .verify/memory
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.harness import Harness, JarvisClient  # noqa: E402

#: What the eval remembers, and what it then asks for. The queries deliberately
#: do not repeat the note's words: retrieval that only works when you quote the
#: note back is a lookup table, not a memory.
FACTS = [
    ("The spare key is in the blue tin on the shelf.", ["house"], "where do we keep the spare key"),
    ("Mira is my daughter and she is seven.", ["people"], "how old is my daughter"),
    ("I take my coffee black, no sugar.", ["preferences"], "how do I like my coffee"),
    ("The boiler was serviced in March.", ["house"], "when was the boiler last looked at"),
]

#: The one that gets forgotten, by text.
FORGET_QUERY = "coffee"

#: The recall set (M33): queries that share NO content word with the note that
#: answers them. This is the gap `vectors.py` was written for — "where do we
#: keep the caffeine" against "the good coffee is in the left cupboard" — and
#: the number it produces is what says whether an embedding server and a
#: cross-encoder were worth adding.
#:
#: Keyword search scores near zero on these by construction. That is the point:
#: a baseline that the change cannot move is not a baseline.
RECALL = [
    ("where do we keep the caffeine", "I take my coffee black, no sugar."),
    ("what age is my child", "Mira is my daughter and she is seven."),
    ("who lets themselves in when I am away", "The spare key is in the blue tin on the shelf."),
    ("when did the heating engineer last visit", "The boiler was serviced in March."),
    ("is the hot water system due a check", "The boiler was serviced in March."),
    ("what should I not put in a drink for you", "I take my coffee black, no sugar."),
]


class Failed(Exception):
    """One step of the eval did not hold."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


async def run(harness: Harness, out: Path) -> dict:
    steps: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  — ' + detail if detail else ''}", flush=True)

    client = JarvisClient(harness.base_url, harness.token)
    await client.connect()
    try:
        # --- store ---------------------------------------------------------
        for text, tags, _query in FACTS:
            answer = await client.call_service_rest(
                "memory", "add", {"text": text, "tags": tags}, return_response=True
            )
            check(
                bool((answer.get("service_response") or answer).get("stored")),
                f"the store refused {text!r}: {answer}",
            )
        listing = await client.command("jarvis/memory/list")
        check(
            listing["total"] == len(FACTS),
            f"stored {len(FACTS)} notes, the server has {listing['total']}",
        )
        record("store", True, f"{listing['total']} notes")

        # --- restart -------------------------------------------------------
        await client.aclose()
        harness.restart_core()
        client = JarvisClient(harness.base_url, harness.token)
        await client.connect()
        listing = await client.command("jarvis/memory/list")
        check(
            listing["total"] == len(FACTS),
            f"after the restart the server has {listing['total']} of {len(FACTS)} notes",
        )
        record("restart", True, "every note survived the process dying")

        # --- retrieve ------------------------------------------------------
        for text, _tags, query in FACTS:
            found = await client.command("jarvis/memory/list", query=query)
            texts = [entry["text"] for entry in found["entries"]]
            check(
                bool(texts) and texts[0] == text,
                f"{query!r} returned {texts[:2]} — expected {text!r} first",
            )
        record("retrieve", True, f"{len(FACTS)} targeted queries, right note first")

        # --- recall, as a number -------------------------------------------
        #
        # Not pass/fail: a measurement, reported, so that adding a service can
        # be shown to have helped rather than asserted to have. `PROCESS.md`
        # §2c is the rule this exists to satisfy.
        hits_at_1 = hits_at_3 = 0
        misses: list[str] = []
        for query, expected in RECALL:
            found = await client.command("jarvis/memory/list", query=query, limit=3)
            texts = [entry["text"] for entry in found["entries"]]
            if texts[:1] == [expected]:
                hits_at_1 += 1
            if expected in texts[:3]:
                hits_at_3 += 1
            else:
                misses.append(f"{query!r} -> {texts[:1] or ['nothing']}")
        recall = {
            "queries": len(RECALL),
            "recall_at_1": round(hits_at_1 / len(RECALL), 3),
            "recall_at_3": round(hits_at_3 / len(RECALL), 3),
            "misses": misses,
        }
        record(
            "recall",
            True,
            f"recall@1 {recall['recall_at_1']:.0%}, recall@3 {recall['recall_at_3']:.0%} "
            f"over {len(RECALL)} paraphrase queries",
        )
        for miss in misses:
            print(f"       · missed {miss}", flush=True)

        # --- forget --------------------------------------------------------
        coffee = [
            entry
            for entry in (await client.command("jarvis/memory/list"))["entries"]
            if FORGET_QUERY in entry["text"].lower()
        ]
        check(len(coffee) == 1, f"expected one note about {FORGET_QUERY}, found {len(coffee)}")
        await client.command("jarvis/memory/forget", entry_id=coffee[0]["id"])
        after = await client.command("jarvis/memory/list", query="how do I like my coffee")
        check(
            all(FORGET_QUERY not in entry["text"].lower() for entry in after["entries"]),
            "the forgotten note still surfaces in a search for it",
        )
        check(
            (await client.command("jarvis/memory/list"))["total"] == len(FACTS) - 1,
            "the count did not go down",
        )
        record("forget", True, "gone from the store and from retrieval")

        # --- export --------------------------------------------------------
        exported = await client.get_json("/api/memory/export")
        check(
            exported["count"] == len(FACTS) - 1,
            f"the export has {exported['count']} notes, expected {len(FACTS) - 1}",
        )
        remaining = {text for text, _t, _q in FACTS if FORGET_QUERY not in text.lower()}
        check(
            {entry["text"] for entry in exported["entries"]} == remaining,
            "the export is not what the store holds",
        )
        # `auth=True`: the default is off because TTS audio is served
        # unauthenticated, and a memory export very much is not.
        markdown = (
            await client.get_bytes("/api/memory/export?format=markdown", auth=True)
        ).decode()
        check("# What Jarvis remembers" in markdown, "the markdown export has no heading")
        record("export", True, f"{exported['count']} notes, json and markdown")

        # --- wipe ----------------------------------------------------------
        wiped = await client.command("jarvis/memory/forget", all=True)
        check(wiped["wiped"] == len(FACTS) - 1, f"wipe reported {wiped}")
        check(
            (await client.command("jarvis/memory/list"))["total"] == 0,
            "something survived the wipe",
        )
        # And on disk, which is where "deleted" has to be true.
        store_file = Path(harness.config_dir) / ".storage" / "memory.json"
        if store_file.is_file():
            data = json.loads(store_file.read_text() or "{}")
            check(not (data.get("data") or data).get("entries"), "the file still holds notes")
        record("wipe", True, "store empty, file empty")
    finally:
        await client.aclose()

    out.mkdir(parents=True, exist_ok=True)
    (out / "memory_eval.json").write_text(
        json.dumps(
            {"steps": steps, "facts": len(FACTS), "recall": recall}, indent=2
        )
        + "\n"
    )
    return {"steps": steps, "recall": recall}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".verify/memory", help="where to write the report")
    parser.add_argument("--keep", action="store_true", help="keep the harness work directory")
    parser.add_argument("--embeddings-url", default=os.environ.get("EMBEDDINGS_URL", ""),
                        help="an OpenAI-compatible embedding server ('' = keyword only)")
    parser.add_argument("--embeddings-model",
                        default=os.environ.get("EMBEDDINGS_MODEL", "BAAI/bge-small-en-v1.5"))
    parser.add_argument("--rerank-url", default=os.environ.get("RERANK_URL", ""),
                        help="a cross-encoder rerank endpoint ('' = no reranking)")
    parser.add_argument("--rerank-model",
                        default=os.environ.get("RERANK_MODEL",
                                               "cross-encoder/ms-marco-MiniLM-L-6-v2"))
    args = parser.parse_args(argv)

    out = Path(args.out)
    harness = Harness(
        work_dir=str(out / "harness"),
        keep=True,
        # The retrieval services, when they are up. Without them this measures
        # the keyword baseline, which is exactly what it measured before M33 —
        # and the printed line says which one you are looking at.
        embeddings_url=args.embeddings_url,
        embeddings_model=args.embeddings_model,
        rerank_url=args.rerank_url,
        rerank_model=args.rerank_model,
    )
    print("memory eval: store → restart → retrieve → forget → export → wipe", flush=True)
    try:
        harness.start()
    except Exception as err:  # noqa: BLE001 - a boot failure is the answer
        print(f"could not start the harness: {err}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(harness, out))
    except Failed as err:
        print(f"\nFAILED: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001
        print(f"\nERROR: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    finally:
        harness.stop(cleanup=not args.keep)
    print("\nmemory eval passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
