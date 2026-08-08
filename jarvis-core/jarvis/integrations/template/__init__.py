"""Template integration — entities whose state is a Jinja expression.

    template:
      - sensor:
          - name: Average Temperature
            state: "{{ ((states('sensor.a')|float + states('sensor.b')|float) / 2) | round(1) }}"
            unit_of_measurement: "°C"
            device_class: temperature
            attributes:
              inputs: "{{ ['sensor.a', 'sensor.b'] }}"
        binary_sensor:
          - name: Anyone Home
            state: "{{ is_state('person.sam', 'home') }}"
            device_class: presence
        switch:
          - name: Study Lamp Proxy
            state: "{{ is_state('light.study', 'on') }}"
            turn_on:
              service: light.turn_on
              data: {entity_id: light.study}
            turn_off:
              service: light.turn_off
              data: {entity_id: light.study}

Every template entity re-renders whenever any *other* entity's state
changes (changes to template entities themselves are ignored, which is what
keeps the graph from feeding itself).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...const import EVENT_JARVIS_START, EVENT_STATE_CHANGED, STATE_OFF, STATE_ON, STATE_UNKNOWN
from ...entity import Entity, EntityPlatform
from ...helpers.template import (
    TemplateError,
    render,
    render_complex,
    result_as_boolean,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...bus import Context, Event
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "template"

PLATFORM_KEYS = ("sensor", "binary_sensor", "switch", "button")


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------
async def async_run_actions(
    jarvis: "Jarvis",
    action: Any,
    variables: dict[str, Any] | None = None,
    context: "Context | None" = None,
) -> None:
    """Run a `service:`-style action (or a list of them)."""
    if action is None:
        return
    steps = action if isinstance(action, list) else [action]
    for step in steps:
        if not isinstance(step, dict):
            continue
        service = step.get("service") or step.get("action")
        if not service or "." not in str(service):
            _LOGGER.error("template action needs a 'service: domain.name': %r", step)
            continue
        domain, _, name = str(service).partition(".")
        data: dict[str, Any] = {}
        for key in ("data", "data_template", "service_data"):
            payload = step.get(key)
            if isinstance(payload, dict):
                data.update(render_complex(jarvis, payload, variables))
        target = step.get("target")
        if isinstance(target, dict):
            data.update(render_complex(jarvis, target, variables))
        for key in ("entity_id", "area_id", "device_id"):
            if key in step:
                data[key] = render_complex(jarvis, step[key], variables)
        await jarvis.async_call_service(domain, name, data, context=context)


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------
class TemplateEntity(Entity):
    """An entity whose state (and attributes) come from templates."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        self.jarvis = jarvis
        self._config = config
        self._attr_name = config.get("name") or "Template"
        self._attr_unique_id = config.get("unique_id")
        self._attr_device_class = config.get("device_class")
        self._attr_should_poll = False
        self._state_template = config.get("state") or config.get("value_template")
        self._icon_template = config.get("icon_template")
        self._attr_icon = config.get("icon")
        self._availability_template = config.get("availability") or config.get(
            "availability_template"
        )
        self._attribute_templates: dict[str, Any] = dict(config.get("attributes") or {})
        self._attr_extra_attributes = {}

    def _variables(self) -> dict[str, Any]:
        return {"this": self.entity_id}

    def _render_state(self) -> Any:
        if not self._state_template:
            return STATE_UNKNOWN
        return render(self.jarvis, self._state_template, self._variables())

    def async_render(self) -> bool:
        """Re-render state, attributes and icon. True if anything changed."""
        before = (
            self._attr_state,
            self._attr_available,
            dict(self._attr_extra_attributes or {}),
        )
        try:
            available = True
            if self._availability_template:
                available = result_as_boolean(
                    render(self.jarvis, self._availability_template, self._variables())
                )
            self._attr_available = available
            if available:
                self._attr_state = self._render_state()
                self._attr_extra_attributes = {
                    key: render_complex(self.jarvis, template, self._variables())
                    for key, template in self._attribute_templates.items()
                }
                if self._icon_template:
                    self._attr_icon = render(
                        self.jarvis, self._icon_template, self._variables()
                    )
        except TemplateError as exc:
            _LOGGER.warning("Template entity %s: %s", self._attr_name, exc)
            self._attr_available = False
        after = (
            self._attr_state,
            self._attr_available,
            dict(self._attr_extra_attributes or {}),
        )
        return before != after

    def async_render_and_write(self) -> None:
        self.async_render()
        self.async_write_state()


class TemplateSensor(TemplateEntity):
    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._attr_unit_of_measurement = config.get("unit_of_measurement") or config.get(
            "unit"
        )


