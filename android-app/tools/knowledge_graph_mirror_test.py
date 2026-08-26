#!/usr/bin/env python3
"""The phone's knowledge graph is the console's (M61).

`assist/KnowledgeGraph.kt` is a port of `jarvis-web/src/lib/knowledge/graph.ts`;
`tests/contracts/knowledge_graph.json` pins the nodes and edges both build for
one small house. The console's vitest asserts the TypeScript against it and
`app/src/test/.../KnowledgeGraphTest.kt` asserts the Kotlin against the same
fixture on the JVM. This mirror, which runs with no JVM, reads both sources as
text: the constants that shape the layout agree with the contract, the Kotlin
test carries the contract's fixture verbatim, and the view only paints.

Run:  python3 android-app/tools/knowledge_graph_mirror_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KT = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/KnowledgeGraph.kt"
KT_TEST = ROOT / "app/src/test/kotlin/ai/jarvis/app/assist/KnowledgeGraphTest.kt"
VIEW = ROOT / "app/src/main/kotlin/ai/jarvis/app/ui/KnowledgeGraphView.kt"
TS = ROOT.parent / "jarvis-web/src/lib/knowledge/graph.ts"
CONTRACT = ROOT.parent / "tests/contracts/knowledge_graph.json"


def contract() -> dict:
    return json.loads(CONTRACT.read_text())


def const(src: str, name: str) -> float:
    m = re.search(rf"const val {name} = ([0-9.]+)f?", src)
    assert m, f"{name} not in the Kotlin"
    return float(m.group(1))


def test_the_constants_that_shape_the_layout_agree():
    kt = KT.read_text()
    ts = TS.read_text()
    want = contract()["constants"]
    assert const(kt, "TAG_FANOUT") == want["tag_fanout"] and f"TAG_FANOUT = {want['tag_fanout']}" in ts
    assert const(kt, "LABEL_CHARS") == want["label_chars"] and f"length > {want['label_chars']}" in ts
    assert const(kt, "ITERATIONS") == want["iterations"] and f"iterations ?? {want['iterations']}" in ts
    assert const(kt, "LINK_WEIGHT") == want["link_weight"] and const(kt, "TAG_WEIGHT") == want["tag_weight"]
    assert f"'link' ? {want['link_weight']} : {want['tag_weight']}" in ts


def test_the_prng_is_the_consoles_bit_for_bit():
    kt = KT.read_text()
    ts = TS.read_text()
    for number in ("2166136261", "16777619", "2246822507", "3266489909", "4294967296"):
        assert number in kt and number in ts, number
    assert "ushr 15" in kt and ">>> 15" in ts and "ushr 13" in kt and ">>> 13" in ts


def test_the_kotlin_test_carries_the_contracts_fixture_and_expectations():
    test = KT_TEST.read_text()
    c = contract()
    for note in c["notes"]:
        assert f'"{note["id"]}"' in test and f'"{note["title"]}"' in test, note["id"]
    for entry in c["memory"]:
        assert f'"{entry["text"]}"' in test, entry["id"]
    for edge in c["edges"]:
        assert f'"{edge["from"]}"' in test and f'"{edge["to"]}"' in test, edge
    assert c["nodes"][3]["label"] in test, "the truncated memory label"


def test_the_view_only_paints_in_tokens():
    view = VIEW.read_text()
    assert "KnowledgeGraph.layout(" in view and "fun render(" in view and "fun pulse(" in view
    assert not re.search(r"Color\.parseColor|0x[0-9A-Fa-f]{6,8}", view), "a colour typed by hand"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
