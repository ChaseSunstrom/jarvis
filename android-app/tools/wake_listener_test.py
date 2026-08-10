#!/usr/bin/env python3
"""Executable spec for the always-on wake listener's microphone hand-off.

`WakeWordService` holds the microphone open so "Hey Jarvis" works with the
phone face-down on a table. `JarvisConversation` opens its own the moment a
conversation starts. Two `AudioRecord`s on one device is a coin toss over which
one receives the audio, and losing that toss means the conversation the user
just triggered hears nothing — which is the exact symptom this whole area has
been plagued by, so it gets a spec rather than a comment.

The invariant: **at most one holder at any time**, and the wake listener always
gets it back afterwards, including on the paths where the conversation ends
badly.

Run:  python3 android-app/tools/wake_listener_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WAKE = "wake"
CONVO = "convo"


class Mic:
    """Who holds the microphone. Mirrors the two owners' start/stop calls."""

    def __init__(self) -> None:
        self.holder: str | None = None
        self.conflicts: list[str] = []

    def acquire(self, who: str) -> None:
        if self.holder is not None and self.holder != who:
            self.conflicts.append(f"{who} opened the mic while {self.holder} held it")
        self.holder = who

    def release(self, who: str) -> None:
        if self.holder == who:
            self.holder = None


class Phone:
    """The two components, as far as microphone ownership is concerned."""

    def __init__(self, wake_enabled: bool = True) -> None:
        self.mic = Mic()
        self.wake_enabled = wake_enabled
        self.service_running = False

    # --- WakeWordService ---------------------------------------------------
    def wake_start(self) -> None:
        if not self.wake_enabled:
            return
        self.service_running = True
        self.mic.acquire(WAKE)

    def wake_pause(self) -> None:
        # Mirrors WakeWordService.pause(): closeLink() drops the recorder.
        if not self.wake_enabled:
            return
        self.mic.release(WAKE)

    def wake_resume(self) -> None:
        # Mirrors the companion's resume(): a no-op when the setting is off, so
        # a user who turned wake-word off mid-conversation does not get a
        # microphone re-opened behind them.
        if not self.wake_enabled or not self.service_running:
            return
        self.mic.acquire(WAKE)

    def wake_stop(self) -> None:
        self.service_running = False
        self.wake_enabled = False
        self.mic.release(WAKE)

    # --- JarvisAssistActivity ----------------------------------------------
    def convo_start(self) -> None:
        # The activity pauses the listener BEFORE opening its own recorder.
        self.wake_pause()
        self.mic.acquire(CONVO)

    def convo_end(self) -> None:
        # onDestroy: stop first, then hand the mic back.
        self.mic.release(CONVO)
        self.wake_resume()


def scenario_wake_then_talk() -> Phone:
    """The headline path: say the name, talk, finish."""
    p = Phone()
    p.wake_start()
    p.convo_start()  # triggered by onWakeWord
    p.convo_end()
    return p


def scenario_tap_to_talk_while_listening() -> Phone:
    """The user taps the button while the listener is already running."""
    p = Phone()
    p.wake_start()
    p.convo_start()
    p.convo_end()
    return p


def scenario_two_conversations_back_to_back() -> Phone:
    p = Phone()
    p.wake_start()
    p.convo_start()
    p.convo_end()
    p.convo_start()
    p.convo_end()
    return p


def scenario_disabled_midway() -> Phone:
    """Wake-word turned off while a conversation is open."""
    p = Phone()
    p.wake_start()
    p.convo_start()
    p.wake_enabled = False  # the user unticks it in Settings
    p.convo_end()
    return p


def scenario_never_enabled() -> Phone:
    """Push-to-talk on a phone that never wanted always-on."""
    p = Phone(wake_enabled=False)
    p.wake_start()
    p.convo_start()
    p.convo_end()
    return p


def scenario_stop_button() -> Phone:
    p = Phone()
    p.wake_start()
    p.wake_stop()
    return p


SCENARIOS = [
    (scenario_wake_then_talk, WAKE, "the listener takes the mic back after the conversation"),
    (scenario_tap_to_talk_while_listening, WAKE, "a tap while listening must not double-open"),
    (scenario_two_conversations_back_to_back, WAKE, "the hand-off survives repetition"),
    (
        scenario_disabled_midway,
        None,
        "turning it off mid-conversation must not re-open the mic afterwards",
    ),
    (scenario_never_enabled, None, "no listener means nothing holds the mic when idle"),
    (scenario_stop_button, None, "STOP releases the mic and stays off"),
]


def check_scenarios() -> list[str]:
    failures = []
    for build, expected_holder, why in SCENARIOS:
        phone = build()
        if phone.mic.conflicts:
            failures.append(f"{build.__name__}: {phone.mic.conflicts[0]}\n    ({why})")
        if phone.mic.holder != expected_holder:
            failures.append(
                f"{build.__name__}: mic ends up with {phone.mic.holder}, "
                f"expected {expected_holder}\n    ({why})"
            )
    return failures


def check_kotlin_still_says_so() -> list[str]:
    root = Path(__file__).resolve().parents[1] / "app/src/main/kotlin/ai/jarvis/app"
    required = [
        (
            "JarvisAssistActivity.kt",
            "WakeWordService.pause(this@JarvisAssistActivity)",
            "the listener being paused before the conversation opens its recorder",
        ),
        (
            "JarvisAssistActivity.kt",
            "WakeWordService.resume(this)",
            "the microphone being handed back in onDestroy",
        ),
        (
            "assist/WakeWordService.kt",
            "AssistPipelineClient.StartStage.WAKE_WORD",
            "the run starting at the wake stage rather than at stt",
        ),
        (
            "assist/WakeWordService.kt",
            "ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE",
            "the foreground type the platform requires before a service may capture",
        ),
        (
            "assist/WakeWordService.kt",
            "config.wakeWordEnabled = false",
            "STOP turning the setting off, so it does not restart itself",
        ),
        (
            "assist/AssistPipelineClient.kt",
            '"wake_word-end"',
            "the detection event being handled at all",
        ),
    ]
    failures = []
    for name, snippet, what in required:
        path = root / name
        if not path.exists():
            failures.append(f"missing {name}")
            continue
        if snippet not in path.read_text(encoding="utf-8"):
            failures.append(f"{name} no longer contains {what} ({snippet!r})")
    return failures


def check_manifest() -> list[str]:
    path = Path(__file__).resolve().parents[1] / "app/src/main/AndroidManifest.xml"
    text = path.read_text(encoding="utf-8")
    failures = []
    if "ai.jarvis.app.assist.WakeWordService" not in text:
        failures.append("WakeWordService is not declared in the manifest")
        return failures
    # The declaration must carry the microphone type, or startForeground throws
    # on 34+ and always-on listening dies on exactly the phones that have it.
    block = re.search(
        r'<service[^>]*android:name="ai\.jarvis\.app\.assist\.WakeWordService".*?/?>',
        text,
        re.S,
    )
    if not block or "microphone" not in block.group(0):
        failures.append("WakeWordService is declared without foregroundServiceType=microphone")
    if "android.permission.FOREGROUND_SERVICE_MICROPHONE" not in text:
        failures.append("FOREGROUND_SERVICE_MICROPHONE is not requested")
    return failures


def main() -> int:
    failures = check_scenarios() + check_kotlin_still_says_so() + check_manifest()
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failed", file=sys.stderr)
        return 1
    print(f"wake listener: {len(SCENARIOS)} hand-off scenarios, the Kotlin and the manifest agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