class TemplateBinarySensor(TemplateEntity):
    def _render_state(self) -> Any:
        if not self._state_template:
            return STATE_UNKNOWN
        rendered = render(self.jarvis, self._state_template, self._variables())
        return STATE_ON if result_as_boolean(rendered) else STATE_OFF


class TemplateSwitch(TemplateEntity):
    """A switch backed by an optional state template plus on/off actions."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._turn_on = config.get("turn_on")
        self._turn_off = config.get("turn_off")
        self._optimistic_state = STATE_OFF

    def _render_state(self) -> Any:
        if not self._state_template:
            return self._optimistic_state
        rendered = render(self.jarvis, self._state_template, self._variables())
        return STATE_ON if result_as_boolean(rendered) else STATE_OFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._optimistic_state = STATE_ON
        self._attr_state = STATE_ON
        await async_run_actions(self.jarvis, self._turn_on, self._variables())
        self.async_render()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._optimistic_state = STATE_OFF
        self._attr_state = STATE_OFF
        await async_run_actions(self.jarvis, self._turn_off, self._variables())
        self.async_render()

    async def async_toggle(self, **kwargs: Any) -> None:
        if self._attr_state == STATE_ON:
            await self.async_turn_off()
        else:
            await self.async_turn_on()


class TemplateButton(TemplateEntity):
    def __init__(self, jarvis: "Jarvis", config: dict[str, Any]) -> None:
        super().__init__(jarvis, config)
        self._press_action = config.get("press") or config.get("turn_on")
        self._attr_state = STATE_UNKNOWN

    def _render_state(self) -> Any:
        return self._attr_state

    async def async_press(self, **kwargs: Any) -> None:
        await async_run_actions(self.jarvis, self._press_action, self._variables())


_ENTITY_TYPES: dict[str, type[TemplateEntity]] = {
    "sensor": TemplateSensor,
    "binary_sensor": TemplateBinarySensor,
    "switch": TemplateSwitch,
    "button": TemplateButton,
}


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------
class TemplateTracker:
    """Re-renders every template entity when foreign state changes."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.entities: list[TemplateEntity] = []
        self._own_ids: set[str] = set()
        self._unsubs: list[Any] = []
        self._rendering = False

    def add(self, entities: list[TemplateEntity]) -> None:
        for entity in entities:
            self.entities.append(entity)
            if entity.entity_id:
                self._own_ids.add(entity.entity_id)

    def start(self) -> None:
        if self._unsubs:
            return
        self._unsubs.append(
            self.jarvis.bus.listen(EVENT_STATE_CHANGED, self._handle_state_changed)
        )
        self._unsubs.append(
            self.jarvis.bus.listen(EVENT_JARVIS_START, lambda event: self.render_all())
        )
        self.jarvis.register_shutdown(self.stop)

    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def _handle_state_changed(self, event: "Event") -> None:
        entity_id = event.data.get("entity_id")
        if entity_id in self._own_ids or self._rendering:
            return
        self.render_all()

    def render_all(self) -> None:
        self._rendering = True
        try:
            for entity in self.entities:
                self._own_ids.add(entity.entity_id)
                entity.async_render_and_write()
        finally:
            self._rendering = False


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def _as_blocks(config: Any) -> list[dict[str, Any]]:
    if config is None:
        return []
    if isinstance(config, dict):
        return [config]
    return [block for block in config if isinstance(block, dict)]


def _as_entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        # `sensor: {name: ..., state: ...}` or `sensor: {slug: {...}}`
        if any(key in value for key in ("name", "state", "value_template")):
            return [value]
        entries = []
        for key, entry in value.items():
            if isinstance(entry, dict):
                entries.append({"name": entry.get("name", key), **entry})
        return entries
    return [entry for entry in value if isinstance(entry, dict)]


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    blocks = _as_blocks(config)
    if not blocks:
        return True

    store = jarvis.data.setdefault(DOMAIN, {})
    tracker: TemplateTracker = store.get("tracker") or TemplateTracker(jarvis)
    store["tracker"] = tracker
    platforms: dict[str, EntityPlatform] = store.setdefault("platforms", {})

    total = 0
    for block in blocks:
        for domain in PLATFORM_KEYS:
            entries = _as_entries(block.get(domain))
            if not entries:
                continue
            platform = platforms.get(domain)
            if platform is None:
                platform = EntityPlatform(jarvis, domain, DOMAIN)
                platforms[domain] = platform

            entities = [_ENTITY_TYPES[domain](jarvis, entry) for entry in entries]
            for entity in entities:
                entity.async_render()
            await platform.async_add_entities(entities)
            tracker.add(entities)
            total += len(entities)

    tracker.start()
    tracker.render_all()
    _LOGGER.info("Template: %d entities", total)
    return True
