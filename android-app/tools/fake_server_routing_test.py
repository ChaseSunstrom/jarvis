#!/usr/bin/env python3
"""Executable spec: the fake server pushes to the device channel, not to whoever dialled last.

`FakeJarvisServer` is the whole server for the instrumented suite. Tests script
it with `sendCompanionMessage`, `sendDeviceCommand` and friends, and every one
of those ends in `send(frame)`, which used to mean:

    val live = socket ?: error(...)      // `socket` = the LAST connection opened

That is only correct while the app opens exactly one socket, and it does not.
`CompanionAskActivity.askAloud()` runs from `onCreate` for every MODE_ASK
message and builds a `CompanionVoiceClient` against the same `serverUrl`, which
dials immediately. From the moment a question is on screen, the fake's one
pointer aims at the voice client.

## Why it was invisible

Nothing errors. Three separate mechanisms each swallow half the evidence:

  * frames the DEVICE sends are unaffected — `JarvisChannel.sendFrame` writes to
    its own session socket, and `received` is collected across all connections,
    so the first `jarvis_message_result` arrives exactly as expected;
  * `WebSocket.send` returns a Boolean that `FakeJarvisServer` discards, so a
    frame handed to the wrong socket is "sent" successfully;
  * `CompanionVoiceClient.onMessage` has branches for `auth_required`,
    `auth_ok`, `auth_invalid`, `event` and `result` — and none for
    `jarvis_message`, so the misrouted frame is dropped without a log line.

`CompanionMessageHandler.handle` is reached from exactly one place in the app
(`JarvisChannel.onText`), so a frame that misses the channel socket never
touches the ledger: no replayed answer, and no second question either. That is
precisely the pair `CompanionAskTest.aDuplicateDeliveryReplaysTheAnswerAndAsks-
NobodyAgain` reported, as a 45-second timeout, on a device that behaved
correctly throughout.

The suite already half-knew. `DeviceChannelTest` calls `TestHooks.muteMicrophone`
to stop a second socket reaching its fake — a workaround for this defect, at the
one call site where somebody hit it and diagnosed it locally.

## The rule this pins

`jarvis/device/register` is sent by `JarvisChannel` and by nothing else, and it
is re-sent on every reconnect. So: the socket that registers is the device
channel; server pushes go there; and a socket closing only clears the slot it
actually occupied.

This mirror exists because the emulator suite costs ten minutes and an AVD, and
this rule is worth checking in milliseconds — the Kotlin it mirrors cannot be
compiled on a runner without the Android SDK.

Run:  python3 android-app/tools/fake_server_routing_test.py
      python3 -m pytest android-app/tools/fake_server_routing_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
FAKE = ANDROID / "app/src/androidTest/kotlin/ai/jarvis/app/support/FakeJarvisServer.kt"
ASK = ANDROID / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionAskActivity.kt"
VOICE = ANDROID / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionVoiceClient.kt"


# --- the model ---------------------------------------------------------------


class FakeServer:
    """The routing rule, in the small. Mirrors FakeJarvisServer's three slots."""

    def __init__(self) -> None:
        self.last_opened: str | None = None
        self.command: str | None = None
        self.delivered: list[tuple[str, str]] = []

    def on_open(self, sock: str) -> None:
        self.last_opened = sock

    def on_register(self, sock: str) -> None:
        self.command = sock

    def on_closed(self, sock: str) -> None:
        # Only the slot the socket actually occupied.
        if self.last_opened == sock:
            self.last_opened = None
        if self.command == sock:
            self.command = None

    def send(self, frame: str) -> None:
        live = self.command or self.last_opened
        if live is None:
            raise AssertionError("the fake server has no live socket")
        self.delivered.append((live, frame))


def check(name: str, condition: bool, detail: str) -> bool:
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        print("      " + detail)
    return condition


