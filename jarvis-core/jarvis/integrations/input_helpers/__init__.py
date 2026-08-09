"""`input_boolean/number/text/select/datetime:` — user-editable state.

These are the knobs automations and voice commands read and write ("guest
mode", "target temperature", "current chore"). Values survive restarts via
:class:`jarvis.store.Store` (``<config>/.storage/input_helpers.json``).

    input_boolean:
      guest_mode:
        name: Guest mode
        initial: off

    input_number:
      bedtime_volume: {min: 0, max: 100, step: 5, initial: 30}

    input_select:
      house_mode: {options: [home, away, night], initial: home}

Because `input_boolean:` (and friends) are not integration names, the
integration loader never sees them — the always-on `automation` integration
bootstraps this module when any of those keys is present. Setting up twice
is a no-op.

Services: ``input_boolean.turn_on/turn_off/toggle``,
``input_number.set_value/increment/decrement``, ``input_text.set_value``,
``input_select.select_option/select_next/select_previous/set_options``,
``input_datetime.set_datetime``.
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_cls, datetime, time as time_cls
from typing import TYPE_CHECKING, Any

from ...automation.util import as_float, as_list, parse_time
from ...const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "input_helpers"
DATA_MANAGER = "input_helpers"
STORAGE_KEY = "input_helpers"

DOMAIN_BOOLEAN = "input_boolean"
DOMAIN_NUMBER = "input_number"
DOMAIN_TEXT = "input_text"
DOMAIN_SELECT = "input_select"
DOMAIN_DATETIME = "input_datetime"

INPUT_DOMAINS = (
    DOMAIN_BOOLEAN,
    DOMAIN_NUMBER,
    DOMAIN_TEXT,
    DOMAIN_SELECT,
    DOMAIN_DATETIME,
)

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M:%S"
DATETIME_FORMAT = f"{DATE_FORMAT} {TIME_FORMAT}"


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------
class InputEntity(Entity):
    """Base for every input helper (value + persistence hook)."""

    input_domain = DOMAIN_BOOLEAN

    def __init__(self, manager: "InputManager", object_id: str, config: dict[str, Any]):
        self._manager = manager
        self.object_id = str(object_id)
        self.config = dict(config or {})
        # `name` decides the entity_id slug; the friendly name is an attribute.
        self._attr_name = self.object_id
        self._attr_unique_id = f"{self.input_domain}_{self.object_id}"
        self._attr_icon = self.config.get("icon")
        self._value: Any = None

    # --- naming -----------------------------------------------------------
    @property
    def friendly_name(self) -> str:
        return str(self.config.get("name") or self.object_id.replace("_", " ").title())

    @property
    def state(self) -> Any:
        return STATE_UNKNOWN if self._value is None else self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"friendly_name": self.friendly_name, "editable": True}
        attrs.update(self.type_attributes())
        return attrs

    def type_attributes(self) -> dict[str, Any]:
        return {}

    # --- values -----------------------------------------------------------
    def coerce(self, raw: Any) -> Any:
        """Validate/normalise a candidate value (None = reject)."""
        return raw

    def default_value(self) -> Any:
        return self.config.get("initial")

    def restore(self, stored: Any) -> None:
        value = self.coerce(stored) if stored is not None else None
        if value is None:
            value = self.coerce(self.default_value())
        self._value = value

    @property
    def stored_value(self) -> Any:
        return self._value

    async def async_set_raw(self, raw: Any) -> bool:
        value = self.coerce(raw)
        if value is None:
            _LOGGER.warning("%s: rejected value %r", self.entity_id or self.object_id, raw)
            return False
        self._value = value
        self.async_write_state()
        await self._manager.async_save()
        return True

    # The domains-layer method contract, so scenes/scripts can drive these.
    async def async_set_value(self, value: Any) -> None:
        await self.async_set_raw(value)


class InputBoolean(InputEntity):
    input_domain = DOMAIN_BOOLEAN

    def coerce(self, raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, bool):
            return STATE_ON if raw else STATE_OFF
        text = str(raw).strip().lower()
        if text in ("on", "true", "yes", "1"):
            return STATE_ON
        if text in ("off", "false", "no", "0"):
            return STATE_OFF
        return None

    def default_value(self) -> Any:
        return self.config.get("initial", STATE_OFF)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set_raw(STATE_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_raw(STATE_OFF)

    async def async_toggle(self) -> None:
        await self.async_set_raw(STATE_OFF if self._value == STATE_ON else STATE_ON)


class InputNumber(InputEntity):
    input_domain = DOMAIN_NUMBER

    @property
    def minimum(self) -> float:
        return float(self.config.get("min", 0))

    @property
    def maximum(self) -> float:
        return float(self.config.get("max", 100))

    @property
    def step(self) -> float:
        return float(self.config.get("step", 1))

    def type_attributes(self) -> dict[str, Any]:
        return {
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "mode": self.config.get("mode", "slider"),
            "unit_of_measurement": self.config.get("unit_of_measurement"),
        }

    def coerce(self, raw: Any) -> Any:
        value = as_float(raw)
        if value is None:
            return None
        value = max(self.minimum, min(self.maximum, value))
        return int(value) if value == int(value) and self.step == int(self.step) else value

    def default_value(self) -> Any:
        return self.config.get("initial", self.config.get("min", 0))

    async def async_increment(self) -> None:
        current = as_float(self._value) or 0.0
        await self.async_set_raw(current + self.step)

    async def async_decrement(self) -> None:
        current = as_float(self._value) or 0.0
        await self.async_set_raw(current - self.step)


class InputText(InputEntity):
    input_domain = DOMAIN_TEXT

    def type_attributes(self) -> dict[str, Any]:
        return {
            "min": int(self.config.get("min", 0)),
            "max": int(self.config.get("max", 255)),
            "mode": self.config.get("mode", "text"),
            "pattern": self.config.get("pattern"),
        }

    def coerce(self, raw: Any) -> Any:
        if raw is None:
            return None
        text = str(raw)
        low = int(self.config.get("min", 0))
        high = int(self.config.get("max", 255))
        if not low <= len(text) <= high:
            return None
        pattern = self.config.get("pattern")
        if pattern and not re.fullmatch(str(pattern), text):
            return None
        return text

    def default_value(self) -> Any:
        return self.config.get("initial", "")


class InputSelect(InputEntity):
    input_domain = DOMAIN_SELECT

    def __init__(self, manager: "InputManager", object_id: str, config: dict[str, Any]):
        super().__init__(manager, object_id, config)
        self.options = [str(o) for o in as_list(config.get("options"))]

    def type_attributes(self) -> dict[str, Any]:
        return {"options": list(self.options)}

    def coerce(self, raw: Any) -> Any:
        if raw is None:
            return None
        text = str(raw)
        return text if text in self.options else None

    def default_value(self) -> Any:
        return self.config.get("initial", self.options[0] if self.options else None)

    async def async_select_option(self, option: str) -> None:
        await self.async_set_raw(option)

    async def async_offset(self, offset: int) -> None:
        if not self.options:
            return
        try:
            index = self.options.index(str(self._value))
        except ValueError:
            index = 0
        await self.async_set_raw(self.options[(index + offset) % len(self.options)])

    async def async_set_options(self, options: list[Any]) -> None:
        self.options = [str(o) for o in as_list(options)]
        if str(self._value) not in self.options:
            self._value = self.options[0] if self.options else None
        self.async_write_state()
        await self._manager.async_save()


class InputDatetime(InputEntity):
    input_domain = DOMAIN_DATETIME

    @property
    def has_date(self) -> bool:
        return bool(self.config.get("has_date", not self.config.get("has_time", False)))

    @property
    def has_time(self) -> bool:
        return bool(self.config.get("has_time", not self.config.get("has_date", False)))

    def _format(self) -> str:
        if self.has_date and self.has_time:
            return DATETIME_FORMAT
        return DATE_FORMAT if self.has_date else TIME_FORMAT

    def type_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"has_date": self.has_date, "has_time": self.has_time}
        parsed = self._parse(self._value)
        if parsed is None:
            return attrs
        if self.has_date:
            attrs.update({"year": parsed.year, "month": parsed.month, "day": parsed.day})
        if self.has_time:
            attrs.update(
                {"hour": parsed.hour, "minute": parsed.minute, "second": parsed.second}
            )
        attrs["timestamp"] = parsed.timestamp()
        return attrs

    def _parse(self, raw: Any) -> datetime | None:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, date_cls):
            return datetime(raw.year, raw.month, raw.day)
        if isinstance(raw, time_cls):
            today = datetime.now()
            return today.replace(
                hour=raw.hour, minute=raw.minute, second=raw.second, microsecond=0
            )
        text = str(raw).strip()
        for fmt in (DATETIME_FORMAT, DATE_FORMAT, TIME_FORMAT, "%Y-%m-%dT%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if fmt in (TIME_FORMAT, "%H:%M"):
                today = datetime.now()
                return today.replace(
                    hour=parsed.hour,
                    minute=parsed.minute,
                    second=parsed.second,
                    microsecond=0,
                )
            return parsed
        return None

    def coerce(self, raw: Any) -> Any:
        parsed = self._parse(raw)
        if parsed is None:
            return None
        return parsed.strftime(self._format())

    def default_value(self) -> Any:
        initial = self.config.get("initial")
        if initial is not None:
            return initial
        if self.has_date and self.has_time:
            return "1970-01-01 00:00:00"
        return "1970-01-01" if self.has_date else "00:00:00"

    async def async_set_datetime(self, **kwargs: Any) -> None:
        if kwargs.get("timestamp") is not None:
            await self.async_set_raw(datetime.fromtimestamp(float(kwargs["timestamp"])))
            return
        if kwargs.get("datetime") is not None:
            await self.async_set_raw(kwargs["datetime"])
            return
        current = self._parse(self._value) or datetime.now().replace(microsecond=0)
        if kwargs.get("date") is not None:
            parsed = self._parse(kwargs["date"])
            if parsed is not None:
                current = current.replace(
                    year=parsed.year, month=parsed.month, day=parsed.day
                )
        if kwargs.get("time") is not None:
            parsed_time = parse_time(kwargs["time"])
            if parsed_time is not None:
                current = current.replace(
                    hour=parsed_time.hour,
                    minute=parsed_time.minute,
                    second=parsed_time.second,
                    microsecond=0,
                )
        await self.async_set_raw(current)


ENTITY_CLASSES: dict[str, type[InputEntity]] = {
    DOMAIN_BOOLEAN: InputBoolean,
    DOMAIN_NUMBER: InputNumber,
    DOMAIN_TEXT: InputText,
    DOMAIN_SELECT: InputSelect,
    DOMAIN_DATETIME: InputDatetime,
}


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------
class InputManager:
    """Creates the helper entities and persists their values."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.store = Store(jarvis.config_dir, STORAGE_KEY)
        self.entities: dict[str, InputEntity] = {}
        self.platforms: dict[str, EntityPlatform] = {}

    def by_domain(self, domain: str) -> list[InputEntity]:
        return [e for e in self.entities.values() if e.input_domain == domain]

    def resolve(self, domain: str, entity_ids: Any) -> list[InputEntity]:
        wanted = [str(e) for e in as_list(entity_ids)]
        if not wanted or any(w in ("all", "*") for w in wanted):
            return self.by_domain(domain)
        found = []
        for entity_id in wanted:
            entity = self.entities.get(entity_id)
            if entity is None:
                _LOGGER.warning("No input helper %s", entity_id)
                continue
            found.append(entity)
        return found

    async def async_load(self, config: dict[str, Any]) -> None:
        stored = await self.store.load() or {}
        for domain in INPUT_DOMAINS:
            block = config.get(domain)
            if not isinstance(block, dict):
                if block:
                    _LOGGER.warning("%s: expected a mapping, got %r", domain, block)
                continue
            platform = self.platforms.get(domain) or EntityPlatform(
                self.jarvis, domain, DOMAIN
            )
            self.platforms[domain] = platform
            saved = stored.get(domain) or {}
            created: list[InputEntity] = []
            for object_id, raw in block.items():
                entity = ENTITY_CLASSES[domain](self, str(object_id), raw or {})
                entity.restore(saved.get(str(object_id)))
                created.append(entity)
            if not created:
                continue
            await platform.async_add_entities(list(created))
            # The platform owns entity_id allocation, so key off it afterwards.
            for entity in created:
                self.entities[entity.entity_id] = entity

    async def async_save(self) -> None:
        data: dict[str, dict[str, Any]] = {}
        for entity in self.entities.values():
            data.setdefault(entity.input_domain, {})[entity.object_id] = entity.stored_value
        await self.store.save(data)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if jarvis.data.get(DATA_MANAGER) is not None:
        return True  # already bootstrapped (by the automation integration)

    manager = InputManager(jarvis)
    jarvis.data[DATA_MANAGER] = manager

    merged: dict[str, Any] = {}
    for domain in INPUT_DOMAINS:
        block = (jarvis.config or {}).get(domain)
        if isinstance(config, dict) and isinstance(config.get(domain), dict):
            block = {**(block or {}), **config[domain]}
        if block:
            merged[domain] = block

    await manager.async_load(merged)

    # --- services ---------------------------------------------------------
    async def _turn_on(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_BOOLEAN, call.get("entity_id")):
            await entity.async_turn_on()  # type: ignore[attr-defined]

    async def _turn_off(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_BOOLEAN, call.get("entity_id")):
            await entity.async_turn_off()  # type: ignore[attr-defined]

    async def _toggle(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_BOOLEAN, call.get("entity_id")):
            await entity.async_toggle()  # type: ignore[attr-defined]

    async def _set_number(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_NUMBER, call.get("entity_id")):
            await entity.async_set_raw(call.get("value"))

    async def _increment(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_NUMBER, call.get("entity_id")):
            await entity.async_increment()  # type: ignore[attr-defined]

    async def _decrement(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_NUMBER, call.get("entity_id")):
            await entity.async_decrement()  # type: ignore[attr-defined]

    async def _set_text(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_TEXT, call.get("entity_id")):
            await entity.async_set_raw(call.get("value"))

    async def _select_option(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_SELECT, call.get("entity_id")):
            await entity.async_set_raw(call.get("option"))

    async def _select_next(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_SELECT, call.get("entity_id")):
            await entity.async_offset(1)  # type: ignore[attr-defined]

    async def _select_previous(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_SELECT, call.get("entity_id")):
            await entity.async_offset(-1)  # type: ignore[attr-defined]

    async def _set_options(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_SELECT, call.get("entity_id")):
            await entity.async_set_options(call.get("options"))  # type: ignore[attr-defined]

    async def _set_datetime(call: ServiceCall) -> None:
        for entity in manager.resolve(DOMAIN_DATETIME, call.get("entity_id")):
            await entity.async_set_datetime(  # type: ignore[attr-defined]
                date=call.get("date"),
                time=call.get("time"),
                datetime=call.get("datetime"),
                timestamp=call.get("timestamp"),
            )

    entity_field = {"entity_id": {"description": "Target helper(s).", "required": True}}

    jarvis.services.register(
        DOMAIN_BOOLEAN, "turn_on", _turn_on, "Turn an input boolean on.", entity_field
    )
    jarvis.services.register(
        DOMAIN_BOOLEAN, "turn_off", _turn_off, "Turn an input boolean off.", entity_field
    )
    jarvis.services.register(
        DOMAIN_BOOLEAN, "toggle", _toggle, "Toggle an input boolean.", entity_field
    )
    jarvis.services.register(
        DOMAIN_NUMBER,
        "set_value",
        _set_number,
        "Set an input number.",
        {**entity_field, "value": {"description": "New value.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN_NUMBER, "increment", _increment, "Step an input number up.", entity_field
    )
    jarvis.services.register(
        DOMAIN_NUMBER, "decrement", _decrement, "Step an input number down.", entity_field
    )
    jarvis.services.register(
        DOMAIN_TEXT,
        "set_value",
        _set_text,
        "Set an input text.",
        {**entity_field, "value": {"description": "New text.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN_SELECT,
        "select_option",
        _select_option,
        "Choose an input select option.",
        {**entity_field, "option": {"description": "Option to select.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN_SELECT, "select_next", _select_next, "Select the next option.", entity_field
    )
    jarvis.services.register(
        DOMAIN_SELECT,
        "select_previous",
        _select_previous,
        "Select the previous option.",
        entity_field,
    )
    jarvis.services.register(
        DOMAIN_SELECT,
        "set_options",
        _set_options,
        "Replace an input select's options.",
        {**entity_field, "options": {"description": "New options.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN_DATETIME,
        "set_datetime",
        _set_datetime,
        "Set an input datetime.",
        {
            **entity_field,
            "date": {"description": "YYYY-MM-DD"},
            "time": {"description": "HH:MM:SS"},
            "datetime": {"description": "YYYY-MM-DD HH:MM:SS"},
            "timestamp": {"description": "UNIX timestamp"},
        },
    )
    return True


__all__ = ["DOMAIN", "InputManager", "async_setup"]
