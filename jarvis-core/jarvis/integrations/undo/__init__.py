"""undo — "undo that".

Every state-changing service call is watched. When one runs, the states it
moves are captured *as they were immediately before*, keyed by the call's
context id, and kept for a few minutes. ``undo.last`` puts them back.

The interesting part is what it refuses.

* **Not everything is reversible.** A lock is not "put back" by locking it
  again — the door was open to the world in between, and the model does not
  get to decide that was fine. Notifications cannot be unsent. Scripts,
  scenes of unknown content, presses, alarms and anything in
  :data:`jarvis.const.GATED_DOMAINS` are recorded so the refusal can *name*
  what it is refusing, and then refused.
* **Intent goes stale.** Entries expire (default 10 minutes). "Undo that" an
  hour later must not resurrect a decision from an hour ago; it says there is
  nothing recent to undo instead.
* **The house moves on.** Restoring is best-effort per entity: an entity that
  has since disappeared, or that has no service able to put it back, is
  skipped with a reason rather than silently dropped.
* **Undo is not itself undoable.** The reversal runs under an ``undo``-origin
  context, which the recorder ignores, so "undo, undo, undo" cannot oscillate
  the house.

Configuration (all optional)::

    undo:
      max_entries: 20      # how many recent actions to keep
      ttl: 600             # seconds an entry stays undoable

Services
    ``undo.last``  (entry_id) → what was reversed, or why it was not
    ``undo.list``  (limit)    → the recent, still-undoable actions
    ``undo.clear``            → forget the history

LLM tool: ``undo_last_action``.

The services and the tool are not the same thing
------------------------------------------------
The *services* are the trusted path: the API (authenticated), automations, the
user's own console. They see the whole house.

The *tool* is the model's path, and it carries two extra restrictions the
services do not, because "put it back" is still a way to move the house:

* **Exposure.** Undo is the only tool that acts on entities it was not asked
  to name, so it is the one place an unexposed entity could be actuated — and
  named — by the model. If the action being reversed touched anything the user
  has not exposed, the whole reversal is refused rather than half-applied, and
  nothing about those entities is described back.
* **Untrusted turns.** A turn that has already read a web page, a screen, a
  notification or an MQTT payload cannot undo. ``control_device`` raises such a
  turn to CONFIRM; undo cannot do that honestly — "the last action" is resolved
  at approval time, so what a human approved need not be what runs — so it
  refuses and tells the user to say it themselves.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...bus import Context
from ...const import (
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
    GATED_DOMAINS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from ...services import ServiceCall
from ...state import split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...state import State

_LOGGER = logging.getLogger(__name__)

DOMAIN = "undo"
DEPENDENCIES = ["llm"]

EVENT_UNDO_PERFORMED = "undo_performed"

DEFAULT_MAX_ENTRIES = 20
DEFAULT_TTL = 600.0

#: A state change arriving later than this after its service call is treated
#: as something else's doing, not that call's effect.
ATTRIBUTION_WINDOW = 10.0

#: `Entity.async_write_state()` writes without the service call's context, so
#: an entity-backed device reports its change under a *fresh* context id and
#: the exact match below finds nothing. The fallback matches on target and
#: recency instead, over a much shorter window — long enough for a device
#: round-trip, short enough that it cannot claim someone else's change.
FALLBACK_WINDOW = 2.0

#: Contexts we never record — our own reversals, and read-only plumbing.
IGNORED_ORIGINS = frozenset({"undo"})
IGNORED_DOMAINS = frozenset(
    {
        DOMAIN, "memory", "trace", "briefing", "logbook", "recorder", "history",
        "persistent_notification", "system_log", "homeassistant_compat",
        "conversation", "llm", "voice", "template", "web",
    }
)

#: Domains whose previous state genuinely describes the previous *situation*,
#: so writing it back is a real undo.
REVERSIBLE_DOMAINS = frozenset(
    {
        "light", "switch", "fan", "cover", "climate", "media_player",
        "humidifier", "number", "select", "text", "input_boolean",
        "input_number", "input_select", "input_text", "scene",
    }
)

#: Why each domain is off-limits, in words a person (or a model relaying it)
#: can use.
REFUSALS: dict[str, str] = {
    "lock": "locks are never reversed automatically; say plainly what you want locked or unlocked",
    "notify": "a notification cannot be unsent",
    "companion": "a message that was already delivered cannot be unsent",
    "alarm_control_panel": "arming and disarming an alarm is not something to reverse blind",
    "siren": "a siren that has already sounded cannot be un-sounded",
    "button": "a button press has already happened; there is nothing to put back",
    "script": "a script may have done things that are not states — re-run the opposite deliberately",
    "automation": "enabling or disabling an automation is a deliberate change, not an accident",
    "vacuum": "sending a vacuum somewhere is a physical journey, not a state to rewrite",
    "camera": "camera actions are not reversible",
    "device_control": "actions on your own devices are approved individually; reverse them the same way",
}

#: Domains that fan out into other service calls under the *same* context. The
#: inner calls are not recorded separately, so "undo that" after a scene puts
#: back everything the scene touched rather than only its last light.
CONTAINER_DOMAINS = frozenset({"scene"})

#: These entities' own states are bookkeeping (a scene's activation stamp, a
#: script's on/off). There is nothing in them to restore.
BOOKKEEPING_DOMAINS = frozenset({"scene", "script", "automation"})

#: Service verbs that destroy or reload something regardless of domain.
DESTRUCTIVE_SERVICES = frozenset(
    {"reload", "delete", "remove", "purge", "clear", "reset", "restart", "stop", "shutdown"}
)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def classify(domain: str, service: str) -> tuple[bool, str]:
    """``(reversible, reason_if_not)`` for a ``domain.service`` pair."""
    domain = str(domain or "").lower()
    service = str(service or "").lower()

    if domain in GATED_DOMAINS or domain in REFUSALS:
        return False, REFUSALS.get(domain, f"{domain} actions always need a human decision")
    if service in DESTRUCTIVE_SERVICES:
        return False, f"{domain}.{service} is destructive; there is no previous state to restore"
    if domain in REVERSIBLE_DOMAINS:
        return True, ""
    return False, f"{domain} actions are not known to be safely reversible"


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------
@dataclass
class UndoEntry:
    id: str
    domain: str
    service: str
    data: dict[str, Any]
    context_id: str
    origin: str
    created: float
    reversible: bool
    reason: str = ""
    #: entity_id -> the State immediately before this call (None = it did not
    #: exist yet, which we will not "undo" by deleting it).
    previous: dict[str, Any] = field(default_factory=dict)
    undone: bool = False

    @property
    def entity_ids(self) -> list[str]:
        return list(self.previous)

    def expired(self, ttl: float, now: float | None = None) -> bool:
        return ((now if now is not None else time.time()) - self.created) > ttl

    def describe(self) -> str:
        targets = ", ".join(
            str(state.get("name") or entity_id)
            for entity_id, state in list(self.previous.items())[:3]
        )
        more = len(self.previous) - 3
        if more > 0:
            targets = f"{targets} and {more} more"
        return f"{self.domain}.{self.service}" + (f" on {targets}" if targets else "")

    def as_dict(self, ttl: float = DEFAULT_TTL, now: float | None = None) -> dict[str, Any]:
        moment = now if now is not None else time.time()
        return {
            "id": self.id,
            "domain": self.domain,
            "service": self.service,
            "description": self.describe(),
            "entity_ids": self.entity_ids,
            "origin": self.origin,
            "when": self.created,
            "age_s": round(moment - self.created, 1),
            "expires_in_s": round(max(0.0, ttl - (moment - self.created)), 1),
            "reversible": self.reversible,
            "reason": self.reason or None,
            "undone": self.undone,
        }


def _target_ids(data: dict[str, Any]) -> set[str]:
    """Entity ids a service call named explicitly (empty = area/device/all)."""
    raw = data.get("entity_id")
    if raw is None:
        return set()
    if isinstance(raw, str):
        if raw.strip().lower() in ("all", "*"):
            return set()
        return {raw.strip().lower()}
    if isinstance(raw, (list, tuple, set, frozenset)):
        return {str(item).strip().lower() for item in raw if str(item).strip()}
    return set()


def _snapshot(state: "State | None") -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "state": state.state,
        "attributes": dict(state.attributes),
        "name": state.attributes.get("friendly_name")
        or split_entity_id(state.entity_id)[1].replace("_", " ").title(),
    }


# ---------------------------------------------------------------------------
# restoring
# ---------------------------------------------------------------------------
ON_LIKE = frozenset({"on", "open", "home"})


def restore_plan(
    jarvis: "Jarvis", entity_id: str, previous: dict[str, Any]
) -> tuple[list[tuple[str, str, dict[str, Any]]], str]:
    """``(calls, reason_if_none)`` that put one entity back where it was.

    Returns a list because a couple of domains need two calls (a thermostat's
    mode *and* its setpoint) to actually be back where they were.
    """
    domain = split_entity_id(entity_id)[0]
    state = str(previous.get("state") or "")
    attributes = previous.get("attributes") or {}
    calls: list[tuple[str, str, dict[str, Any]]] = []

    if state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
        return [], f"{entity_id} had no usable previous state ({state or 'none'})"

    if domain == "light":
        if state == "on":
            data: dict[str, Any] = {"entity_id": entity_id}
            for key in ("brightness", "color_temp_kelvin", "rgb_color"):
                if attributes.get(key) is not None:
                    data[key] = attributes[key]
            calls.append((domain, "turn_on", data))
        else:
            calls.append((domain, "turn_off", {"entity_id": entity_id}))

    elif domain in ("switch", "input_boolean", "humidifier"):
        calls.append(
            (domain, "turn_on" if state in ON_LIKE else "turn_off", {"entity_id": entity_id})
        )

    elif domain == "media_player":
        # "It was playing" is put back by playing, not by turning the thing on.
        if state == "playing":
            calls.append((domain, "media_play", {"entity_id": entity_id}))
        elif state == "paused":
            calls.append((domain, "media_pause", {"entity_id": entity_id}))
        elif state in ("off", "standby"):
            calls.append((domain, "turn_off", {"entity_id": entity_id}))
        else:
            calls.append((domain, "media_stop", {"entity_id": entity_id}))
        if attributes.get("volume_level") is not None:
            calls.append(
                (domain, "volume_set",
                 {"entity_id": entity_id, "volume_level": attributes["volume_level"]})
            )

    elif domain == "fan":
        if state == "on":
            data = {"entity_id": entity_id}
            if attributes.get("percentage") is not None:
                data["percentage"] = attributes["percentage"]
            calls.append((domain, "turn_on", data))
        else:
            calls.append((domain, "turn_off", {"entity_id": entity_id}))

    elif domain == "cover":
        position = attributes.get("current_position", attributes.get("position"))
        if position is not None and jarvis.services.has_service(domain, "set_cover_position"):
            calls.append((domain, "set_cover_position", {"entity_id": entity_id, "position": position}))
        elif state == "open":
            calls.append((domain, "open_cover", {"entity_id": entity_id}))
        elif state == "closed":
            calls.append((domain, "close_cover", {"entity_id": entity_id}))
        else:
            return [], f"{entity_id} was mid-travel ({state}); it has no position to return to"

    elif domain == "climate":
        calls.append((domain, "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": state}))
        if attributes.get("temperature") is not None:
            calls.append(
                (domain, "set_temperature",
                 {"entity_id": entity_id, "temperature": attributes["temperature"]})
            )

    elif domain in ("number", "input_number", "text", "input_text"):
        calls.append((domain, "set_value", {"entity_id": entity_id, "value": state}))

    elif domain in ("select", "input_select"):
        calls.append((domain, "select_option", {"entity_id": entity_id, "option": state}))

    else:
        return [], f"nothing in Jarvis knows how to put a {domain} entity back"

    usable = [c for c in calls if jarvis.services.has_service(c[0], c[1])]
    if not usable:
        missing = ", ".join(f"{d}.{s}" for d, s, _ in calls)
        return [], f"{missing} is not available on this system"
    return usable, ""


# ---------------------------------------------------------------------------
# the recorder
# ---------------------------------------------------------------------------
class UndoRecorder:
    """Watches service calls, remembers how to walk them back."""

    def __init__(
        self,
        jarvis: "Jarvis",
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl: float = DEFAULT_TTL,
    ) -> None:
        self.jarvis = jarvis
        self.max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
        self.ttl = float(ttl or DEFAULT_TTL)
        self.entries: deque[UndoEntry] = deque(maxlen=self.max_entries)
        self._counter = 0
        self._unsubs: list[Any] = []

    # --- lifecycle --------------------------------------------------------
    def async_setup(self) -> None:
        self._unsubs.append(self.jarvis.bus.listen(EVENT_CALL_SERVICE, self._on_service_call))
        self._unsubs.append(self.jarvis.bus.listen(EVENT_STATE_CHANGED, self._on_state_changed))
        self.jarvis.register_shutdown(self.async_shutdown)

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    # --- capture ----------------------------------------------------------
    def _on_service_call(self, event: Any) -> None:
        try:
            domain = str(event.data.get("domain") or "")
            service = str(event.data.get("service") or "")
            if not domain or domain in IGNORED_DOMAINS:
                return
            context = getattr(event, "context", None)
            if getattr(context, "origin", "") in IGNORED_ORIGINS:
                return
            if self._inside_container(getattr(context, "id", "") or ""):
                return
            reversible, reason = classify(domain, service)
            self._counter += 1
            self.entries.append(
                UndoEntry(
                    id=f"u{self._counter}",
                    domain=domain,
                    service=service,
                    data=dict(event.data.get("service_data") or {}),
                    context_id=getattr(context, "id", "") or "",
                    origin=getattr(context, "origin", "internal"),
                    created=float(getattr(event, "time_fired", time.time())),
                    reversible=reversible,
                    reason=reason,
                )
            )
        except Exception:  # pragma: no cover - a bad listener must not break the bus
            _LOGGER.exception("undo failed recording a service call")

    def _on_state_changed(self, event: Any) -> None:
        try:
            context = getattr(event, "context", None)
            context_id = getattr(context, "id", None)
            if not context_id:
                return
            entity_id = event.data.get("entity_id")
            if not entity_id:
                return
            if split_entity_id(str(entity_id))[0] in BOOKKEEPING_DOMAINS:
                return
            entry = self._entry_for_state(str(entity_id), context_id)
            if entry is None:
                return
            if entity_id in entry.previous:
                return  # the state *before* the call is the one that matters
            entry.previous[entity_id] = _snapshot(event.data.get("old_state")) or {
                "state": STATE_UNKNOWN,
                "attributes": {},
                "name": split_entity_id(entity_id)[1].replace("_", " ").title(),
                "existed": False,
            }
        except Exception:  # pragma: no cover
            _LOGGER.exception("undo failed recording a state change")

    def _inside_container(self, context_id: str) -> bool:
        """True when this call is a scene fanning out under its own context."""
        if not context_id:
            return False
        now = time.time()
        for entry in reversed(self.entries):
            if entry.context_id != context_id:
                continue
            return (
                entry.domain in CONTAINER_DOMAINS
                and now - entry.created <= ATTRIBUTION_WINDOW
            )
        return False

    def _entry_for_state(self, entity_id: str, context_id: str) -> UndoEntry | None:
        """Which recorded call caused this state change, if any.

        A script runs every step under one context, so "most recent under this
        context" is what attributes a change to the step that actually caused
        it. Failing that (see :data:`FALLBACK_WINDOW`), the newest call that
        plausibly targeted this entity in the last couple of seconds.
        """
        now = time.time()
        for entry in reversed(self.entries):
            if entry.context_id != context_id:
                continue
            return entry if now - entry.created <= ATTRIBUTION_WINDOW else None

        domain = split_entity_id(entity_id)[0]
        for entry in reversed(self.entries):
            if now - entry.created > FALLBACK_WINDOW:
                return None
            targets = _target_ids(entry.data)
            if targets:
                if entity_id.lower() in targets:
                    return entry
            elif entry.domain == domain:
                return entry
        return None

    # --- reading ----------------------------------------------------------
    def purge(self, now: float | None = None) -> int:
        moment = now if now is not None else time.time()
        fresh = [e for e in self.entries if not e.expired(self.ttl, moment)]
        removed = len(self.entries) - len(fresh)
        self.entries = deque(fresh, maxlen=self.max_entries)
        return removed

    def recent(self, limit: int | None = None) -> list[UndoEntry]:
        """Newest first: what "undo that" could plausibly be referring to.

        A reversible call that moved nothing is not an action anybody means.
        An *irreversible* one is kept even when it moved no entity state — a
        sent message is exactly the thing whose refusal has to be explained
        rather than answered with "nothing to undo".
        """
        self.purge()
        entries = [
            e
            for e in reversed(self.entries)
            if (e.previous or not e.reversible) and not e.undone
        ]
        if limit:
            entries = entries[: int(limit)]
        return entries

    def get(self, entry_id: str) -> UndoEntry | None:
        self.purge()
        for entry in self.entries:
            if entry.id == str(entry_id):
                return entry
        return None

    # --- undoing ----------------------------------------------------------
    async def async_undo(self, entry_id: str | None = None) -> dict[str, Any]:
        self.purge()

        if entry_id:
            entry = self.get(str(entry_id))
            if entry is None:
                return {
                    "status": "nothing_to_undo",
                    "message": f"There is no recent action {entry_id!r} to undo, Sir.",
                }
        else:
            candidates = self.recent(limit=1)
            if not candidates:
                return {
                    "status": "nothing_to_undo",
                    "message": (
                        "Nothing to undo, Sir — nothing has changed in the last "
                        f"{int(self.ttl // 60)} minutes."
                    ),
                }
            entry = candidates[0]

        if entry.undone:
            return {
                "status": "nothing_to_undo",
                "entry": entry.as_dict(self.ttl),
                "message": "That one has already been undone, Sir.",
            }
        if not entry.reversible:
            return {
                "status": "refused",
                "entry": entry.as_dict(self.ttl),
                "reason": entry.reason,
                "message": (
                    f"I won't undo {entry.describe()}, Sir — {entry.reason}."
                ),
            }
        if not entry.previous:
            return {
                "status": "nothing_to_undo",
                "entry": entry.as_dict(self.ttl),
                "message": "That changed nothing I could put back, Sir.",
            }

        context = Context(origin="undo", parent_id=entry.context_id or None)
        restored: list[str] = []
        skipped: dict[str, str] = {}

        for entity_id, previous in entry.previous.items():
            if self.jarvis.states.get(entity_id) is None:
                skipped[entity_id] = "no longer exists"
                continue
            if previous.get("existed") is False:
                skipped[entity_id] = "did not exist before the action"
                continue
            calls, reason = restore_plan(self.jarvis, entity_id, previous)
            if not calls:
                skipped[entity_id] = reason
                continue
            failed = ""
            for domain, service, data in calls:
                try:
                    await self.jarvis.services.async_call(
                        domain, service, data, blocking=True, context=context
                    )
                except Exception as exc:
                    failed = f"{type(exc).__name__}: {exc}"
                    break
            if failed:
                skipped[entity_id] = failed
            else:
                restored.append(entity_id)

        entry.undone = bool(restored)
        status = "ok" if restored and not skipped else "partial" if restored else "failed"
        result = {
            "status": status,
            "entry": entry.as_dict(self.ttl),
            "restored": restored,
            "skipped": skipped,
            "message": _undo_message(entry, restored, skipped),
        }
        self.jarvis.bus.fire(EVENT_UNDO_PERFORMED, dict(result), context)
        return result


def _undo_message(entry: UndoEntry, restored: list[str], skipped: dict[str, str]) -> str:
    names = [
        str((entry.previous.get(eid) or {}).get("name") or eid) for eid in restored
    ]
    if not names:
        return f"I could not put anything back, Sir: {'; '.join(skipped.values())}."
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    text = f"Put {listed} back as it was, Sir."
    if skipped:
        text += f" ({len(skipped)} left alone: {'; '.join(sorted(set(skipped.values())))}.)"
    return text


def get_undo(jarvis: "Jarvis") -> UndoRecorder | None:
    recorder = jarvis.data.get(DOMAIN)
    return recorder if isinstance(recorder, UndoRecorder) else None


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    recorder = UndoRecorder(
        jarvis,
        max_entries=int(options.get("max_entries") or DEFAULT_MAX_ENTRIES),
        ttl=float(options.get("ttl") or DEFAULT_TTL),
    )
    recorder.async_setup()
    jarvis.data[DOMAIN] = recorder

    async def handle_last(call: ServiceCall) -> dict[str, Any]:
        return await recorder.async_undo(call.get("entry_id") or call.get("id"))

    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        limit = call.get("limit")
        entries = recorder.recent(int(limit) if limit else None)
        return {
            "entries": [e.as_dict(recorder.ttl) for e in entries],
            "count": len(entries),
            "ttl_s": recorder.ttl,
        }

    async def handle_clear(call: ServiceCall) -> dict[str, Any]:
        count = len(recorder.entries)
        recorder.entries.clear()
        return {"cleared": count}

    jarvis.services.register(
        DOMAIN, "last", handle_last, supports_response=True,
        description="Reverse the most recent reversible action.",
        fields={"entry_id": {"description": "Undo a specific entry from undo.list."}},
    )
    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Recent actions that are still undoable, newest first.",
        fields={"limit": {"description": "Maximum entries to return."}},
    )
    jarvis.services.register(
        DOMAIN, "clear", handle_clear, supports_response=True,
        description="Forget the undo history.",
    )

    _register_tools(jarvis, recorder)
    _LOGGER.info("undo ready: last %d actions, %ds window", recorder.max_entries, int(recorder.ttl))
    return True


def _register_tools(jarvis: "Jarvis", recorder: UndoRecorder) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("undo: no LLM tool registry; services registered without tools")
        return

    from ...api.devices import turn_is_untrusted
    from ...llm.tools import schema_object

    def _hidden_from_model(entry: UndoEntry) -> list[str]:
        """Entities in this entry the user has not exposed to the assistant."""
        exposure = getattr(registry, "exposure", None)
        if exposure is None:
            return []
        hidden = []
        for entity_id in entry.previous:
            try:
                visible = exposure.is_exposed(jarvis, entity_id)
            except Exception:  # pragma: no cover - fail closed
                _LOGGER.exception("undo: exposure check for %s failed", entity_id)
                visible = False
            if not visible:
                hidden.append(entity_id)
        return hidden

    async def tool_undo(args: dict[str, Any], context: Any = None) -> Any:
        if turn_is_untrusted(jarvis, context):
            return {
                "status": "refused",
                "reason": "this turn has read content the user did not write",
                "message": (
                    "I won't reverse anything on this turn, Sir — I have been "
                    "reading something you did not write. Tell me what to put "
                    "back and I will."
                ),
            }

        entry_id = args.get("entry_id")
        # Resolve the target once, here, so the exposure check below and the
        # reversal below it are talking about the same action.
        if entry_id:
            entry = recorder.get(str(entry_id))
        else:
            candidates = recorder.recent(limit=1)
            entry = candidates[0] if candidates else None

        if entry is not None and _hidden_from_model(entry):
            # Deliberately vague: naming them would leak exactly what exposure
            # exists to hide. Fail closed rather than reversing the rest.
            return {
                "status": "refused",
                "reason": "that action touched something you are not exposed to",
                "message": (
                    "I can't reverse that one, Sir — it involved something "
                    "outside what you have given me access to."
                ),
            }

        return await recorder.async_undo(entry.id if entry is not None else entry_id)

    registry.register(
        name="undo_last_action",
        description=(
            "Undo the last thing that changed the house — \"undo that\", \"put it "
            "back\". Some actions are refused (locks, messages, anything that "
            "already happened in the world); when that happens the result says "
            "why, and you must relay the reason rather than trying another route."
        ),
        parameters=schema_object(
            {
                "entry_id": {
                    "type": "string",
                    "description": "Specific entry id from undo.list; omit for the most recent.",
                }
            }
        ),
        handler=tool_undo,
        domain=DOMAIN,
    )


__all__ = [
    "DOMAIN",
    "UndoEntry",
    "UndoRecorder",
    "async_setup",
    "classify",
    "get_undo",
    "restore_plan",
]