def test_a_redelivery_reaches_the_channel_after_the_ask_screen_dials() -> None:
    """The exact CompanionAskTest sequence."""
    s = FakeServer()
    s.on_open("channel")
    s.on_register("channel")
    s.send("jarvis_message #1")
    # The question is on screen; askAloud() dials the voice client.
    s.on_open("voice")
    s.send("jarvis_message #1 redelivered")
    assert [sock for sock, _ in s.delivered] == ["channel", "channel"], s.delivered


def test_the_old_rule_is_what_broke_it() -> None:
    """Last-opened-wins misroutes the redelivery — the bug, reproduced."""
    s = FakeServer()
    s.on_open("channel")
    s.on_register("channel")
    s.on_open("voice")
    s.command = None  # the pre-fix server had no such slot
    s.send("jarvis_message redelivered")
    assert s.delivered == [("voice", "jarvis_message redelivered")], s.delivered


def test_the_voice_socket_closing_does_not_blind_the_server() -> None:
    s = FakeServer()
    s.on_open("channel")
    s.on_register("channel")
    s.on_open("voice")
    s.on_closed("voice")
    s.send("still routable")
    assert s.delivered == [("channel", "still routable")], s.delivered


def test_a_reconnect_moves_the_channel() -> None:
    s = FakeServer()
    s.on_open("channel-1")
    s.on_register("channel-1")
    s.on_closed("channel-1")
    s.on_open("channel-2")
    s.on_register("channel-2")
    s.send("after reconnect")
    assert s.delivered == [("channel-2", "after reconnect")], s.delivered


def test_before_registration_it_falls_back_to_the_only_connection() -> None:
    """A test scripting the handshake itself must still be able to push."""
    s = FakeServer()
    s.on_open("channel")
    s.send("auth_required")
    assert s.delivered == [("channel", "auth_required")], s.delivered


def test_no_socket_at_all_is_still_a_loud_failure() -> None:
    s = FakeServer()
    try:
        s.send("nothing to send on")
    except AssertionError:
        return
    raise AssertionError("send() must fail loudly when nothing is connected")


# --- the source these claims are about ---------------------------------------


def _body(path: Path) -> str:
    """Source with // comments stripped, so a claim in prose is not evidence."""
    return re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))


def test_the_kotlin_actually_implements_the_rule() -> None:
    src = _body(FAKE)
    assert "private var commandSocket: WebSocket? = null" in src, (
        "FakeJarvisServer must keep the device channel in a slot of its own"
    )
    assert re.search(r"commandSocket\s*=\s*webSocket", src), (
        "the register branch must claim the socket as the device channel"
    )
    assert re.search(r"val live = commandSocket \?: socket \?: error", src), (
        "send() must prefer the device channel, falling back only before registration"
    )
    assert re.search(r"if \(commandSocket === webSocket\) commandSocket = null", src), (
        "a closing socket must clear the command slot only when it held it"
    )


def test_the_second_socket_is_real_and_not_a_story() -> None:
    """If askAloud stops dialling, this spec is about nothing and should say so."""
    ask = _body(ASK)
    assert "askAloud()" in ask, "CompanionAskActivity no longer speaks its question"
    assert re.search(r"CompanionVoiceClient\(config\.serverUrl", ask), (
        "askAloud must still build a client against the configured server, or the "
        "premise of this spec has gone"
    )
    voice = _body(VOICE)
    assert "newWebSocket" in voice, "CompanionVoiceClient no longer opens a socket"
    assert '"jarvis_message"' not in voice, (
        "CompanionVoiceClient has grown a jarvis_message branch — if it now handles "
        "companion frames, the misrouting would no longer be silent and this spec "
        "needs rewriting rather than passing"
    )


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = True
    for fn in tests:
        try:
            fn()
            ok &= check(fn.__name__, True, "")
        except AssertionError as e:
            ok &= check(fn.__name__, False, str(e))
    print(("\nall %d checks passed" % len(tests)) if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
