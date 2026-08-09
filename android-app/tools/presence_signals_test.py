#!/usr/bin/env python3
"""Executable spec for the phone's half of cross-device conversation.

Two pieces of Kotlin decide whether Jarvis can reach you on this phone, and
neither can be compiled in this container, so both are written down twice: once
in Kotlin, and once here, where it runs.

  1. `channel/PresenceReporter.kt` -> `PresenceThrottle`
     When a presence sample is worth a frame. Wrong in one direction it is a
     firehose that eats the battery; wrong in the other the phone goes quiet,
     the server marks it ABSENT, and every proactive message lands on some other
     device while the user is staring at this one.

  2. `companion/CompanionLedger.kt` + `CompanionMessageHandler.kt`
     The ask-flow state machine: delivered -> answered | dismissed | timeout,
     with EXACTLY ONE reply per message_id. The server escalates on anything
     that is not `answered`, so a second, different reply pushes a question the
     user already dealt with onto another device — and no reply at all leaves an
     automation blocked until its own timeout.

Three kinds of check:

  * the rules, re-implemented below, agree with explicit tables written out by
    hand — so a bug in "the algorithm" cannot hide in both copies;
  * the state machine is driven through every ordering that matters, including
    the redelivery and process-death paths;
  * the Kotlin source still contains those rules, which catches someone editing
    one copy and not the other.

Run:  python3 android-app/tools/presence_signals_test.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN_PRESENCE = ROOT / "app/src/main/kotlin/ai/jarvis/app/channel/PresenceReporter.kt"
KOTLIN_LEDGER = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionLedger.kt"
KOTLIN_PROTOCOL = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionMessage.kt"
KOTLIN_HANDLER = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionMessageHandler.kt"
KOTLIN_GATE = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionAskGate.kt"
KOTLIN_ACTIVITY = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionAskActivity.kt"
KOTLIN_VOICE = ROOT / "app/src/main/kotlin/ai/jarvis/app/companion/CompanionVoiceClient.kt"


# --- the throttle, mirrored from PresenceThrottle ---------------------------

HEARTBEAT_MS = 60_000
MIN_INTERVAL_MS = 3_000
POLL_INTERVAL_MS = 5_000
ACTIVE_WITHIN_MS = 120_000
BATTERY_STEP = 5

#: The signals compared, in the order the Kotlin compares them.
TRACKED = [
    "screen_on", "locked", "driving", "zone",
    "audio_available", "muted", "charging", "jarvis_foreground",
    "active", "battery",
]


@dataclass(frozen=True)
class Signals:
    screen_on: bool = False
    locked: bool = True
    last_interaction_ms: int = 0
    active: bool | None = None
    driving: bool = False
    zone: str | None = None
    audio_available: bool = True
    muted: bool = False
    battery: int | None = None
    charging: bool | None = None
    jarvis_foreground: bool = False


def active_at(last_interaction_ms: int, now_ms: int) -> bool | None:
    """Null when the phone has never seen an interaction: an unknown value must
    not flap the edge every time it is sampled."""
    if last_interaction_ms <= 0:
        return None
    return now_ms - last_interaction_ms <= ACTIVE_WITHIN_MS


def driving(car_bluetooth_connected: bool) -> bool:
    return car_bluetooth_connected


def battery_moved(before: int | None, after: int | None) -> bool:
    if after is None:
        return False
    if before is None:
        return True
    return abs(after - before) >= BATTERY_STEP


def meaningful_change(before: Signals | None, after: Signals) -> str | None:
    if before is None:
        return "first report"
    for name in ("screen_on", "locked", "driving", "zone", "audio_available",
                 "muted", "charging", "jarvis_foreground", "active"):
        if getattr(before, name) != getattr(after, name):
            return name
    if battery_moved(before.battery, after.battery):
        return "battery"
    return None


def should_send(
    before: Signals | None,
    after: Signals,
    since_sent_ms: int,
    heartbeat_ms: int = HEARTBEAT_MS,
    min_interval_ms: int = MIN_INTERVAL_MS,
) -> str | None:
    if before is None:
        return "first report"
    if since_sent_ms >= heartbeat_ms:
        return "heartbeat"
    if since_sent_ms < min_interval_ms:
        return None
    return meaningful_change(before, after)


BASE = Signals(
    screen_on=True,
    locked=False,
    last_interaction_ms=1_000_000,
    active=True,
    audio_available=True,
    battery=80,
    charging=False,
)


# --- the throttle: the table written out by hand ----------------------------

#: (description, before, after, ms since the last SENT frame, expected reason)
THROTTLE_TABLE = [
    ("nothing sent yet", None, BASE, 0, "first report"),
    ("nothing sent yet, and it is early", None, BASE, 1, "first report"),

    ("idle creeping up is not a change", BASE, BASE, 30_000, None),
    ("heartbeat, one tick early", BASE, BASE, HEARTBEAT_MS - 1, None),
    ("heartbeat, exactly due", BASE, BASE, HEARTBEAT_MS, "heartbeat"),
    ("heartbeat, overdue", BASE, BASE, HEARTBEAT_MS * 3, "heartbeat"),

    ("screen off", BASE, replace(BASE, screen_on=False), 5_000, "screen_on"),
    ("locked", BASE, replace(BASE, locked=True), 5_000, "locked"),
    ("got in the car", BASE, replace(BASE, driving=True), 5_000, "driving"),
    ("left the car", replace(BASE, driving=True), BASE, 5_000, "driving"),
    ("arrived home", BASE, replace(BASE, zone="home"), 5_000, "zone"),
    ("headphones out", BASE, replace(BASE, audio_available=False), 5_000, "audio_available"),
    ("silenced", BASE, replace(BASE, muted=True), 5_000, "muted"),
    ("plugged in", BASE, replace(BASE, charging=True), 5_000, "charging"),
    ("app came forward", BASE, replace(BASE, jarvis_foreground=True), 5_000,
     "jarvis_foreground"),
    ("went quiet", BASE, replace(BASE, active=False), 5_000, "active"),
    ("came back", replace(BASE, active=False), BASE, 5_000, "active"),

    ("battery drifting one point", BASE, replace(BASE, battery=79), 5_000, None),
    ("battery drifting under the step", BASE, replace(BASE, battery=76), 5_000, None),
    ("battery moved a real step", BASE, replace(BASE, battery=75), 5_000, "battery"),
    ("battery moved up a real step", BASE, replace(BASE, battery=85), 5_000, "battery"),
    ("learning the battery for the first time",
     replace(BASE, battery=None), replace(BASE, battery=42), 5_000, "battery"),
    ("losing the battery reading is not news",
     BASE, replace(BASE, battery=None), 5_000, None),

    # The rate floor wins over any change. The change is compared against the
    # last SENT snapshot, so it is still pending and goes out on the next tick.
    ("the floor suppresses a lock", BASE, replace(BASE, locked=True), 0, None),
    ("the floor suppresses a lock, just", BASE, replace(BASE, locked=True),
     MIN_INTERVAL_MS - 1, None),
    ("past the floor, the lock goes out", BASE, replace(BASE, locked=True),
     MIN_INTERVAL_MS, "locked"),

    ("an unknown active state does not flap",
     replace(BASE, active=None, last_interaction_ms=0),
     replace(BASE, active=None, last_interaction_ms=0), 5_000, None),
]


def test_throttle_table():
    for description, before, after, elapsed, expected in THROTTLE_TABLE:
        actual = should_send(before, after, elapsed)
        assert actual == expected, (
            f"{description}: should_send(...) = {actual!r}, expected {expected!r}"
        )


def test_the_poll_interval_clears_the_rate_floor():
    """Otherwise a real change would need two ticks to escape, every time."""
    assert POLL_INTERVAL_MS >= MIN_INTERVAL_MS


def test_the_heartbeat_is_well_inside_the_servers_absent_window():
    """jarvis/presence.py calls a device ABSENT after 15 minutes of silence."""
    assert HEARTBEAT_MS <= 15 * 60_000 // 4


def test_the_active_edge_matches_the_servers():
    """`ACTIVE_WITHIN` in jarvis/presence.py is 120 seconds."""
    assert ACTIVE_WITHIN_MS == 120_000
    assert active_at(0, 1_000_000) is None
    assert active_at(1_000_000, 1_000_000) is True
    assert active_at(1_000_000, 1_000_000 + ACTIVE_WITHIN_MS) is True
    assert active_at(1_000_000, 1_000_000 + ACTIVE_WITHIN_MS + 1) is False


def test_driving_is_the_car_stereo():
    assert driving(True) is True
    assert driving(False) is False


def test_every_tracked_signal_is_actually_compared():
    """A field in TRACKED that nothing compares is a signal silently ignored."""
    variants = {
        "screen_on": replace(BASE, screen_on=False),
        "locked": replace(BASE, locked=True),
        "driving": replace(BASE, driving=True),
        "zone": replace(BASE, zone="work"),
        "audio_available": replace(BASE, audio_available=False),
        "muted": replace(BASE, muted=True),
        "charging": replace(BASE, charging=True),
        "jarvis_foreground": replace(BASE, jarvis_foreground=True),
        "active": replace(BASE, active=False),
        "battery": replace(BASE, battery=60),
    }
    assert sorted(variants) == sorted(TRACKED), "TRACKED and the variants disagree"
    for name, after in variants.items():
        assert meaningful_change(BASE, after) == name, name


def test_a_flapping_signal_cannot_become_a_firehose():
    """Twenty flips inside the floor must produce exactly one frame."""
    sent, last_sent, elapsed = 0, BASE, 0
    for index in range(20):
        elapsed += 50  # 50 ms per flip
        candidate = replace(BASE, locked=index % 2 == 0)
        if should_send(last_sent, candidate, elapsed) is not None:
            sent += 1
            last_sent = candidate
            elapsed = 0
    assert sent <= 1, f"a flapping lock produced {sent} frames"


def test_a_realistic_hour_stays_cheap():
    """An hour of ordinary use: a heartbeat a minute plus a handful of edges."""
    now = 1_000_000
    last_sent: Signals | None = None
    elapsed = 0
    sent = 0
    state = BASE
    for tick in range(3600 * 1000 // POLL_INTERVAL_MS):
        now += POLL_INTERVAL_MS
        # The battery drains a point every four minutes; nothing else moves.
        if tick % (240_000 // POLL_INTERVAL_MS) == 0 and state.battery:
            state = replace(state, battery=state.battery - 1)
        elapsed = 0 if last_sent is None else elapsed + POLL_INTERVAL_MS
        if should_send(last_sent, state, elapsed) is not None:
            sent += 1
            last_sent = state
            elapsed = 0
    # 60 heartbeats, plus a battery frame every fifth step. Anything near the
    # 720 polls in an hour would mean the throttle is not doing its job.
    assert 55 <= sent <= 75, f"{sent} frames in an hour"


def test_a_dropped_frame_leaves_the_change_pending():
    """The comparison is against the last SENT snapshot, never the last sample."""
    last_sent = BASE
    locked = replace(BASE, locked=True)
    # The emit fails, so last_sent is NOT advanced.
    assert should_send(last_sent, locked, 5_000) == "locked"
    assert should_send(last_sent, locked, 10_000) == "locked"


# --- the ask-flow state machine, mirrored from CompanionLedger --------------

STATUS_ANSWERED = "answered"
STATUS_DISMISSED = "dismissed"
STATUS_TIMEOUT = "timeout"
STATUS_UNDELIVERABLE = "undeliverable"
STATUSES = [STATUS_ANSWERED, STATUS_DISMISSED, STATUS_TIMEOUT, STATUS_UNDELIVERABLE]

#: Everything except `answered` makes the server try the next device.
ESCALATES = {STATUS_DISMISSED, STATUS_TIMEOUT, STATUS_UNDELIVERABLE}

MODES = ["speak", "ask", "notify"]
KIND_TO_MODE = {"say": "speak", "ask": "ask", "notify": "notify"}
SENSITIVE_IMPORTANCE = {"high", "critical"}

FRESH, IN_FLIGHT, SETTLED = "fresh", "in_flight", "settled"

MAX_REMEMBERED = 256


class Ledger:
    """Mirror of CompanionLedger."""

    def __init__(self, max_remembered: int = MAX_REMEMBERED) -> None:
        self.max_remembered = max(1, max_remembered)
        self.in_flight: list[str] = []
        self.settled: dict[str, tuple[str, str]] = {}

    def admit(self, message_id: str):
        mid = message_id.strip()
        if not mid:
            return (IN_FLIGHT, None)
        if mid in self.settled:
            return (SETTLED, self.settled[mid])
        if mid in self.in_flight:
            return (IN_FLIGHT, None)
        self.in_flight.append(mid)
        return (FRESH, None)

    def settle(self, message_id: str, status: str, reply: str) -> bool:
        mid = message_id.strip()
        if not mid or mid in self.settled:
            return False
        if mid in self.in_flight:
            self.in_flight.remove(mid)
        self.settled[mid] = (status, reply)
        while len(self.settled) > self.max_remembered:
            self.settled.pop(next(iter(self.settled)))
        return True

    def abandon(self, message_id: str) -> None:
        mid = message_id.strip()
        if mid in self.in_flight:
            self.in_flight.remove(mid)

    def status_of(self, message_id: str) -> str | None:
        entry = self.settled.get(message_id.strip())
        return entry[0] if entry else None


class Device:
    """Mirror of CompanionMessageHandler: what actually reaches the socket."""

    def __init__(self, *, can_notify: bool = True, can_show: bool = True,
                 can_speak: bool = False, max_remembered: int = MAX_REMEMBERED) -> None:
        self.ledger = Ledger(max_remembered)
        self.can_notify = can_notify
        self.can_show = can_show
        self.can_speak = can_speak
        self.sent: list[tuple[str, str, str | None]] = []
        self.prompts: list[str] = []
        self.spoken: list[str] = []
        self.notifications: list[str] = []

    # --- outbound ---------------------------------------------------------
    def _reply(self, message_id: str, status: str, answer: str | None = None) -> None:
        status = status if status in STATUSES else STATUS_UNDELIVERABLE
        frame = _result(message_id, status, answer)
        if not self.ledger.settle(message_id, status, frame):
            return
        self.sent.append((message_id, status, answer if status == STATUS_ANSWERED else None))

    def _transmit(self, frame: str) -> None:
        message_id, status, answer = _parse_result(frame)
        self.sent.append((message_id, status, answer))

    # --- inbound ----------------------------------------------------------
    def handle(self, message: dict) -> None:
        parsed = parse_message(message)
        if parsed is None:
            return
        kind, replay = self.ledger.admit(parsed["message_id"])
        if kind == SETTLED:
            # A redelivery replays the stored reply verbatim and prompts nobody.
            self._transmit(replay[1])
            return
        if kind == IN_FLIGHT:
            return

        mode = parsed["mode"]
        if mode == "ask":
            if not self.can_show and not self.can_notify:
                self._reply(parsed["message_id"], STATUS_UNDELIVERABLE)
                return
            self.prompts.append(parsed["message_id"])
            return  # waits for answer / dismiss / timeout
        if mode == "speak":
            if self.can_speak:
                self.spoken.append(parsed["text"])
                self._reply(parsed["message_id"], STATUS_ANSWERED, "")
                return
            self._notify(parsed)
            return
        if mode == "notify":
            self._notify(parsed)
            return
        self._reply(parsed["message_id"], STATUS_UNDELIVERABLE)

    def _notify(self, parsed: dict) -> None:
        if self.can_notify:
            self.notifications.append(parsed["text"])
            self._reply(parsed["message_id"], STATUS_ANSWERED, "")
        else:
            self._reply(parsed["message_id"], STATUS_UNDELIVERABLE)

    # --- what the UI reports back ----------------------------------------
    def answer(self, message_id: str, text: str) -> None:
        self._reply(message_id, STATUS_ANSWERED, text)

    def dismiss(self, message_id: str) -> None:
        self._reply(message_id, STATUS_DISMISSED)

    def timeout(self, message_id: str) -> None:
        self._reply(message_id, STATUS_TIMEOUT)

    def destroyed_without_answering(self, message_id: str) -> None:
        """Swiped away, killed, config-changed out of existence."""
        self._reply(message_id, STATUS_DISMISSED)

    def report_unknown(self, message_id: str) -> None:
        self._reply(message_id, STATUS_UNDELIVERABLE)

    def replies_for(self, message_id: str) -> list[tuple[str, str, str | None]]:
        return [row for row in self.sent if row[0] == message_id]


def _result(message_id: str, status: str, answer: str | None) -> str:
    if status == STATUS_ANSWERED:
        return f"{message_id}|{status}|{answer or ''}"
    return f"{message_id}|{status}|"


def _parse_result(frame: str) -> tuple[str, str, str | None]:
    message_id, status, answer = frame.split("|", 2)
    return message_id, status, answer if status == STATUS_ANSWERED else None


def parse_message(msg: dict) -> dict | None:
    """Mirror of CompanionProtocol.parse."""
    if msg.get("type") != "jarvis_message":
        return None
    message_id = str(msg.get("message_id") or "").strip()[:128]
    if not message_id:
        return None
    kind = str(msg.get("kind") or "").strip().lower()
    mode = str(msg.get("mode") or "").strip().lower()
    if mode not in MODES:
        mode = KIND_TO_MODE.get(kind, "")
    importance = str(msg.get("importance") or "").strip().lower()
    if importance not in ("low", "normal", "high", "critical"):
        importance = "normal"
    options = []
    raw = msg.get("options")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (dict, list)):
                continue
            text = str(item).strip().replace("\n", " ")[:80]
            if text and text not in options:
                options.append(text)
            if len(options) >= 8:
                break
    return {
        "message_id": message_id,
        "kind": kind or "notify",
        "mode": mode,
        "text": str(msg.get("text") or "")[:4000],
        "options": options,
        "importance": importance,
        "timeout_ms": clamp_timeout(msg.get("timeout_s"),
                                    120_000 if mode == "ask" else 30_000),
    }


def clamp_timeout(raw, default: int) -> int:
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return default
    if seconds != seconds or seconds <= 0:
        return default
    return max(5_000, min(600_000, int(seconds * 1000)))


def ask(message_id: str = "a1b2c3", **overrides) -> dict:
    frame = {
        "type": "jarvis_message",
        "message_id": message_id,
        "kind": "ask",
        "mode": "ask",
        "text": "Deploy to production?",
        "options": ["yes", "no"],
        "conversation_id": "conv-7",
        "importance": "high",
        "timeout_s": 120,
    }
    frame.update(overrides)
    return frame


# --- the state machine: every ending, exactly one reply ---------------------


def test_delivered_then_answered():
    device = Device()
    device.handle(ask())
    assert device.prompts == ["a1b2c3"]
    assert device.sent == []
    device.answer("a1b2c3", "no")
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "no")]


def test_delivered_then_dismissed():
    device = Device()
    device.handle(ask())
    device.dismiss("a1b2c3")
    assert device.sent == [("a1b2c3", STATUS_DISMISSED, None)]


def test_delivered_then_timed_out():
    device = Device()
    device.handle(ask())
    device.timeout("a1b2c3")
    assert device.sent == [("a1b2c3", STATUS_TIMEOUT, None)]


def test_being_destroyed_without_a_choice_is_a_dismissal():
    device = Device()
    device.handle(ask())
    device.destroyed_without_answering("a1b2c3")
    assert device.sent == [("a1b2c3", STATUS_DISMISSED, None)]


def test_no_way_to_show_the_question_is_undeliverable():
    device = Device(can_show=False, can_notify=False)
    device.handle(ask())
    assert device.prompts == []
    assert device.sent == [("a1b2c3", STATUS_UNDELIVERABLE, None)]


def test_every_ending_is_reported():
    """Whatever happens, the server hears about it. Silence is not an ending."""
    endings = {
        "answered": lambda d: d.answer("a1b2c3", "yes"),
        "dismissed": lambda d: d.dismiss("a1b2c3"),
        "timeout": lambda d: d.timeout("a1b2c3"),
        "destroyed": lambda d: d.destroyed_without_answering("a1b2c3"),
    }
    for name, ending in endings.items():
        device = Device()
        device.handle(ask())
        ending(device)
        assert len(device.sent) == 1, f"{name} produced {len(device.sent)} replies"
        assert device.sent[0][1] in STATUSES, name


def test_only_answered_stops_the_escalation():
    for status in STATUSES:
        escalates = status in ESCALATES
        assert escalates == (status != STATUS_ANSWERED), status


# --- exactly once -----------------------------------------------------------


def test_a_second_ending_after_the_first_is_refused():
    """A countdown that fires just after a tap must not send a second reply."""
    for first, second in product(
        ["answer", "dismiss", "timeout", "destroyed"], repeat=2
    ):
        device = Device()
        device.handle(ask())
        _apply(device, first)
        _apply(device, second)
        assert len(device.sent) == 1, f"{first} then {second} produced {device.sent}"
        assert device.sent[0][1] == _status_of(first)


def _apply(device: Device, name: str) -> None:
    {
        "answer": lambda: device.answer("a1b2c3", "yes"),
        "dismiss": lambda: device.dismiss("a1b2c3"),
        "timeout": lambda: device.timeout("a1b2c3"),
        "destroyed": lambda: device.destroyed_without_answering("a1b2c3"),
    }[name]()


def _status_of(name: str) -> str:
    return {
        "answer": STATUS_ANSWERED,
        "dismiss": STATUS_DISMISSED,
        "timeout": STATUS_TIMEOUT,
        "destroyed": STATUS_DISMISSED,
    }[name]


def test_a_redelivery_while_the_question_is_on_screen_changes_nothing():
    device = Device()
    device.handle(ask())
    device.handle(ask())
    device.handle(ask())
    assert device.prompts == ["a1b2c3"], "the human must be asked once"
    assert device.sent == []
    device.answer("a1b2c3", "yes")
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "yes")]


def test_a_redelivery_after_the_answer_replays_the_same_reply():
    device = Device()
    device.handle(ask())
    device.answer("a1b2c3", "no")
    device.handle(ask())
    device.handle(ask())
    assert device.prompts == ["a1b2c3"], "a redelivery must not ask again"
    assert device.sent == [
        ("a1b2c3", STATUS_ANSWERED, "no"),
        ("a1b2c3", STATUS_ANSWERED, "no"),
        ("a1b2c3", STATUS_ANSWERED, "no"),
    ]
    assert len({row for row in device.sent}) == 1, "every replay must be identical"


def test_a_redelivery_after_a_dismissal_replays_the_dismissal():
    device = Device()
    device.handle(ask())
    device.dismiss("a1b2c3")
    device.handle(ask())
    assert device.prompts == ["a1b2c3"]
    assert {row[1] for row in device.sent} == {STATUS_DISMISSED}


def test_different_ids_are_independent():
    device = Device()
    device.handle(ask("m1"))
    device.handle(ask("m2"))
    device.answer("m2", "later")
    device.dismiss("m1")
    assert device.replies_for("m1") == [("m1", STATUS_DISMISSED, None)]
    assert device.replies_for("m2") == [("m2", STATUS_ANSWERED, "later")]


def test_an_id_nobody_is_waiting_for_is_undeliverable_once():
    """The activity restored after process death: the ledger is empty, so the
    only honest answer is "not here" — and it is sent once."""
    device = Device()
    device.report_unknown("ghost")
    device.report_unknown("ghost")
    assert device.sent == [("ghost", STATUS_UNDELIVERABLE, None)]


def test_report_unknown_cannot_overwrite_a_real_answer():
    device = Device()
    device.handle(ask())
    device.answer("a1b2c3", "yes")
    device.report_unknown("a1b2c3")
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "yes")]


def test_abandoning_leaves_the_id_askable_again():
    """A socket that dies mid-question reported nothing, so the redelivery that
    follows the reconnect is free to ask."""
    device = Device()
    device.handle(ask())
    device.ledger.abandon("a1b2c3")
    device.handle(ask())
    assert device.prompts == ["a1b2c3", "a1b2c3"]
    assert device.sent == []


def test_the_ledger_is_bounded():
    device = Device(max_remembered=4)
    for index in range(20):
        device.handle(ask(f"m{index}"))
        device.answer(f"m{index}", "ok")
    assert len(device.ledger.settled) == 4
    assert device.ledger.status_of("m19") == STATUS_ANSWERED
    assert device.ledger.status_of("m0") is None


def test_a_message_with_no_id_answers_nothing():
    device = Device()
    device.handle(ask(message_id=""))
    device.handle({"type": "jarvis_message"})
    device.handle({"type": "device_command", "message_id": "x"})
    assert device.sent == [] and device.prompts == []


# --- routing per mode -------------------------------------------------------


def test_notify_is_shown_and_reported():
    device = Device()
    device.handle(ask(kind="notify", mode="notify", text="Backup finished."))
    assert device.notifications == ["Backup finished."]
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "")]


def test_notify_that_cannot_be_shown_is_undeliverable():
    device = Device(can_notify=False)
    device.handle(ask(kind="notify", mode="notify"))
    assert device.sent == [("a1b2c3", STATUS_UNDELIVERABLE, None)]


def test_speak_in_the_foreground_uses_the_orb():
    device = Device(can_speak=True)
    device.handle(ask(kind="say", mode="speak", text="The build failed."))
    assert device.spoken == ["The build failed."]
    assert device.notifications == []
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "")]


def test_speak_in_the_background_downgrades_to_a_notification():
    device = Device(can_speak=False)
    device.handle(ask(kind="say", mode="speak", text="The build failed."))
    assert device.spoken == []
    assert device.notifications == ["The build failed."]
    assert device.sent == [("a1b2c3", STATUS_ANSWERED, "")]


def test_speak_with_nowhere_to_land_is_undeliverable():
    device = Device(can_speak=False, can_notify=False)
    device.handle(ask(kind="say", mode="speak"))
    assert device.sent == [("a1b2c3", STATUS_UNDELIVERABLE, None)]


def test_an_unknown_mode_is_undeliverable_rather_than_guessed_at():
    device = Device()
    device.handle(ask(mode="teleport", kind="teleport"))
    assert device.sent == [("a1b2c3", STATUS_UNDELIVERABLE, None)]
    assert device.prompts == [] and device.notifications == []


def test_a_garbled_mode_falls_back_to_the_kind():
    assert parse_message(ask(mode="SHOUT", kind="say"))["mode"] == "speak"
    assert parse_message(ask(mode="", kind="notify"))["mode"] == "notify"
    assert parse_message(ask(mode="", kind="ask"))["mode"] == "ask"
    assert parse_message(ask(mode="?", kind="?"))["mode"] == ""


# --- parsing clamps ---------------------------------------------------------


def test_the_parser_clamps_every_hostile_edge():
    parsed = parse_message(
        ask(
            text="x" * 99_999,
            options=["a", "a", "b" * 500] + [f"o{i}" for i in range(50)] + [{}, []],
            timeout_s=99_999,
            importance="ULTRA",
        )
    )
    assert len(parsed["text"]) == 4000
    assert len(parsed["options"]) == 8
    assert all(len(o) <= 80 for o in parsed["options"])
    assert parsed["options"].count("a") == 1
    assert parsed["timeout_ms"] == 600_000
    assert parsed["importance"] == "normal"


def test_the_timeout_is_clamped_never_trusted():
    table = [
        (None, 120_000), ("nonsense", 120_000), (0, 120_000), (-30, 120_000),
        (float("nan"), 120_000), (1, 5_000), (120, 120_000), (10_000, 600_000),
    ]
    for raw, expected in table:
        assert parse_message(ask(timeout_s=raw))["timeout_ms"] == expected, raw


def test_the_parser_has_no_slot_for_an_action():
    """A proactive message is information and questions only."""
    parsed = parse_message(
        ask(skip_confirmation=True, policy="allow", tier=1, action="send_sms",
            params={"to": "+441234567890"})
    )
    for forbidden in ("action", "params", "tier", "policy", "skip_confirmation"):
        assert forbidden not in parsed, forbidden


# --- the keyguard gate, mirrored from CompanionAskGate ----------------------


def sensitive(importance: str) -> bool:
    return importance.strip().lower() in SENSITIVE_IMPORTANCE


def text_visible(locked: bool, importance: str) -> bool:
    return not locked or not sensitive(importance)


def answer_enabled(locked: bool, armed: bool, answered: bool, importance: str) -> bool:
    return (not locked) and armed and (not answered) and text_visible(locked, importance)


def dismiss_enabled(answered: bool) -> bool:
    return not answered


def test_a_sensitive_question_never_renders_over_the_keyguard():
    for importance in ("high", "critical"):
        assert text_visible(True, importance) is False
        assert text_visible(False, importance) is True
    for importance in ("low", "normal"):
        assert text_visible(True, importance) is True


def test_answering_always_needs_an_unlocked_phone():
    for locked, armed, answered, importance in product(
        [True, False], [True, False], [True, False], ["low", "normal", "high", "critical"]
    ):
        allowed = answer_enabled(locked, armed, answered, importance)
        if allowed:
            assert not locked and armed and not answered, (
                f"answering was allowed with locked={locked} armed={armed} "
                f"answered={answered}"
            )


def test_dismissing_is_live_from_a_locked_screen():
    """Refusing is safe from anywhere; it escalates rather than destroying
    anything."""
    for locked, armed, importance in product(
        [True, False], [True, False], ["low", "normal", "high", "critical"]
    ):
        assert dismiss_enabled(False) is True
        assert dismiss_enabled(True) is False


def test_an_unarmed_screen_cannot_be_answered():
    """A tap already in flight when the screen appears must not land."""
    assert answer_enabled(False, False, False, "normal") is False
    assert answer_enabled(False, True, False, "normal") is True


# --- structural drift checks against the Kotlin -----------------------------


def _flat(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def test_kotlin_presence_constants_match():
    src = _flat(KOTLIN_PRESENCE)
    expected = {
        "HEARTBEAT_MS": HEARTBEAT_MS,
        "MIN_INTERVAL_MS": MIN_INTERVAL_MS,
        "POLL_INTERVAL_MS": POLL_INTERVAL_MS,
        "ACTIVE_WITHIN_MS": ACTIVE_WITHIN_MS,
    }
    for name, value in expected.items():
        match = re.search(rf"const val {name} = ([0-9_]+)L", src)
        assert match, f"PresenceReporter.kt no longer defines {name}"
        assert int(match.group(1).replace("_", "")) == value, name
    battery = re.search(r"const val BATTERY_STEP = (\d+)", src)
    assert battery and int(battery.group(1)) == BATTERY_STEP


def test_kotlin_throttle_still_encodes_the_order():
    src = _flat(KOTLIN_PRESENCE)
    required = [
        'if (before == null) return "first report"',
        'if (sinceSentMs >= heartbeatMs) return "heartbeat"',
        "if (sinceSentMs < minIntervalMs) return null",
        "return meaningfulChange(before, after)",
        'if (before.screenOn != after.screenOn) return "screen_on"',
        'if (before.locked != after.locked) return "locked"',
        'if (before.driving != after.driving) return "driving"',
        'if (before.active != after.active) return "active"',
        'if (batteryMoved(before.battery, after.battery)) return "battery"',
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"PresenceReporter.kt lost: {needle}"


def test_kotlin_tracked_list_matches():
    src = KOTLIN_PRESENCE.read_text(encoding="utf-8")
    body = src.split("val TRACKED = listOf(", 1)[1].split(")", 1)[0]
    names = re.findall(r'"([a-z_]+)"', body)
    assert names == TRACKED, f"TRACKED is {names}, expected {TRACKED}"


def test_kotlin_presence_payload_uses_the_servers_field_names():
    src = _flat(KOTLIN_PRESENCE)
    for key in (
        "screen_on", "locked", "driving", "audio_available", "muted",
        "last_interaction", "battery", "charging", "zone",
    ):
        assert f'"{key}"' in src, f"the presence payload lost {key}"
    assert 'const val EVENT_PRESENCE = "presence"' in src


def test_kotlin_presence_never_reports_a_dropped_frame_as_sent():
    src = _flat(KOTLIN_PRESENCE)
    assert "if (!delivered) {" in src
    assert re.sub(r"\s+", " ", "lastSent = signals lastSentAt = clock()") in src


def test_kotlin_protocol_constants_match():
    src = _flat(KOTLIN_PROTOCOL)
    for status in STATUSES:
        assert f'"{status}"' in src, status
    for mode in MODES:
        assert f'"{mode}"' in src, mode
    assert 'const val TYPE_MESSAGE = "jarvis_message"' in src
    assert 'const val TYPE_RESULT = "jarvis_message_result"' in src
    # Only `answered` carries an answer.
    assert re.sub(
        r"\s+", " ",
        'if (clean == STATUS_ANSWERED) out.put("answer", (answer ?: "").take(MAX_TEXT))',
    ) in src
    # An unrecognised status must never read as answered.
    assert "?: STATUS_UNDELIVERABLE" in src


def test_kotlin_protocol_has_no_action_shaped_field():
    """The struct is the guarantee: no slot means nothing to talk it into."""
    src = KOTLIN_PROTOCOL.read_text(encoding="utf-8")
    body = src.split("data class Message(", 1)[1].split("\n    }", 1)[0]
    for forbidden in ("action", "params", "tier", "policy", "command"):
        assert not re.search(rf"\bval {forbidden}\b", body), forbidden


def test_kotlin_ledger_still_answers_once():
    src = _flat(KOTLIN_LEDGER)
    required = [
        "settled[id]?.let { return Admission.Settled(it.status, it.reply) }",
        "if (!inFlight.add(id)) return Admission.InFlight",
        "if (settled.containsKey(id)) return false",
        "fun abandon(messageId: String)",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"CompanionLedger.kt lost: {needle}"
    match = re.search(r"const val DEFAULT_MAX_REMEMBERED = (\d+)", src)
    assert match and int(match.group(1)) == MAX_REMEMBERED


def test_kotlin_handler_reports_every_failure():
    src = _flat(KOTLIN_HANDLER)
    required = [
        "is CompanionLedger.Admission.Settled ->",
        "CompanionLedger.Admission.InFlight ->",
        "STATUS_UNDELIVERABLE",
        "STATUS_TIMEOUT",
        "fun reportUndeliverable(",
        "private fun armWatchdog(",
    ]
    for needle in required:
        assert needle in src, f"CompanionMessageHandler.kt lost: {needle}"
    # The watchdog has to outlast the question's own countdown, or it would
    # answer `timeout` while the user is still reading.
    match = re.search(r"const val WATCHDOG_GRACE_MS = ([0-9_]+)L", src)
    assert match and int(match.group(1).replace("_", "")) > 0


def test_kotlin_handler_cannot_reach_the_action_layer():
    """Structural, not stylistic: a proactive message must not be able to run
    anything, and the cheapest guarantee is having no import."""
    for path in (KOTLIN_HANDLER, KOTLIN_PROTOCOL, KOTLIN_LEDGER, KOTLIN_GATE,
                 KOTLIN_ACTIVITY):
        src = path.read_text(encoding="utf-8")
        for forbidden in (
            "automation.actions",
            "ActionRegistry",
            "BridgeDispatcher",
            "AutomationBridge",
            "PolicyEngine",
        ):
            assert forbidden not in src, f"{path.name} must not reach {forbidden}"


def test_kotlin_gate_still_hides_a_sensitive_question():
    src = _flat(KOTLIN_GATE)
    required = [
        "fun textVisible(locked: Boolean, importance: String): Boolean = !locked || !sensitive(importance)",
        "!textVisible(locked, importance) -> HIDDEN_TEXT",
        "): Boolean = !locked && armed && !answered && textVisible(locked, importance)",
        "fun dismissEnabled(answered: Boolean): Boolean = !answered",
        "const val IMPLICIT_STATUS = CompanionProtocol.STATUS_DISMISSED",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"CompanionAskGate.kt lost: {needle}"


def test_kotlin_activity_fails_towards_dismissed():
    src = _flat(KOTLIN_ACTIVITY)
    assert "override fun onDestroy()" in src
    assert re.sub(r"\s+", " ", "if (!answered && mode == CompanionProtocol.MODE_ASK)") in src
    assert "CompanionAskGate.IMPLICIT_STATUS" in src
    assert "setShowWhenLocked(true)" in src
    assert "WindowManager.LayoutParams.FLAG_SECURE" in src
    assert "filterTouchesWhenObscured = true" in src


def test_kotlin_spoken_answers_never_reach_the_conversation_agent():
    """The whole point of the companion STT client: an answer to a question is
    transcribed, not executed."""
    src = _flat(KOTLIN_VOICE)
    assert re.sub(r"\s+", " ", 'run.put("start_stage", "stt") .put("end_stage", "stt")') in src
    assert re.sub(r"\s+", " ", 'run.put("start_stage", "tts") .put("end_stage", "tts")') in src
    assert '"conversation/process"' not in src
    assert '"intent"' not in src


# --- runner -----------------------------------------------------------------


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(THROTTLE_TABLE)} throttle rows, "
        f"{len(STATUSES) ** 2} ending orderings, "
        f"{2 * 2 * 2 * 4} keyguard-gate combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
