"""routines — Jarvis proposes a routine (M104).

    routines:
      at: "07:05"        # once a morning, in the house's zone; unset for on demand only
      days: 14           # how far back the miner reads
      min_days: 3        # on how many distinct days the same thing must have happened
      slot_minutes: 15   # how close in time "the same time" is
      max_proposals: 3   # how many are put to the person in one go

The house does the same things at the same times and nobody writes the
routine. The miner reads the recorder's history: an entity put in the same
state at the same time of day on enough distinct days — and not by an
automation the house already has — is a candidate. Each is put to the person
once, as a `proposal` card and a question ("You turn the kitchen lights off at
about 22:30 most nights — shall I make that a routine?"); a yes creates the
automation through the same door the console and `create_automation` use, so
it reads back like any routine (M97); a no is remembered for thirty days.

What it will NOT propose: an unlock, a disarm, or anything on a domain it
does not know how to name — a routine that opens the house is the person's to
write. And it never creates one without a yes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING, Any, Iterable

from ...automation.util import configured_clock, get_clock, next_time_of_day
from ...const import EVENT_JARVIS_START
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "routines"
DEPENDENCIES: list[str] = []
STORAGE_KEY = "routines"
STORAGE_VERSION = 1
EVENT_PROPOSED = "jarvis_routine_proposed"

DEFAULT_DAYS = 14
DEFAULT_MIN_DAYS = 3
DEFAULT_SLOT_MINUTES = 15
DEFAULT_MAX_PROPOSALS = 3
DECLINE_SECONDS = 30 * 86400.0

#: The states a routine may put a thing in, and the service that does it.
#: `unlocked` is deliberately absent: a routine that opens the house is the
#: person's to write, never something Jarvis suggests.
ACTIONS: dict[tuple[str, str], str] = {
    ("light", "on"): "light.turn_on", ("light", "off"): "light.turn_off",
    ("switch", "on"): "switch.turn_on", ("switch", "off"): "switch.turn_off",
    ("fan", "on"): "fan.turn_on", ("fan", "off"): "fan.turn_off",
    ("lock", "locked"): "lock.lock",
    ("cover", "closed"): "cover.close_cover", ("cover", "open"): "cover.open_cover",
    ("media_player", "off"): "media_player.turn_off",
}


def slot_of(minute_of_day: int, slot_minutes: int) -> int:
    """The quarter-hour (by default) a minute belongs to, as its start minute."""
    return (int(minute_of_day) // int(slot_minutes)) * int(slot_minutes)


def verb_for(service: str) -> str:
    return {
        "turn_on": "turn on", "turn_off": "turn off", "lock": "lock",
        "close_cover": "close", "open_cover": "open",
    }.get(service.split(".", 1)[1], service)


def name_of(jarvis: "Jarvis | None", entity_id: str) -> str:
    states = getattr(jarvis, "states", None)
    state = states.get(entity_id) if states is not None else None
    name = str((getattr(state, "attributes", {}) or {}).get("friendly_name") or "") if state is not None else ""
    return name or entity_id.split(".", 1)[1].replace("_", " ")


def mine(
    rows: Iterable[dict[str, Any]],
    *,
    now: float,
    zone: Any = None,
    days: int = DEFAULT_DAYS,
    min_days: int = DEFAULT_MIN_DAYS,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
    excluded: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Candidates from recorded state rows: `{entity_id, state, at, days, service}`.

    A row is `{entity_id, state, last_changed}` as the recorder returns it. The
    same (entity, state) in the same slot on `min_days` distinct days is a
    candidate, unless on more than one of those days the entity was ALSO put
    in a different state in that slot — a thing toggled back and forth at ten
    is not a routine to turn it off at ten.
    """
    skip = {str(e).lower() for e in excluded}
    since = float(now) - int(days) * 86400.0
    by_key: dict[tuple[str, str, int], set[str]] = {}
    any_state: dict[tuple[str, int], dict[str, set[str]]] = {}
    for row in rows:
        entity_id = str(row.get("entity_id") or "").lower()
        state = str(row.get("state") or "").lower()
        when = float(row.get("last_changed") or row.get("last_updated") or 0.0)
        if not entity_id or not state or when < since or entity_id in skip:
            continue
        domain = entity_id.split(".", 1)[0]
        if (domain, state) not in ACTIONS:
            continue
        local = datetime.fromtimestamp(when, zone) if zone is not None else datetime.fromtimestamp(when)
        day = local.strftime("%Y-%m-%d")
        slot = slot_of(local.hour * 60 + local.minute, slot_minutes)
        by_key.setdefault((entity_id, state, slot), set()).add(day)
        any_state.setdefault((entity_id, slot), {}).setdefault(state, set()).add(day)
    out: list[dict[str, Any]] = []
    for (entity_id, state, slot), seen in by_key.items():
        if len(seen) < int(min_days):
            continue
        others = any_state.get((entity_id, slot), {})
        contradicted = set()
        for other_state, other_days in others.items():
            if other_state != state:
                contradicted |= other_days & seen
        if len(contradicted) > 1:
            continue
        domain = entity_id.split(".", 1)[0]
        out.append({
            "key": f"{entity_id}|{state}|{slot}",
            "entity_id": entity_id,
            "state": state,
            "at": f"{slot // 60:02d}:{slot % 60:02d}",
            "days": sorted(seen),
            "service": ACTIONS[(domain, state)],
        })
    out.sort(key=lambda c: (-len(c["days"]), c["entity_id"], c["at"]))
    return out


