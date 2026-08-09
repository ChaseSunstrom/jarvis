"""Presence tracking — deciding WHICH device to reach the user on.

Every connected client (phone, desktop, satellite) reports lightweight signals;
this module turns them into a ranked answer to "if Jarvis needs to say
something right now, where does it land?".

Pure logic: no I/O, no asyncio, no framework. That makes the routing decision
testable and auditable, which matters because a wrong answer here means Jarvis
either talks to an empty room or interrupts you in a meeting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterable


class Reach(IntEnum):
    """How likely the user is to see/hear something on this device, now."""

    ABSENT = 0      # no recent signal at all
    BACKGROUND = 1  # connected, but screen off / long idle
    IDLE = 2        # screen on-ish or recently used
    PRESENT = 3     # screen on and unlocked
    ACTIVE = 4      # interacted within the last couple of minutes


# Signals older than this tell us nothing.
STALE_AFTER = 15 * 60.0
ACTIVE_WITHIN = 120.0
RECENT_WITHIN = 10 * 60.0


@dataclass(slots=True)
class DevicePresence:
    device_id: str
    name: str
    platform: str  # android | desktop | web | satellite
    capabilities: list[str] = field(default_factory=list)

    last_seen: float = 0.0           # any contact (heartbeat)
    last_interaction: float = 0.0    # the user actually did something
    screen_on: bool = False
    locked: bool = True
    jarvis_foreground: bool = False
    audio_available: bool = True     # can speak out loud here
    driving: bool = False
    zone: str | None = None          # home | away | work | ...
    battery: int | None = None
    charging: bool | None = None
    muted: bool = False              # user asked for quiet on this device
    connected: bool = False

    def update(self, **signals: Any) -> None:
        for key, value in signals.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)
        self.last_seen = signals.get("last_seen") or time.time()

    def reach(self, now: float | None = None) -> Reach:
        now = now if now is not None else time.time()
        if not self.connected or not self.last_seen:
            return Reach.ABSENT
        if now - self.last_seen > STALE_AFTER:
            return Reach.ABSENT
        if self.last_interaction and now - self.last_interaction <= ACTIVE_WITHIN:
            return Reach.ACTIVE
        if self.screen_on and not self.locked:
            return Reach.PRESENT
        if self.screen_on or (
            self.last_interaction and now - self.last_interaction <= RECENT_WITHIN
        ):
            return Reach.IDLE
        return Reach.BACKGROUND

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "reach": int(self.reach()),
            "reach_name": self.reach().name,
            "screen_on": self.screen_on,
            "locked": self.locked,
            "driving": self.driving,
            "zone": self.zone,
            "muted": self.muted,
            "connected": self.connected,
            "last_interaction": self.last_interaction,
        }


# What a message needs from a device.
NEEDS_ANSWER = "ask"      # the user must be able to respond
NEEDS_SPEECH = "speak"    # must be audible
NEEDS_VISUAL = "notify"   # a notification is enough


@dataclass(slots=True)
class Delivery:
    """The routing decision: where, how, and what to fall back to."""

    device_id: str | None
    mode: str            # speak | ask | notify | queue
    reason: str
    fallbacks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "mode": self.mode,
            "reason": self.reason,
            "fallbacks": list(self.fallbacks),
        }


class PresenceRegistry:
    def __init__(self) -> None:
        self.devices: dict[str, DevicePresence] = {}

    # --- bookkeeping ------------------------------------------------------
    def register(
        self, device_id: str, name: str, platform: str, capabilities: Iterable[str] = ()
    ) -> DevicePresence:
        device = self.devices.get(device_id)
        if device is None:
            device = DevicePresence(device_id, name, platform, list(capabilities))
            self.devices[device_id] = device
        else:
            device.name = name or device.name
            device.platform = platform or device.platform
            device.capabilities = list(capabilities) or device.capabilities
        device.connected = True
        device.last_seen = time.time()
        return device

    def disconnect(self, device_id: str) -> None:
        device = self.devices.get(device_id)
        if device:
            device.connected = False

    def update(self, device_id: str, **signals: Any) -> DevicePresence | None:
        device = self.devices.get(device_id)
        if device is None:
            return None
        device.update(**signals)
        return device

    def touch_interaction(self, device_id: str) -> None:
        """The user just spoke/typed here — this is the strongest presence signal."""
        device = self.devices.get(device_id)
        if device:
            now = time.time()
            device.last_interaction = now
            device.last_seen = now

    def all(self) -> list[DevicePresence]:
        return list(self.devices.values())

    # --- the decision -----------------------------------------------------
    def rank(self, need: str = NEEDS_VISUAL, now: float | None = None) -> list[DevicePresence]:
        """Best-first list of devices able to satisfy `need`."""
        now = now if now is not None else time.time()

        def usable(d: DevicePresence) -> bool:
            if not d.connected or d.reach(now) is Reach.ABSENT:
                return False
            # A question is useless on a device the user can't answer on.
            if need is NEEDS_ANSWER and d.reach(now) < Reach.IDLE:
                return False
            return True

        def key(d: DevicePresence) -> tuple:
            # Driving wins outright for anything audible (hands/eyes are busy).
            driving_bonus = 1 if (d.driving and need is not NEEDS_VISUAL) else 0
            # Muted / silent devices are a last resort rather than excluded —
            # if it is the only device you have, a quiet notification still
            # beats losing the message. _mode_for() downgrades speech to notify.
            quiet_penalty = 0 if d.muted else 1
            can_speak = 1 if (d.audio_available or need is not NEEDS_SPEECH) else 0
            return (
                driving_bonus,
                quiet_penalty,
                can_speak,
                int(d.reach(now)),
                1 if d.jarvis_foreground else 0,
                d.last_interaction,
            )

        return sorted((d for d in self.devices.values() if usable(d)), key=key, reverse=True)

    def route(
        self,
        need: str = NEEDS_VISUAL,
        importance: str = "normal",
        prefer_device: str | None = None,
        now: float | None = None,
    ) -> Delivery:
        """Pick a device and a delivery mode.

        Policy, in order:
          * an explicitly requested device wins if it can serve the need;
          * driving -> speak, never visual-only (eyes stay on the road);
          * a question needs a device the user can actually answer on;
          * otherwise the most-present device; speech only where it is
            audible and not muted;
          * nothing reachable -> queue it (delivered when a device returns),
            unless it is critical, in which case notify the best-known device
            anyway so it is waiting for them.
        """
        now = now if now is not None else time.time()
        ranked = self.rank(need, now)

        if prefer_device:
            preferred = next((d for d in ranked if d.device_id == prefer_device), None)
            if preferred is not None:
                return Delivery(
                    preferred.device_id,
                    _mode_for(need, preferred),
                    "requested device",
                    [d.device_id for d in ranked if d.device_id != preferred.device_id],
                )

        if not ranked:
            if importance == "critical" and self.devices:
                # Nothing is reachable; leave it on the most recently seen device.
                #
                # The mode still comes from _mode_for(): this branch decides
                # WHERE a critical message waits, never WHAT it is. Hard-coding
                # "notify" here used to turn a question into a notification, and
                # a device that is told "just notify" acknowledges delivery —
                # which the manager reads as an answer, resolves the waiting
                # `companion.ask` with nothing, and stops escalating. A question
                # stays a question wherever it lands.
                best = max(self.devices.values(), key=lambda d: d.last_seen)
                return Delivery(
                    best.device_id,
                    _mode_for(need, best),
                    "critical, no device reachable",
                    [],
                )
            return Delivery(None, "queue", "no reachable device", [])

        chosen = ranked[0]
        if chosen.driving and need is not NEEDS_VISUAL:
            reason = "user is driving"
        elif chosen.reach(now) is Reach.ACTIVE:
            reason = "most recently used device"
        else:
            reason = f"best available ({chosen.reach(now).name.lower()})"
        return Delivery(
            chosen.device_id,
            _mode_for(need, chosen),
            reason,
            [d.device_id for d in ranked[1:]],
        )


def _mode_for(need: str, device: DevicePresence) -> str:
    if need == NEEDS_ANSWER:
        return "ask"
    if need == NEEDS_SPEECH:
        return "speak" if device.audio_available and not device.muted else "notify"
    # A plain message is spoken only when the user is clearly there and it is
    # audible; otherwise it is left as a notification.
    if (
        device.audio_available
        and not device.muted
        and (device.driving or device.reach() is Reach.ACTIVE)
    ):
        return "speak"
    return "notify"
