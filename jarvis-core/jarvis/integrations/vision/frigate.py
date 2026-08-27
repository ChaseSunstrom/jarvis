"""Frigate's events, as moments.

`vision.look` answers a question; it does not watch. Frigate does — motion,
a detector, a tracker — and publishes "a person entered the porch" on
``frigate/events`` as it happens. This module turns the first message about
each event into a record in `notifications` (kind ``camera``), which is what
the console, the phone and "what did you tell me earlier" read.

What it deliberately does **not** do:

* **Look.** Turning an event into a `vision.look` is an automation's job
  (`docs/vision.md` has the YAML), because that is where consent is decided
  and audited. This listener never fetches a frame and never calls the model.
* **Trust the payload.** Every string in it — camera, label, zone,
  `sub_label` — came off an MQTT topic anything on the broker can publish
  to, and `sub_label` can be typed into Frigate's UI by hand. Only the camera,
  the label and the zones reach the record, each reduced to a plain
  identifier and clipped; the title is built from a fixed sentence around
  them; nothing here is ever an instruction to anything.
* **Repeat itself.** One moment per event id, and at most one per camera and
  label inside ``debounce`` seconds — a porch that fires twenty ``update``
  messages while somebody stands on it is one thing happening, not twenty.

```yaml
vision:
  frigate:
    mqtt: true                   # off unless asked for
    topic: frigate/events        # Frigate's default topic_prefix
    debounce: 30                 # seconds, per camera+label
    labels: [person, package]    # optional: only these; empty means every label
    cameras: [front_door]        # optional: only these
```
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOPIC = "frigate/events"
DEFAULT_DEBOUNCE = 30.0
#: How many event ids are remembered. Frigate's ids are `<epoch>-<random>`;
#: a house sees a few hundred a day, and the oldest can be forgotten because
#: a repeat of one that old is already outside every debounce window.
MAX_REMEMBERED_IDS = 1000

#: The notification kind, and the bus event named as its `source` so "why am
#: I seeing this" has the answer that is a fact rather than a sentence.
KIND = "camera"
SOURCE = "frigate_event"

_IDENTIFIER = re.compile(r"[^a-z0-9_ -]+")


def _clean(value: Any, limit: int = 40) -> str:
    """A payload string reduced to a plain lowercase identifier.

    The characters left are letters, digits, space, `_` and `-` — enough for
    `front_door`, `person`, `driveway-1` and nothing an NVR would put in a
    camera name that a notification should ever carry. A fence marker, a
    control literal, an emoji, a newline: gone before it is looked at.
    """
    text = " ".join(str(value or "").split()).lower()
    text = _IDENTIFIER.sub("", text).strip()
    return text[:limit]


def _title_case(identifier: str) -> str:
    return " ".join(part.capitalize() for part in identifier.replace("_", " ").split()) or ""


@dataclass(frozen=True)
class FrigateConfig:
    enabled: bool = False
    topic: str = DEFAULT_TOPIC
    debounce: float = DEFAULT_DEBOUNCE
    labels: tuple[str, ...] = ()
    cameras: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, options: Any) -> "FrigateConfig":
        if not isinstance(options, dict):
            return cls()
        enabled = options.get("mqtt", options.get("enabled", False))
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("true", "yes", "on", "1")
        try:
            debounce = max(0.0, float(options.get("debounce", DEFAULT_DEBOUNCE)))
        except (TypeError, ValueError):
            debounce = DEFAULT_DEBOUNCE

        def _names(key: str) -> tuple[str, ...]:
            raw = options.get(key)
            if isinstance(raw, str):
                raw = [raw]
            return tuple(n for n in (_clean(v) for v in (raw or [])) if n)

        return cls(
            enabled=bool(enabled),
            topic=str(options.get("topic") or DEFAULT_TOPIC).strip() or DEFAULT_TOPIC,
            debounce=debounce,
            labels=_names("labels"),
            cameras=_names("cameras"),
        )


class FrigateEvents:
    """The listener. `handle` is the whole of it; `async_setup` is the wiring."""

    def __init__(self, jarvis: "Jarvis", config: FrigateConfig) -> None:
        self.jarvis = jarvis
        self.config = config
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._last: dict[tuple[str, str], float] = {}
        self._unsubscribe: Any = None
        self._warned_no_notifications = False

    # --- wiring -----------------------------------------------------------
    async def async_setup(self) -> None:
        from ..mqtt import async_subscribe  # local: mqtt is optional

        async def _on_message(message: Any) -> None:
            payload = getattr(message, "payload", message)
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8", "replace")
            try:
                await self.handle(payload)
            except Exception:  # a bad payload must not take the subscription down
                _LOGGER.exception("vision: frigate event handler failed")

        try:
            self._unsubscribe = await async_subscribe(self.jarvis, self.config.topic, _on_message)
        except Exception:
            _LOGGER.exception("vision: could not subscribe to %s", self.config.topic)
        if self._unsubscribe is None:
            _LOGGER.warning(
                "vision: frigate events wanted but the mqtt integration is not set "
                "up, so %s is not being read", self.config.topic,
            )

    async def async_shutdown(self) -> None:
        unsub, self._unsubscribe = self._unsubscribe, None
        if callable(unsub):
            try:
                result = unsub()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # pragma: no cover - defensive
                _LOGGER.debug("vision: frigate unsubscribe failed", exc_info=True)

    # --- the work ---------------------------------------------------------
    def _remember(self, event_id: str, now: float) -> None:
        self._seen[event_id] = now
        while len(self._seen) > MAX_REMEMBERED_IDS:
            self._seen.popitem(last=False)

    async def handle(self, raw: Any, now: float | None = None) -> dict[str, Any]:
        """One message off the topic. Returns what was done and why, for the tests."""
        now = time.time() if now is None else now
        if isinstance(raw, (dict, list)):
            payload = raw
        else:
            try:
                payload = json.loads(str(raw or ""))
            except (TypeError, ValueError):
                return {"recorded": False, "reason": "not JSON"}
        if not isinstance(payload, dict):
            return {"recorded": False, "reason": "not an event"}

        # `after` is the state the message describes; `before` exists on
        # `update` and `end`. Either names the same event.
        after = payload.get("after")
        if not isinstance(after, dict):
            after = payload.get("before")
        if not isinstance(after, dict):
            return {"recorded": False, "reason": "no event in the message"}

        event_id = _clean(after.get("id") or payload.get("id"), 64).replace(" ", "")
        camera = _clean(after.get("camera"))
        label = _clean(after.get("label")) or "something"
        if not event_id or not camera:
            return {"recorded": False, "reason": "no id or camera"}
        if self.config.cameras and camera not in self.config.cameras:
            return {"recorded": False, "reason": f"camera {camera} is not listened to"}
        if self.config.labels and label not in self.config.labels:
            return {"recorded": False, "reason": f"label {label} is not listened to"}

        # A false positive Frigate has retracted is not something that happened.
        if after.get("false_positive") is True:
            self._remember(event_id, now)
            return {"recorded": False, "reason": "false positive"}

        if event_id in self._seen:
            return {"recorded": False, "reason": "already recorded"}
        self._remember(event_id, now)

        key = (camera, label)
        last = self._last.get(key)
        if last is not None and self.config.debounce and now - last < self.config.debounce:
            return {"recorded": False, "reason": "debounced"}
        self._last[key] = now

        zones_raw = after.get("entered_zones") or after.get("current_zones") or []
        zones = [z for z in (_clean(v) for v in zones_raw if isinstance(v, str)) if z][:5]

        title = f"{_title_case(label)} at {_title_case(camera)}"
        where = f" in {', '.join(zones)}" if zones else ""
        body = (
            f"Frigate saw a {label} on the {camera.replace('_', ' ')} camera{where}. "
            "Ask Jarvis to look at the camera for a description."
        )
        return await self._record(title, body, event_id)

    async def _record(self, title: str, body: str, event_id: str) -> dict[str, Any]:
        services = self.jarvis.services
        if not services.has_service("notifications", "add"):
            if not self._warned_no_notifications:
                self._warned_no_notifications = True
                _LOGGER.warning(
                    "vision: a frigate event arrived but `notifications:` is not "
                    "enabled, so there is nowhere to record it"
                )
            return {"recorded": False, "reason": "notifications is not enabled"}
        result = await services.async_call(
            "notifications", "add",
            {"kind": KIND, "title": title, "body": body, "source": SOURCE},
            blocking=True, return_response=True,
        )
        out = dict(result) if isinstance(result, dict) else {"recorded": True}
        out.setdefault("recorded", True)
        out["event_id"] = event_id
        return out


__all__ = ["DEFAULT_TOPIC", "KIND", "SOURCE", "FrigateConfig", "FrigateEvents"]
