#!/usr/bin/env python3
"""The phone plays a reply the way the console does (M60/M61): sentences as
they come, then the remainder, never the whole reply twice.

`tts-chunk` arrives before the model has finished; `tts-end` carries the whole
reply for a client that ignores chunks, plus `remainder_url` for one that did
not. The Kotlin is read as text: the client handles both events, refuses an
off-origin URL, and the conversation queues chunks in order and ends the turn
when the queue drains.

Run:  python3 android-app/tools/tts_chunk_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/AssistPipelineClient.kt"
CONVERSATION = ROOT / "app/src/main/kotlin/ai/jarvis/app/assist/JarvisConversation.kt"
CLIENTS_DOC = ROOT.parent / "jarvis-core/docs/clients.md"


def test_the_client_handles_tts_chunk_and_passes_the_remainder():
    src = CLIENT.read_text()
    chunk = src[src.index('"tts-chunk" ->'): src.index('"tts-end" ->')]
    assert "absolute(url)" in chunk, "a chunk url is not checked against the server's origin"
    assert "callbacks.onTtsChunk(resolved, index)" in chunk
    end = src[src.index('"tts-end" ->'): src.index('"run-end" ->')]
    assert "remainder_url" in end and "callbacks.onTtsEnd(resolved, remainder, chunks)" in end
    assert "absolute(it)" in end, "the remainder is played from any origin"
    # The old contract survives for a caller that overrides only onTtsUrl.
    assert re.search(r"fun onTtsEnd\(.*?\)\s*\{\s*onTtsUrl\(absoluteUrl\)", src, re.S)


def test_the_conversation_plays_chunks_in_order_then_the_remainder_once():
    src = CONVERSATION.read_text()
    assert "ArrayDeque<String>" in src and "fun playNextChunk()" in src
    end = src[src.index("override fun onTtsEnd("): src.index("private fun playNextChunk()")]
    assert "if (chunksHeard == 0)" in end and "onTtsUrl(absoluteUrl)" in end, "a reply with no chunks is no longer played whole"
    assert "remainderUrl != null" in end and "chunkQueue.addLast(remainderUrl)" in end
    assert "tts?.play(absoluteUrl)" not in end.split("return")[1], "the whole reply is played after the chunks"
    nxt = src[src.index("private fun playNextChunk()"): src.index("override fun onBusEvent(")]
    assert "beginNextTurn()" in nxt, "the turn does not end when the queue drains"
    begin = src[src.index("private fun beginNextTurn()"): src.index("private fun beginNextTurn()") + 400]
    assert "chunkQueue.clear()" in begin, "a new turn inherits the last one's queue"


def test_the_wire_is_documented_for_every_client():
    doc = CLIENTS_DOC.read_text()
    assert "tts-chunk" in doc and "remainder_url" in doc


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
