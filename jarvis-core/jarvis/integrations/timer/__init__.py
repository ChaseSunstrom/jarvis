"""timer — a kitchen timer as an entity, counting down on the house's clock.

    timer: {}

"Set a ten-minute timer for the pasta" is the oldest request a voice assistant
gets, and the house had nothing for it: the model reached for a reminder (a
scheduled job with a title, not a countdown), or apologised. Now a timer is an
entity — ``timer.pasta`` — with a state (``active``, ``paused``, ``finished``,
``idle``) and attributes that answer "how long is left?" without a model doing
arithmetic (``remaining`` in seconds, ``remaining_spoken``, ``finishes_at``,
``duration``, ``label``). It counts down on the house's clock
(``automation.util.get_clock``), so a frozen clock in a test freezes the timer
and the house's zone is the zone ``finishes_at`` is written in.

When it finishes it CHIMES: an inbox card (kind ``reminder``, so the console
and "what's new?" see it) and a spoken line on the device that asked
(``companion.notify`` with the device remembered for the turn, M94's
``device_of``), or wherever the person is when no device is known. Then it sits
``finished`` until the next timer with that label starts, or it is cancelled.

Services — ``timer.start`` (``duration`` in seconds, ``label``), ``timer.pause``,
``timer.resume``, ``timer.cancel``, ``timer.snooze`` (``minutes``, default five:
a finished timer counting again) — are the door for automations, the API and
the one model tool ``timer`` (``action`` start|status|pause|resume|cancel|
snooze), which is one tool rather than five because every tool costs intent.

What this does NOT do: survive a restart with the countdown intact. The timers
are written to ``.storage/timer.json`` and on start any that was ``active``
comes back ``finished`` with a card saying it was interrupted — a timer that
went off while the house was down cannot chime late and pretend it was on time.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ...automation.util import get_clock
from ...entity import Entity, EntityPlatform
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "timer"
DEPENDENCIES: list[str] = []

STORAGE_KEY = "timer"
STORAGE_VERSION = 1

STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_FINISHED = "finished"
STATE_IDLE = "idle"

#: The longest timer: a day. Longer is a schedule, and `schedule_task` exists.
MAX_DURATION = 24 * 3600
#: The shortest: a second — the rig sets fifteen-second ones.
MIN_DURATION = 1
#: How many timers may run at once; the model gets a plain refusal past it.
MAX_TIMERS = 20
DEFAULT_SNOOZE_MINUTES = 5
#: How often a running timer rewrites its state, in seconds.
TICK = 10.0

EVENT_TIMER_FINISHED = "timer_finished"


def clean_label(label: Any) -> str:
    """"the pasta timer" -> "pasta": what the timer is called in a sentence.

    Articles and the word "timer" are the sentence's, not the label's — kept,
    "The the pasta timer timer is done" is what the chime would say.
    """
    text = " ".join(str(label or "").split()).strip(" .,!?")
    text = re.sub(r"^(the|a|an|my)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+timer$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,!?")


def slug(label: Any) -> str:
    """"the pasta" -> "pasta": the entity's object id, from the label."""
    text = clean_label(label).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "timer"


