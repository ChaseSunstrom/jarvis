"""Entity base class + platform helper.

An integration creates Entity subclasses and hands them to
`EntityPlatform.async_add_entities`. The platform registers them (stable
unique_id → entity_id), writes their first state, and drives polling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    ATTR_SUPPORTED_FEATURES,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from .state import slugify

if TYPE_CHECKING:  # pragma: no cover
    from .core import Jarvis

_LOGGER = logging.getLogger(__name__)


class Entity:
    """Base entity. Subclasses set `_attr_*` or override the properties."""

    # populated by EntityPlatform
    jarvis: "Jarvis" = None  # type: ignore[assignment]
    entity_id: str = ""
    platform_name: str = ""

    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_state: Any = STATE_UNKNOWN
    _attr_available: bool = True
    _attr_icon: str | None = None
    _attr_device_class: str | None = None
    _attr_unit_of_measurement: str | None = None
    _attr_supported_features: int = 0
    _attr_extra_attributes: dict[str, Any] | None = None
    _attr_device_info: dict[str, Any] | None = None
    _attr_should_poll: bool = False

    @property
    def name(self) -> str:
        return self._attr_name or self.__class__.__name__

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def state(self) -> Any:
        return self._attr_state

    @property
    def available(self) -> bool:
        return self._attr_available

    @property
    def icon(self) -> str | None:
        return self._attr_icon

    @property
    def device_class(self) -> str | None:
        return self._attr_device_class

    @property
    def unit_of_measurement(self) -> str | None:
        return self._attr_unit_of_measurement

    @property
    def supported_features(self) -> int:
        return self._attr_supported_features

    @property
    def should_poll(self) -> bool:
        return self._attr_should_poll

    @property
    def device_info(self) -> dict[str, Any] | None:
        return self._attr_device_info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._attr_extra_attributes or {})

    # --- lifecycle --------------------------------------------------------
    async def async_added_to_jarvis(self) -> None:
        """Called once the entity has an entity_id and is on the bus."""

    async def async_will_remove(self) -> None:
        """Called before the entity is removed."""

    async def async_update(self) -> None:
        """Fetch new data (only called when should_poll is True)."""

    # --- state writing ----------------------------------------------------
    def state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {ATTR_FRIENDLY_NAME: self.name}
        if self.icon:
            attrs[ATTR_ICON] = self.icon
        if self.device_class:
            attrs[ATTR_DEVICE_CLASS] = self.device_class
        if self.unit_of_measurement:
            attrs[ATTR_UNIT_OF_MEASUREMENT] = self.unit_of_measurement
        if self.supported_features:
            attrs[ATTR_SUPPORTED_FEATURES] = self.supported_features
        attrs.update(self.extra_state_attributes)
        return {k: v for k, v in attrs.items() if v is not None}

    def async_write_state(self) -> None:
        """Push the entity's current state onto the state machine."""
        if not self.entity_id or self.jarvis is None:
            return
        state = STATE_UNAVAILABLE if not self.available else self.state
        if state is None:
            state = STATE_UNKNOWN
        self.jarvis.states.set(self.entity_id, state, self.state_attributes())

    async def async_update_state(self) -> None:
        """Poll then write (used by the platform's poll loop)."""
        try:
            await self.async_update()
            self._attr_available = True
        except Exception as exc:
            # An unreachable service is the NORMAL failure here, not a defect:
            # `binary_sensor.ollama_up` exists precisely to report that Ollama
            # is down, and dumping a twenty-frame httpx traceback to say so
            # buried the real log on a first run. Going unavailable IS the
            # answer the entity was asked for.
            #
            # The distinction is fault. A connection that could not be made is
            # the world being the way it is; anything else is this code being
            # wrong, and still gets its traceback.
            if isinstance(exc, _EXPECTED_UPDATE_ERRORS):
                if self._attr_available:  # the transition, not every poll after
                    _LOGGER.warning(
                        "%s is unavailable: %s",
                        self.entity_id or self.name,
                        _brief(exc),
                    )
                else:
                    _LOGGER.debug(
                        "%s still unavailable: %s",
                        self.entity_id or self.name,
                        _brief(exc),
                    )
            else:
                _LOGGER.exception("Error updating %s", self.entity_id or self.name)
            self._attr_available = False
        self.async_write_state()



#: Failures meaning "the thing this entity watches is not there", which is
#: information rather than a malfunction. OSError covers ConnectionError and
#: socket errors, and httpx's connect failures subclass it, so this catches
#: them without importing httpx here.
_EXPECTED_UPDATE_ERRORS = (OSError, asyncio.TimeoutError)


def _brief(exc: BaseException) -> str:
    """One line: the type, plus the message when it adds anything."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class EntityPlatform:
    """Adds entities for one integration+domain and drives their polling."""

    def __init__(
        self,
        jarvis: "Jarvis",
        domain: str,
        platform_name: str,
        scan_interval: float = 30.0,
    ) -> None:
        self.jarvis = jarvis
        self.domain = domain
        self.platform_name = platform_name
        self.scan_interval = scan_interval
        self.entities: dict[str, Entity] = {}
        self._poll_task: asyncio.Task | None = None

    async def async_add_entities(
        self, entities: list[Entity], update_before_add: bool = False
    ) -> None:
        for entity in entities:
            entity.jarvis = self.jarvis
            entity.platform_name = self.platform_name

            unique_id = entity.unique_id or slugify(f"{self.platform_name}_{entity.name}")
            device_id = None
            info = entity.device_info
            if info:
                device = await self.jarvis.devices.async_get_or_create(
                    identifiers=list(info.get("identifiers", [])) or [unique_id],
                    name=info.get("name", entity.name),
                    platform=self.platform_name,
                    manufacturer=info.get("manufacturer"),
                    model=info.get("model"),
                    sw_version=info.get("sw_version"),
                )
                device_id = device.id

            entry = await self.jarvis.entities.async_get_or_create(
                domain=self.domain,
                platform=self.platform_name,
                unique_id=unique_id,
                suggested_object_id=entity.name,
                name=entity.name,
                device_id=device_id,
            )
            entity.entity_id = entry.entity_id
            self.entities[entry.entity_id] = entity
            self.jarvis.data.setdefault("entity_objects", {})[entry.entity_id] = entity

            if update_before_add:
                await entity.async_update_state()
            else:
                entity.async_write_state()
            await entity.async_added_to_jarvis()

        if any(e.should_poll for e in self.entities.values()) and self._poll_task is None:
            self._poll_task = self.jarvis.async_create_task(self._poll_loop())

    async def async_remove_entity(self, entity_id: str) -> None:
        entity = self.entities.pop(entity_id, None)
        if entity is None:
            return
        await entity.async_will_remove()
        self.jarvis.data.get("entity_objects", {}).pop(entity_id, None)
        self.jarvis.states.remove(entity_id)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.scan_interval)
            for entity in list(self.entities.values()):
                if entity.should_poll:
                    await entity.async_update_state()

    async def async_shutdown(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
