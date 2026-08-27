#!/usr/bin/env python3
"""Executable spec: the phone shows what Jarvis said while nobody was looking.

jarvis-core keeps a record of every proactive message and fires
`jarvis_notification` as each is made (`jarvis/integrations/notifications/`).
The console draws an inbox; this is the phone's half, and the phone is the
surface that matters most for it — a research run that finishes at three in the
afternoon is not something anybody was watching a screen for.

The Kotlin cannot be compiled here (no JDK, no SDK — see
`docs/ANDROID_DEVICE_TESTS.md`), so this reads `MomentWatch.kt` and the server
and fails when the two disagree.

Run:  python3 android-app/tools/moments_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
REPO = ANDROID.parent
WATCH_KT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/tasks/MomentWatch.kt"
CHANNEL_KT = ANDROID / "app/src/main/kotlin/ai/jarvis/app/channel/JarvisChannel.kt"
INTEGRATION = REPO / "jarvis-core/jarvis/integrations/notifications/__init__.py"
WEBSOCKET = REPO / "jarvis-core/jarvis/api/websocket.py"

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {message}")
    if not ok:
        failures.append(message)


def main() -> int:
    kotlin = WATCH_KT.read_text(encoding="utf-8")
    channel = CHANNEL_KT.read_text(encoding="utf-8")
    server = INTEGRATION.read_text(encoding="utf-8")
    sockets = WEBSOCKET.read_text(encoding="utf-8")

    print("moments parity")

    # The event and the command, spelled the same on both sides.
    event = re.search(r'EVENT_NOTIFICATION = "([a-z_]+)"', server)
    check(bool(event), "the server names the event")
    if event:
        check(
            f'const val EVENT = "{event.group(1)}"' in kotlin,
            f"the phone listens for {event.group(1)}",
        )
    check(
        '"jarvis/notifications/list"' in sockets,
        "the server answers jarvis/notifications/list",
    )
    check(
        'TYPE_LIST = "jarvis/notifications/list"' in kotlin,
        "and the phone asks for it by that name",
    )

    # Every field the phone reads is one the server sends, read with the
    # accessor for its type. `"opt" in kotlin` would pass for a file that reads
    # nothing at all, which is the shape of check this repo calls vacuous.
    sent = set(re.findall(r'"([a-z_]+)":', server))
    readers = {
        "id": "optString",
        "kind": "optString",
        "title": "optString",
        "body": "optString",
        "source": "optString",
        "link": "optString",
        "at": "optDouble",
        "read": "optBoolean",
    }
    for field, reader in readers.items():
        check(f'{reader}("{field}"' in kotlin, f"the phone reads {field} with {reader}")
        check(field in sent, f"and the server sends {field}")

    # Subscribe first, then list — the window this closes is a record made
    # between the two.
    order = re.search(
        r"private fun watchMoments.*?subscribe.*?TYPE_LIST", channel, re.S
    )
    check(bool(order), "the phone subscribes before it lists")

    # The trap this file exists for, twice over.
    check(
        'optDouble("at", 0.0)' in kotlin,
        "the timestamp is read with a default (optDouble(key) answers NaN)",
    )
    check(
        "if (!TaskWatch.onEvent(msg) && !MomentWatch.onEvent(msg)) SurfaceWatch.onEvent(msg)" in channel,
        "a task event does not fall through to the moment board",
    )
    check("MAX_KEPT" in kotlin, "the phone keeps a bounded number of them")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("moments parity: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