def spoken_duration(seconds: float) -> str:
    """90 -> "a minute and 30 seconds"; 3600 -> "an hour"; 0 -> "no time"."""
    total = max(0, int(round(seconds)))
    if total == 0:
        return "no time"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if hours:
        parts.append("an hour" if hours == 1 else f"{hours} hours")
    if minutes:
        parts.append("a minute" if minutes == 1 else f"{minutes} minutes")
    if secs and not hours:
        parts.append("a second" if secs == 1 else f"{secs} seconds")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def parse_duration(value: Any) -> int | None:
    """Seconds from a number, "10:00", "10m", "1h30m", "90s", "ten minutes"-free.

    Words are the model's to turn into numbers; this accepts the shapes a tool
    argument arrives in. None when unreadable.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value or "").strip().lower()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text)) or None
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1] or None
        return parts[0] * 3600 + parts[1] * 60 + parts[2] or None
    total = 0
    found = False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)(?![a-z])", text):
        found = True
        n = float(number)
        if unit.startswith("h"):
            total += n * 3600
        elif unit.startswith("m"):
            total += n * 60
        else:
            total += n
    return int(total) if found and total > 0 else None


class TimerEntity(Entity):
    """One countdown. The platform owns its task; the entity owns its numbers."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, manager: "TimerManager", label: str, object_id: str) -> None:
        self.manager = manager
        self.label = label
        self.object_id = object_id
        # The entity's object id comes from its name (`suggested_object_id`),
        # so the name is the cleaned label: "pasta", never "the pasta".
        self._attr_name = label
        self._attr_unique_id = f"timer_{object_id}"
        self._attr_state = STATE_IDLE
        self.duration = 0
        self.finishes_at: datetime | None = None
        self.remaining_when_paused: float = 0.0
        self.started_at: datetime | None = None
        self.device: dict[str, Any] | None = None
        self.conversation_id: str = ""
        self._task: asyncio.Task | None = None

    # --- numbers -----------------------------------------------------------
    def remaining(self) -> float:
        if self._attr_state == STATE_ACTIVE and self.finishes_at is not None:
            return max(0.0, (self.finishes_at - self.manager.now()).total_seconds())
        if self._attr_state == STATE_PAUSED:
            return max(0.0, self.remaining_when_paused)
        return 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        remaining = self.remaining()
        return {
            "label": self.label,
            "duration": self.duration,
            "duration_spoken": spoken_duration(self.duration),
            "remaining": int(round(remaining)),
            "remaining_spoken": spoken_duration(remaining),
            "finishes_at": self.finishes_at.isoformat(timespec="seconds") if self.finishes_at else None,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "device": (self.device or {}).get("name"),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "state": self._attr_state,
            **self.extra_state_attributes,
        }

    def persisted(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "label": self.label,
            "state": self._attr_state,
            "duration": self.duration,
            "finishes_at": self.finishes_at.isoformat() if self.finishes_at else None,
            "remaining_when_paused": self.remaining_when_paused,
            "device": self.device,
            "conversation_id": self.conversation_id,
        }

    # --- the countdown -----------------------------------------------------
    def _arm(self) -> None:
        self._disarm()
        self._task = self.manager.jarvis.async_create_task(self._count_down())

    def _disarm(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    async def _count_down(self) -> None:
        clock = self.manager.clock
        try:
            while self._attr_state == STATE_ACTIVE:
                left = self.remaining()
                if left <= 0:
                    break
                # Re-checked every ten seconds at most, and the state rewritten
                # so a console watching `remaining` sees it move; `finishes_at`
                # is there for anything that wants the second.
                await clock.sleep(min(TICK, left))
                if self._attr_state == STATE_ACTIVE:
                    self.async_write_state()
            if self._attr_state == STATE_ACTIVE:
                # Shielded: a snooze or a restart at the very instant the
                # countdown ends cancels THIS task, and the chime and the save
                # must still complete — a timer that went off and said nothing
                # is the one thing a timer must never do.
                await asyncio.shield(self.manager.async_finished(self))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one timer's fault must not end another
            _LOGGER.exception("timer: %s failed while counting down", self.entity_id)

    def start(self, duration: int, device: dict[str, Any] | None, conversation_id: str) -> None:
        now = self.manager.now()
        self.duration = int(duration)
        self.started_at = now
        self.finishes_at = now + timedelta(seconds=self.duration)
        self.remaining_when_paused = 0.0
        self.device = dict(device) if device else None
        self.conversation_id = str(conversation_id or "")
        self._attr_state = STATE_ACTIVE
        self.async_write_state()
        self._arm()

    def pause(self) -> bool:
        if self._attr_state != STATE_ACTIVE:
            return False
        self.remaining_when_paused = self.remaining()
        self._attr_state = STATE_PAUSED
        self.finishes_at = None
        self._disarm()
        self.async_write_state()
        return True

    def resume(self) -> bool:
        if self._attr_state != STATE_PAUSED:
            return False
        self.finishes_at = self.manager.now() + timedelta(seconds=self.remaining_when_paused)
        self.remaining_when_paused = 0.0
        self._attr_state = STATE_ACTIVE
        self.async_write_state()
        self._arm()
        return True

    def cancel(self) -> bool:
        was = self._attr_state
        self._disarm()
        self._attr_state = STATE_IDLE
        self.finishes_at = None
        self.remaining_when_paused = 0.0
        self.async_write_state()
        return was in (STATE_ACTIVE, STATE_PAUSED, STATE_FINISHED)

    def finish(self) -> None:
        self._attr_state = STATE_FINISHED
        self.finishes_at = None
        self.remaining_when_paused = 0.0
        self.async_write_state()

    async def async_will_remove(self) -> None:
        self._disarm()


class TimerManager:
    """Every timer in the house, by object id."""

    def __init__(self, jarvis: "Jarvis", store: Store | None = None) -> None:
        self.jarvis = jarvis
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.platform = EntityPlatform(jarvis, DOMAIN, DOMAIN)
        self.timers: dict[str, TimerEntity] = {}

    @property
    def clock(self) -> Any:
        return get_clock(self.jarvis)

    def now(self) -> datetime:
        return self.clock.now()

    # --- persistence -------------------------------------------------------
    async def async_load(self) -> None:
        data = await self.store.load()
        rows = (data or {}).get("timers") if isinstance(data, dict) else None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            object_id = slug(str(row.get("object_id") or row.get("label") or ""))
            entity = await self._entity(str(row.get("label") or object_id), object_id)
            entity.duration = int(row.get("duration") or 0)
            entity.device = row.get("device") if isinstance(row.get("device"), dict) else None
            entity.conversation_id = str(row.get("conversation_id") or "")
            state = str(row.get("state") or STATE_IDLE)
            if state in (STATE_ACTIVE, STATE_PAUSED):
                # It cannot chime on time any more; saying so beats a late
                # chime that pretends it was on time, and beats silence.
                entity.finish()
                await self._card(entity, f"The {entity.label} timer was interrupted when Jarvis restarted.")
            else:
                entity._attr_state = state if state in (STATE_FINISHED, STATE_IDLE) else STATE_IDLE
                entity.async_write_state()

    async def async_save(self) -> None:
        await self.store.save({"timers": [t.persisted() for t in self.timers.values()]})

    # --- entities ----------------------------------------------------------
    async def _entity(self, label: str, object_id: str) -> TimerEntity:
        entity = self.timers.get(object_id)
        if entity is None:
            entity = TimerEntity(self, label, object_id)
            await self.platform.async_add_entities([entity])
            self.timers[object_id] = entity
        return entity

    def find(self, name: Any) -> TimerEntity | None:
        """By object id, entity id, or label — "pasta", "timer.pasta", "the pasta timer"."""
        text = str(name or "").strip().lower()
        if not text:
            # One timer running: that is the one.
            live = [t for t in self.timers.values() if t.state in (STATE_ACTIVE, STATE_PAUSED, STATE_FINISHED)]
            return live[0] if len(live) == 1 else None
        if text.startswith(f"{DOMAIN}."):
            text = text[len(DOMAIN) + 1 :]
        key = slug(text)
        return self.timers.get(key)

    def listing(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.timers.values() if t.state != STATE_IDLE]

    # --- the verbs ---------------------------------------------------------
    async def async_start(
        self,
        duration: Any,
        label: Any = "",
        device: dict[str, Any] | None = None,
        conversation_id: str = "",
    ) -> dict[str, Any]:
        seconds = parse_duration(duration)
        if seconds is None:
            return {"status": "error", "error": "say how long: seconds, '10m', '1h30m' or '10:00'"}
        if seconds < MIN_DURATION or seconds > MAX_DURATION:
            return {"status": "error", "error": f"a timer runs from {MIN_DURATION} second to {MAX_DURATION // 3600} hours"}
        text = clean_label(label) or "timer"
        object_id = slug(text)
        running = [t for t in self.timers.values() if t.state in (STATE_ACTIVE, STATE_PAUSED)]
        if object_id not in self.timers and len(running) >= MAX_TIMERS:
            return {"status": "error", "error": f"{MAX_TIMERS} timers are already running; cancel one first"}
        entity = await self._entity(text, object_id)
        replaced = entity.state in (STATE_ACTIVE, STATE_PAUSED)
        entity.label = text
        entity.start(seconds, device, conversation_id)
        await self.async_save()
        return {
            "status": "ok",
            "timer": entity.as_dict(),
            "replaced": replaced,
            "message": (
                f"{'Restarted' if replaced else 'Set'}: the {text} timer, {spoken_duration(seconds)}"
                f"{' (it was already running; this replaces it)' if replaced else ''}."
            ),
        }

    async def async_pause(self, name: Any) -> dict[str, Any]:
        entity = self.find(name)
        if entity is None:
            return self._none(name)
        if not entity.pause():
            return {"status": "error", "error": f"the {entity.label} timer is {entity.state}, not running", "timer": entity.as_dict()}
        await self.async_save()
        return {"status": "ok", "timer": entity.as_dict(), "message": f"Paused the {entity.label} timer with {spoken_duration(entity.remaining())} left."}

    async def async_resume(self, name: Any) -> dict[str, Any]:
        entity = self.find(name)
        if entity is None:
            return self._none(name)
        if not entity.resume():
            return {"status": "error", "error": f"the {entity.label} timer is {entity.state}, not paused", "timer": entity.as_dict()}
        await self.async_save()
        return {"status": "ok", "timer": entity.as_dict(), "message": f"Resumed the {entity.label} timer, {spoken_duration(entity.remaining())} left."}

    async def async_cancel(self, name: Any) -> dict[str, Any]:
        entity = self.find(name)
        if entity is None:
            return self._none(name)
        had = entity.cancel()
        await self.async_save()
        return {
            "status": "ok",
            "timer": entity.as_dict(),
            "message": f"Cancelled the {entity.label} timer." if had else f"The {entity.label} timer was not running.",
        }

    async def async_snooze(self, name: Any, minutes: Any = None) -> dict[str, Any]:
        entity = self.find(name)
        if entity is None:
            return self._none(name)
        mins = parse_duration(minutes) if minutes not in (None, "") else None
        seconds = (mins * 60 if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) else mins) if mins else DEFAULT_SNOOZE_MINUTES * 60
        entity.start(int(seconds), entity.device, entity.conversation_id)
        await self.async_save()
        return {"status": "ok", "timer": entity.as_dict(), "message": f"The {entity.label} timer will go again in {spoken_duration(seconds)}."}

    def status(self, name: Any = None) -> dict[str, Any]:
        if name:
            entity = self.find(name)
            if entity is None:
                return self._none(name)
            return {"status": "ok", "timer": entity.as_dict(), "message": self._describe(entity)}
        live = self.listing()
        if not live:
            return {"status": "ok", "timers": [], "message": "No timer is running."}
        return {"status": "ok", "timers": live, "message": " ".join(self._describe(self.timers[slug(t['label'])]) for t in live)}

    def _describe(self, entity: TimerEntity) -> str:
        if entity.state == STATE_ACTIVE:
            return f"The {entity.label} timer has {spoken_duration(entity.remaining())} left."
        if entity.state == STATE_PAUSED:
            return f"The {entity.label} timer is paused with {spoken_duration(entity.remaining())} left."
        if entity.state == STATE_FINISHED:
            return f"The {entity.label} timer has finished."
        return f"The {entity.label} timer is not running."

    def _none(self, name: Any) -> dict[str, Any]:
        live = [t.label for t in self.timers.values() if t.state != STATE_IDLE]
        return {
            "status": "error",
            "error": (f"no timer called {str(name)!r}" if name else "no single timer to mean")
            + (f"; running: {', '.join(live)}" if live else "; none is running"),
        }

    # --- the chime ---------------------------------------------------------
    async def async_finished(self, entity: TimerEntity) -> None:
        entity.finish()
        await self.async_save()
        message = f"The {entity.label} timer is done."
        self.jarvis.bus.fire(EVENT_TIMER_FINISHED, {"entity_id": entity.entity_id, "label": entity.label, "duration": entity.duration})
        await self._card(entity, message)
        # Then the room: the device that asked, or wherever the person is.
        try:
            data: dict[str, Any] = {"message": message, "kind": "say", "importance": "high"}
            if entity.device and entity.device.get("id"):
                data["device_id"] = entity.device["id"]
            if entity.conversation_id:
                data["conversation_id"] = entity.conversation_id
            await self.jarvis.services.async_call("companion", "notify", data, blocking=True)
        except Exception as err:  # noqa: BLE001 - the card is the record; the chime is best effort
            _LOGGER.info("timer: no companion channel for %s (%s)", entity.entity_id, err)

    async def _card(self, entity: TimerEntity, message: str) -> None:
        try:
            await self.jarvis.services.async_call(
                "notifications", "add",
                {"kind": "reminder", "title": message, "body": f"{spoken_duration(entity.duration)}, set{' from ' + entity.device['name'] if entity.device and entity.device.get('name') else ''}.", "source": DOMAIN},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.info("timer: no notifications inbox for %s (%s)", entity.entity_id, err)

    async def async_shutdown(self) -> None:
        for entity in self.timers.values():
            entity._disarm()


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    if config is None or config is False:
        return True
    manager = TimerManager(jarvis)
    jarvis.data[DOMAIN] = manager
    await manager.async_load()
    jarvis.register_shutdown(manager.async_shutdown)

    async def start(call: Any) -> dict[str, Any]:
        return await manager.async_start(call.get("duration"), call.get("label", ""), call.get("device"), str(call.get("conversation_id") or ""))

    async def pause(call: Any) -> dict[str, Any]:
        return await manager.async_pause(call.get("name") or call.get("entity_id"))

    async def resume(call: Any) -> dict[str, Any]:
        return await manager.async_resume(call.get("name") or call.get("entity_id"))

    async def cancel(call: Any) -> dict[str, Any]:
        return await manager.async_cancel(call.get("name") or call.get("entity_id"))

    async def snooze(call: Any) -> dict[str, Any]:
        return await manager.async_snooze(call.get("name") or call.get("entity_id"), call.get("minutes"))

    jarvis.services.register(DOMAIN, "start", start, description="Start (or restart) a countdown timer.",
                             fields={"duration": {"required": True, "example": "10m"}, "label": {"example": "pasta"}}, supports_response=True)
    jarvis.services.register(DOMAIN, "pause", pause, description="Pause a running timer.", fields={"name": {"example": "pasta"}}, supports_response=True)
    jarvis.services.register(DOMAIN, "resume", resume, description="Resume a paused timer.", fields={"name": {"example": "pasta"}}, supports_response=True)
    jarvis.services.register(DOMAIN, "cancel", cancel, description="Cancel a timer.", fields={"name": {"example": "pasta"}}, supports_response=True)
    jarvis.services.register(DOMAIN, "snooze", snooze, description="Run a finished timer again, five minutes by default.",
                             fields={"name": {"example": "pasta"}, "minutes": {"example": 5}}, supports_response=True)

    from ...llm.tools import TIER_DIRECT, schema_object

    registry = jarvis.data.get("llm_tools")
    if registry is None:
        return True

    async def tool_timer(args: dict[str, Any], context: Any = None) -> Any:
        from ...api.devices import device_of

        action = str(args.get("action") or "start").strip().lower()
        name = args.get("name") or args.get("label") or ""
        if action == "start":
            device = device_of(jarvis, context)
            conversation_id, _spoken = registry._turn_facts(context)
            result = await manager.async_start(args.get("duration"), name, device, str(conversation_id or ""))
        elif action == "status":
            result = manager.status(name)
        elif action == "pause":
            result = await manager.async_pause(name)
        elif action == "resume":
            result = await manager.async_resume(name)
        elif action == "cancel":
            result = await manager.async_cancel(name)
        elif action == "snooze":
            result = await manager.async_snooze(name, args.get("minutes"))
        else:
            return {"status": "error", "error": "action is one of start, status, pause, resume, cancel, snooze"}
        if result.get("status") == "ok":
            result["message"] = result.get("message", "") + " Tell the user in those words; the timer's own clock is the truth, do not add or subtract."
        return result

    registry.register(
        name="timer",
        description=(
            "Countdown timers — 'set a ten-minute timer for the pasta', 'how long is left?', "
            "'cancel the tea timer', 'snooze'. `start` needs a duration (seconds, '10m', '1h30m') "
            "and a label; the timer becomes an entity timer.<label>, counts down on the house's "
            "clock, and chimes where it was asked for when it finishes. `status` reads the time "
            "left — say `remaining_spoken` back rather than working it out. Not for 'at seven' "
            "or 'tomorrow': that is schedule_task."
        ),
        parameters=schema_object(
            {
                "action": {"type": "string", "description": "start | status | pause | resume | cancel | snooze (default start)"},
                "duration": {"type": "string", "description": "for start: seconds, '10m', '1h30m', '10:00'"},
                "name": {"type": "string", "description": "the timer's label: 'pasta', 'tea'. Optional when only one is running."},
                "minutes": {"type": "integer", "description": "for snooze: how long, default 5"},
            },
            [],
        ),
        handler=tool_timer,
        tier=TIER_DIRECT,
    )
    _LOGGER.info("timer: %d timer(s) restored", len(manager.timers))
    return True


__all__ = ["DOMAIN", "TimerManager", "TimerEntity", "async_setup", "clean_label", "parse_duration", "spoken_duration", "slug"]
