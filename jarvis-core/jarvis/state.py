"""Entity state machine — the single source of truth for what things are doing."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .bus import Context, EventBus
from .const import EVENT_STATE_CHANGED, STATE_UNKNOWN

ENTITY_ID_RE = re.compile(r"^(?P<domain>[a-z0-9_]+)\.(?P<object_id>[a-z0-9_]+)$")


def valid_entity_id(entity_id: str) -> bool:
    return ENTITY_ID_RE.match(entity_id) is not None


def split_entity_id(entity_id: str) -> tuple[str, str]:
    domain, _, object_id = entity_id.partition(".")
    return domain, object_id


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unnamed"


@dataclass(slots=True)
class State:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    context: Context = field(default_factory=Context)

    @property
    def domain(self) -> str:
        return split_entity_id(self.entity_id)[0]

    @property
    def name(self) -> str:
        return self.attributes.get(
            "friendly_name", split_entity_id(self.entity_id)[1].replace("_", " ").title()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_changed": self.last_changed,
            "last_updated": self.last_updated,
            "context": self.context.as_dict(),
        }


class StateMachine:
    def __init__(self, bus: EventBus) -> None:
        self._states: dict[str, State] = {}
        self._bus = bus

    # --- reads ------------------------------------------------------------
    def get(self, entity_id: str) -> State | None:
        return self._states.get(entity_id.lower())

    def is_state(self, entity_id: str, state: str) -> bool:
        current = self.get(entity_id)
        return current is not None and current.state == state

    def all(self, domain: str | None = None) -> list[State]:
        if domain is None:
            return list(self._states.values())
        return [s for s in self._states.values() if s.domain == domain]

    def entity_ids(self, domain: str | None = None) -> list[str]:
        return [s.entity_id for s in self.all(domain)]

    def domains(self) -> set[str]:
        return {s.domain for s in self._states.values()}

    # --- writes -----------------------------------------------------------
    def set(
        self,
        entity_id: str,
        state: Any,
        attributes: dict[str, Any] | None = None,
        force_update: bool = False,
        context: Context | None = None,
    ) -> State:
        """Set a state and fire state_changed when something actually changed."""
        entity_id = entity_id.lower()
        if not valid_entity_id(entity_id):
            raise ValueError(f"invalid entity_id: {entity_id!r}")

        new_state = STATE_UNKNOWN if state is None else str(state)
        attributes = dict(attributes or {})
        old = self._states.get(entity_id)
        same = (
            old is not None
            and old.state == new_state
            and old.attributes == attributes
            and not force_update
        )
        if same:
            return old  # type: ignore[return-value]

        now = time.time()
        last_changed = now if old is None or old.state != new_state else old.last_changed
        ctx = context or Context()
        current = State(entity_id, new_state, attributes, last_changed, now, ctx)
        self._states[entity_id] = current
        self._bus.fire(
            EVENT_STATE_CHANGED,
            {"entity_id": entity_id, "old_state": old, "new_state": current},
            ctx,
        )
        return current

    def remove(self, entity_id: str, context: Context | None = None) -> bool:
        entity_id = entity_id.lower()
        old = self._states.pop(entity_id, None)
        if old is None:
            return False
        self._bus.fire(
            EVENT_STATE_CHANGED,
            {"entity_id": entity_id, "old_state": old, "new_state": None},
            context or Context(),
        )
        return True
