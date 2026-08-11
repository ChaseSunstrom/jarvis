#!/usr/bin/env python3
"""Executable spec for the audio captured before a voice run can accept it.

The reported symptom was "the STT doesn't work that well" and "I was talking to
it and it wasn't really able to hear me". Not silence — silence gets debugged.
Transcripts that are subtly, consistently wrong.

`JarvisConversation.start` opens the microphone in the same breath as it dials
the socket. But the run's `stt_binary_handler_id` does not exist until the
server sends `run-start`, which is four round trips away:

    WebSocket upgrade → auth_required/auth/auth_ok
                      → assist_pipeline/pipeline/list → result
                      → assist_pipeline/run           → run-start

`sendAudio` was `val id = sttBinaryHandlerId ?: return`. Every frame captured
before that last arrow was dropped on the floor. On a quiet LAN that is a few
hundred milliseconds; against a cold jarvis-core, or when `ServerEndpoint`
tries the wrong candidate first and waits out its 6-second handshake watchdog,
it is seconds.

And after a wake word the user is ALREADY speaking — "Hey Jarvis, turn on the
lights" is one breath — so what was discarded was the FRONT of the command.

Three properties are pinned here, each because getting it wrong is its own bug:

  * **Order.** The held audio must go out before the handler id is published,
    or a capture thread can slip a later frame in front of the utterance and
    the transcript is scrambled rather than merely short.
  * **A bound.** A server that never answers must not grow a queue on a phone.
    Oldest-first, because the useful part of a delayed start is the most recent
    audio.
  * **Not across turns.** After `endAudio` the id is cleared. Holding audio
    then would prepend the tail of one utterance to the start of the next.

Run:  python3 android-app/tools/audio_prebuffer_test.py
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

KOTLIN = "app/src/main/kotlin/ai/jarvis/app/assist/AssistPipelineClient.kt"

BYTES_PER_SECOND = 16_000 * 2
MAX_PREBUFFER_BYTES = 6 * BYTES_PER_SECOND

#: One 64 ms capture buffer, the size MicStreamer emits.
CHUNK = 2048


class Client:
    """Mirrors the prebuffer half of AssistPipelineClient."""

    def __init__(self, start_stage: str = "stt"):
        self.start_stage = start_stage
        self.handler_id: int | None = None
        self.run_started = False
        self.prebuffer: deque[bytes] = deque()
        self.prebuffered_bytes = 0
        #: Everything that actually reached the wire, in order.
        self.sent: list[bytes] = []

    # --- the run's lifecycle ------------------------------------------------
    def run_pipeline(self) -> None:
        self.handler_id = None
        self.run_started = False

    def on_run_start(self, handler: int) -> None:
        # Flush BEFORE publishing the id — see the module docstring.
        self.flush(handler)
        self.handler_id = handler
        self.run_started = True

    def end_audio(self) -> None:
        self.handler_id = None

    def close(self) -> None:
        self.prebuffer.clear()
        self.prebuffered_bytes = 0

    # --- audio --------------------------------------------------------------
    def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        if self.handler_id is None:
            if not self.run_started:
                self.hold(pcm)
            return
        self.sent.append(pcm)

    def hold(self, pcm: bytes) -> None:
        if self.start_stage == "intent":
            return
        self.prebuffer.append(pcm)
        self.prebuffered_bytes += len(pcm)
        while self.prebuffered_bytes > MAX_PREBUFFER_BYTES and self.prebuffer:
            self.prebuffered_bytes -= len(self.prebuffer.popleft())

    def flush(self, handler: int) -> None:
        held = list(self.prebuffer)
        self.prebuffer.clear()
        self.prebuffered_bytes = 0
        self.sent.extend(held)


def chunks(n: int, first: int = 0) -> list[bytes]:
    """`n` distinguishable capture buffers, so order is checkable."""
    return [bytes([(first + i) % 251]) * CHUNK for i in range(n)]


# --- the cases ------------------------------------------------------------


def check_audio_before_the_run_is_kept() -> int:
    """The bug itself. Everything spoken before run-start used to vanish."""
    c = Client()
    c.run_pipeline()
    early = chunks(10)
    for chunk in early:
        c.send_audio(chunk)
    assert c.sent == [], "nothing should reach the wire before the run opens"

    c.on_run_start(1)
    if c.sent != early:
        print(f"FAIL  {len(early) - len(c.sent)} of {len(early)} early buffers were lost")
        return 1
    return 0


def check_the_utterance_stays_in_order() -> int:
    """Flush before publishing the id, or the transcript is scrambled.

    If the id were published first, a capture thread already inside send_audio
    would put a LATER frame on the wire ahead of the held ones — turning a
    merely-truncated transcript into a jumbled one, which is worse.
    """
    c = Client()
    c.run_pipeline()
    early = chunks(5)
    for chunk in early:
        c.send_audio(chunk)
    c.on_run_start(1)
    late = chunks(5, first=100)
    for chunk in late:
        c.send_audio(chunk)

    if c.sent != early + late:
        print("FAIL  the audio reached the wire out of order")
        return 1
    return 0


def check_the_queue_is_bounded() -> int:
    """A server that never answers must not grow a queue on a phone."""
    c = Client()
    c.run_pipeline()
    # A full minute of audio into a run that never opens.
    for chunk in chunks(60 * BYTES_PER_SECOND // CHUNK):
        c.send_audio(chunk)
    failures = 0
    if c.prebuffered_bytes > MAX_PREBUFFER_BYTES:
        print(f"FAIL  the prebuffer grew to {c.prebuffered_bytes} bytes")
        failures += 1
    if c.prebuffered_bytes < MAX_PREBUFFER_BYTES - CHUNK:
        print("FAIL  the prebuffer is dropping more than it needs to")
        failures += 1
    return failures


def check_the_newest_audio_survives() -> int:
    """Oldest-first. A delayed start makes the RECENT audio the useful part."""
    c = Client()
    c.run_pipeline()
    total = 60 * BYTES_PER_SECOND // CHUNK
    every = chunks(total)
    for chunk in every:
        c.send_audio(chunk)
    c.on_run_start(1)

    if not c.sent:
        print("FAIL  everything was dropped")
        return 1
    if c.sent[-1] != every[-1]:
        print("FAIL  the most recent audio was dropped instead of the oldest")
        return 1
    if c.sent[0] == every[0]:
        print("FAIL  nothing was dropped at all, so the bound is not working")
        return 1
    return 0


def check_audio_is_not_held_across_turns() -> int:
    """After end_audio the turn is over.

    Holding then would prepend the tail of one utterance to the start of the
    next — the user's "thanks" arriving as the first word of their next command.
    """
    c = Client()
    c.run_pipeline()
    c.on_run_start(1)
    c.send_audio(b"\x01" * CHUNK)
    c.end_audio()

    trailing = b"\x02" * CHUNK
    c.send_audio(trailing)
    if c.prebuffer:
        print("FAIL  audio after end_audio was held for the next turn")
        return 1

    # ...and the next turn starts clean.
    c.run_pipeline()
    fresh = b"\x03" * CHUNK
    c.send_audio(fresh)
    c.on_run_start(2)
    if trailing in c.sent:
        print("FAIL  the previous turn's tail was sent into the next turn")
        return 1
    if fresh not in c.sent:
        print("FAIL  the next turn's own early audio was lost")
        return 1
    return 0


def check_a_text_run_holds_nothing() -> int:
    """A StartStage.INTENT run carries a sentence; there is no audio to keep."""
    c = Client(start_stage="intent")
    c.run_pipeline()
    for chunk in chunks(20):
        c.send_audio(chunk)
    if c.prebuffer:
        print("FAIL  a text-only run is buffering microphone audio")
        return 1
    return 0


def check_closing_drops_what_it_held() -> int:
    c = Client()
    c.run_pipeline()
    for chunk in chunks(10):
        c.send_audio(chunk)
    c.close()
    if c.prebuffer or c.prebuffered_bytes:
        print("FAIL  closing the client leaked its held audio")
        return 1
    return 0


# --- the Kotlin has to agree ----------------------------------------------


def check_kotlin_agrees(android: Path) -> int:
    path = android / KOTLIN
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    src = path.read_text(encoding="utf-8")
    failures = 0

    # The regression itself: a bare early return throws the audio away.
    if re.search(r"fun sendAudio\([^)]*\)\s*\{\s*\n\s*val id = sttBinaryHandlerId \?: return", src):
        print(
            "FAIL  sendAudio drops audio again when the run is not open yet. That is "
            "the front of every command spoken after a wake word."
        )
        failures += 1

    for const, value in (
        ("BYTES_PER_SECOND", "16_000 * 2"),
        ("MAX_PREBUFFER_BYTES", "6 * BYTES_PER_SECOND"),
    ):
        if f"const val {const} = {value}" not in src:
            print(f"FAIL  AssistPipelineClient.{const} is no longer {value}")
            failures += 1

    # Order: the flush must precede publishing the id.
    flush = src.find("flushPrebuffer(handler)")
    publish = src.find("sttBinaryHandlerId = handler")
    if flush < 0 or publish < 0:
        print("FAIL  run-start no longer flushes the held audio")
        failures += 1
    elif flush > publish:
        print(
            "FAIL  the handler id is published before the held audio is flushed, so a "
            "capture thread can put a later frame in front of the utterance"
        )
        failures += 1

    # Oldest-first, and bounded.
    if "prebuffer.removeFirst()" not in src:
        print("FAIL  the prebuffer no longer drops its OLDEST audio")
        failures += 1
    if "prebufferedBytes > MAX_PREBUFFER_BYTES" not in src:
        print("FAIL  the prebuffer is unbounded")
        failures += 1

    # Not across turns.
    if "if (!runStarted) hold(pcm, len)" not in src:
        print("FAIL  audio is held after the turn ended, which prepends it to the next")
        failures += 1
    if "if (startStage == StartStage.INTENT) return" not in src:
        print("FAIL  a text-only run buffers microphone audio for nothing")
        failures += 1

    # The capture thread and the socket callback both touch the queue.
    if "synchronized(prebuffer)" not in src:
        print("FAIL  the prebuffer is touched from two threads without a lock")
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_audio_before_the_run_is_kept()
        + check_the_utterance_stays_in_order()
        + check_the_queue_is_bounded()
        + check_the_newest_audio_survives()
        + check_audio_is_not_held_across_turns()
        + check_a_text_run_holds_nothing()
        + check_closing_drops_what_it_held()
        + check_kotlin_agrees(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        "audio prebuffer: the front of an utterance survives a slow run start, "
        "in order, bounded, and never across turns"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