def automation_for(candidate: dict[str, Any], name: str) -> dict[str, Any]:
    """The automation a yes creates, in the shape `create_automation` takes."""
    service = str(candidate["service"])
    return {
        "alias": f"{name[:1].upper()}{name[1:]} {verb_for(service).split(' ')[-1] if service.endswith(('turn_on', 'turn_off')) else verb_for(service)} at {candidate['at']}".replace("  ", " "),
        "description": f"Proposed by Jarvis from {len(candidate.get('days') or [])} days of the house's history.",
        "trigger": [{"platform": "time", "at": f"{candidate['at']}:00"}],
        "condition": [],
        "action": [{"service": service, "target": {"entity_id": candidate["entity_id"]}}],
        "mode": "single",
    }


class Routines:
    def __init__(self, jarvis: "Jarvis", options: dict[str, Any], store: Store | None = None) -> None:
        self.jarvis = jarvis
        self.at = str(options.get("at") or "").strip() or None
        self.time_of_day: dt_time | None = None
        if self.at:
            for fmt in ("%H:%M", "%H:%M:%S"):
                try:
                    self.time_of_day = datetime.strptime(self.at, fmt).time()
                    break
                except ValueError:
                    continue
            if self.time_of_day is None:
                _LOGGER.error("routines: at %r is not a time of day (HH:MM); no morning proposal is scheduled", self.at)
        self.days = int(options.get("days") or DEFAULT_DAYS)
        self.min_days = int(options.get("min_days") or DEFAULT_MIN_DAYS)
        self.slot_minutes = int(options.get("slot_minutes") or DEFAULT_SLOT_MINUTES)
        self.max_proposals = int(options.get("max_proposals") or DEFAULT_MAX_PROPOSALS)
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.declined: dict[str, float] = {}
        self.made: dict[str, str] = {}
        self.last: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self.store.load()
        if isinstance(data, dict):
            self.declined = {str(k): float(v) for k, v in (data.get("declined") or {}).items()}
            self.made = {str(k): str(v) for k, v in (data.get("made") or {}).items()}
            self.last = [r for r in (data.get("last") or []) if isinstance(r, dict)]

    async def async_save(self) -> None:
        await self.store.save({"declined": self.declined, "made": self.made, "last": self.last})

    # --- the record --------------------------------------------------------
    def _excluded(self) -> set[str]:
        """Entities an automation of the house already acts on."""
        out: set[str] = set()
        try:
            from ...automation.authored import get_authored

            for entry in get_authored(self.jarvis).entries():
                config = entry.get("config") if isinstance(entry, dict) else None
                for step in (config or entry).get("action") or []:
                    target = (step.get("target") or {}) if isinstance(step, dict) else {}
                    ids = target.get("entity_id")
                    for entity_id in ([ids] if isinstance(ids, str) else (ids or [])):
                        out.add(str(entity_id).lower())
        except Exception:  # noqa: BLE001 - no authored store means nothing excluded
            _LOGGER.debug("routines: could not read the authored automations", exc_info=True)
        return out

    async def candidates(self, now: float | None = None) -> list[dict[str, Any]]:
        now = float(now if now is not None else time.time())
        recorder = self.jarvis.data.get("recorder")
        query = getattr(recorder, "states_between", None)
        if not callable(query):
            return []
        try:
            rows = await query(None, now - self.days * 86400.0, now)
        except Exception:  # noqa: BLE001 - a recorder that cannot answer proposes nothing
            _LOGGER.exception("routines: the recorder did not answer")
            return []
        zone = getattr(configured_clock(self.jarvis), "tz", None)
        found = mine(rows, now=now, zone=zone, days=self.days, min_days=self.min_days,
                     slot_minutes=self.slot_minutes, excluded=self._excluded())
        self.declined = {k: v for k, v in self.declined.items() if v > now}
        fresh = [c for c in found if c["key"] not in self.declined and c["key"] not in self.made]
        for c in fresh:
            c["name"] = name_of(self.jarvis, c["entity_id"])
            c["question"] = (
                f"You {verb_for(c['service'])} the {c['name']} at about {c['at']} most days "
                f"({len(c['days'])} of the last {self.days}) — shall I make that a routine?"
            )
        return fresh

    # --- proposing ---------------------------------------------------------
    async def propose(self, ask: bool = True, now: float | None = None) -> dict[str, Any]:
        async with self._lock:
            found = await self.candidates(now)
            put = found[: self.max_proposals]
            result: dict[str, Any] = {"status": "ok", "candidates": found, "proposed": [], "made": [], "declined": []}
            self.last = found
            for c in put:
                await self._card(c)
                self.jarvis.bus.fire(EVENT_PROPOSED, {k: v for k, v in c.items() if k != "days"} | {"days": len(c["days"])})
                result["proposed"].append(c["key"])
                if not ask:
                    continue
                answer = await self._ask(c)
                if answer is True:
                    made = await self.accept(c)
                    if made.get("automation"):
                        result["made"].append(c["key"])
                elif answer is False:
                    await self.decline(c["key"])
                    result["declined"].append(c["key"])
            await self.async_save()
            return result

    async def _card(self, c: dict[str, Any]) -> None:
        store = self.jarvis.data.get("notifications")
        add = getattr(store, "async_add", None)
        if callable(add):
            try:
                await add(kind="proposal", title=f"A routine: {c['name']} {verb_for(c['service'])} at {c['at']}",
                          body=c["question"], source=EVENT_PROPOSED, link="/house/automations")
            except Exception:
                _LOGGER.exception("routines: could not record the proposal")

    async def _ask(self, c: dict[str, Any]) -> bool | None:
        services = self.jarvis.services
        if not services.has_service("companion", "ask"):
            return None
        try:
            answer = await services.async_call(
                "companion", "ask", {"question": c["question"], "options": ["Yes", "No"], "importance": "normal"},
                blocking=True, return_response=True,
            )
        except Exception:  # noqa: BLE001 - nobody there is not a no
            _LOGGER.info("routines: nobody to ask about %s", c["key"])
            return None
        text = str((answer or {}).get("answer") or "") if isinstance(answer, dict) else ""
        if not text:
            return None
        from ...llm.spoken_answers import AFFIRMATIONS, normalise

        return normalise(text) in {normalise(a) for a in AFFIRMATIONS}

    async def accept(self, candidate: dict[str, Any] | str) -> dict[str, Any]:
        """Make the routine. A key names one of the last candidates; a dict is a draft."""
        c = candidate
        if isinstance(candidate, str):
            c = next((x for x in self.last if x.get("key") == candidate), None)
            if c is None:
                return {"status": "error", "error": f"no proposal {candidate!r} on the table"}
        c = dict(c)
        if "service" not in c:
            domain = str(c.get("entity_id", "")).split(".", 1)[0]
            c["service"] = ACTIONS.get((domain, str(c.get("state", "")).lower()), "")
            if not c["service"]:
                return {"status": "error", "error": "no routine can put that there"}
        if not c.get("at") or not c.get("entity_id"):
            return {"status": "error", "error": "a draft needs an entity_id and an at"}
        c.setdefault("key", f"{c['entity_id']}|{c.get('state', '')}|{c['at']}")
        c.setdefault("name", name_of(self.jarvis, c["entity_id"]))
        from ...api.common import async_create_automation

        try:
            made = await async_create_automation(self.jarvis, {"automation": automation_for(c, c["name"])})
        except Exception as err:  # noqa: BLE001 - said, not raised
            return {"status": "error", "error": str(err)}
        entry = made.get("automation") or {}
        self.made[c["key"]] = str(entry.get("id") or entry.get("automation_id") or "")
        await self.async_save()
        return {"status": "ok", "automation": entry, "key": c["key"]}

    async def decline(self, key: str) -> dict[str, Any]:
        self.declined[str(key)] = time.time() + DECLINE_SECONDS
        await self.async_save()
        return {"status": "ok", "key": key, "until": self.declined[str(key)]}

    # --- the schedule ------------------------------------------------------
    def start(self) -> None:
        if self._task is not None or self.time_of_day is None:
            return
        self._task = self.jarvis.async_create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        clock = get_clock(self.jarvis)
        assert self.time_of_day is not None
        while True:
            when = next_time_of_day(clock.now(), self.time_of_day)
            await clock.sleep(max(1.0, (when - clock.now()).total_seconds()))
            try:
                await self.propose()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("routines: the morning proposal failed")


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    if config is None or config is False:
        return True
    routines = Routines(jarvis, config if isinstance(config, dict) else {})
    await routines.async_load()
    jarvis.data[DOMAIN] = routines

    async def handle_propose(call: Any) -> dict[str, Any]:
        return await routines.propose(ask=bool(call.get("ask", True)))

    async def handle_accept(call: Any) -> dict[str, Any]:
        draft = call.get("draft")
        return await routines.accept(draft if isinstance(draft, dict) else str(call.get("key") or ""))

    async def handle_decline(call: Any) -> dict[str, Any]:
        return await routines.decline(str(call.get("key") or ""))

    jarvis.services.register(DOMAIN, "propose", handle_propose, supports_response=True,
                             description="Mine the house's history for routines and put the candidates to the person (M104).",
                             fields={"ask": {"description": "false: list and card only, ask nobody", "example": True}})
    jarvis.services.register(DOMAIN, "accept", handle_accept, supports_response=True,
                             description="Make a proposed routine: by its key, or from a draft {entity_id, state, at}.",
                             fields={"key": {"example": "light.kitchen_lights|off|1350"}, "draft": {"example": {"entity_id": "light.kitchen_lights", "state": "off", "at": "22:30"}}})
    jarvis.services.register(DOMAIN, "decline", handle_decline, supports_response=True,
                             description="Not that one, not for thirty days.", fields={"key": {"required": True}})

    registry = jarvis.data.get("llm_tools")
    if registry is not None:
        from ...llm.tools import TIER_DIRECT, schema_object

        async def tool_proposed(args: dict[str, Any], context: Any = None) -> Any:
            found = await routines.candidates()
            routines.last = found
            await routines.async_save()
            return {
                "status": "ok",
                "count": len(found),
                "routines": [{"key": c["key"], "says": c["question"], "entity_id": c["entity_id"], "at": c["at"], "days": len(c["days"])} for c in found[:5]],
                "message": ("Nothing in the last %d days happens at the same time often enough to be a routine." % routines.days if not found else
                            "Read `says` back for each; if the user wants one, call routines.accept with its key through the automation tools, or say a plain yes to the card."),
            }

        registry.register(
            name="proposed_routines",
            description=("Routines Jarvis would suggest from the house's history — the same thing at the same time on "
                         "several days. For 'is there a routine you'd suggest?' or 'what do I always do?'. Never creates one."),
            parameters=schema_object({}, []),
            handler=tool_proposed,
            tier=TIER_DIRECT,
            read_only=True,
        )

    def _start(event: Any = None) -> None:
        routines.start()

    if getattr(jarvis, "is_running", False):
        routines.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, _start)
    jarvis.register_shutdown(routines.stop)
    _LOGGER.info("routines: %s", f"every morning at {routines.at}" if routines.time_of_day else "on demand only")
    return True


__all__ = ["ACTIONS", "DOMAIN", "EVENT_PROPOSED", "Routines", "async_setup", "automation_for", "mine", "slot_of"]
