"""Device / entity / area registries — the stable identity layer.

Entities get an `entity_id` (may be renamed), a stable `unique_id` per
platform, and optional device + area membership. This is what makes
"turn on the kitchen lights" resolvable and what the management UI edits.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .bus import EventBus
from .const import (
    EVENT_AREA_REGISTRY_UPDATED,
    EVENT_DEVICE_REGISTRY_UPDATED,
    EVENT_ENTITY_REGISTRY_UPDATED,
)
from .state import slugify, split_entity_id, valid_entity_id
from .store import Store


@dataclass(slots=True)
class AreaEntry:
    id: str
    name: str
    aliases: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DeviceEntry:
    id: str
    name: str
    manufacturer: str | None = None
    model: str | None = None
    sw_version: str | None = None
    identifiers: list[str] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    area_id: str | None = None
    platform: str | None = None
    via_device_id: str | None = None
    disabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EntityEntry:
    entity_id: str
    unique_id: str
    platform: str
    name: str | None = None
    original_name: str | None = None
    device_id: str | None = None
    area_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    icon: str | None = None
    disabled: bool = False
    hidden: bool = False
    exposed: bool = True  # visible to the LLM / voice assistant
    capabilities: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AreaRegistry:
    def __init__(self, bus: EventBus, store: Store) -> None:
        self._bus, self._store = bus, store
        self.areas: dict[str, AreaEntry] = {}

    async def load(self) -> None:
        data = await self._store.load() or {}
        for raw in data.get("areas", []):
            self.areas[raw["id"]] = AreaEntry(**raw)

    async def save(self) -> None:
        await self._store.save({"areas": [a.as_dict() for a in self.areas.values()]})

    def get_by_name(self, name: str) -> AreaEntry | None:
        target = name.strip().lower()
        for area in self.areas.values():
            if area.name.lower() == target or target in [a.lower() for a in area.aliases]:
                return area
        return None

    async def create(self, name: str, aliases: list[str] | None = None) -> AreaEntry:
        existing = self.get_by_name(name)
        if existing:
            return existing
        area = AreaEntry(slugify(name), name, aliases or [])
        self.areas[area.id] = area
        self._bus.fire(EVENT_AREA_REGISTRY_UPDATED, {"action": "create", "area_id": area.id})
        await self.save()
        return area

    async def update(self, area_id: str, **changes: Any) -> AreaEntry | None:
        area = self.areas.get(area_id)
        if area is None:
            return None
        for key, value in changes.items():
            if hasattr(area, key) and value is not None:
                setattr(area, key, value)
        self._bus.fire(EVENT_AREA_REGISTRY_UPDATED, {"action": "update", "area_id": area_id})
        await self.save()
        return area

    async def delete(self, area_id: str) -> bool:
        if self.areas.pop(area_id, None) is None:
            return False
        self._bus.fire(EVENT_AREA_REGISTRY_UPDATED, {"action": "remove", "area_id": area_id})
        await self.save()
        return True


class DeviceRegistry:
    def __init__(self, bus: EventBus, store: Store) -> None:
        self._bus, self._store = bus, store
        self.devices: dict[str, DeviceEntry] = {}

    async def load(self) -> None:
        data = await self._store.load() or {}
        for raw in data.get("devices", []):
            self.devices[raw["id"]] = DeviceEntry(**raw)

    async def save(self) -> None:
        await self._store.save({"devices": [d.as_dict() for d in self.devices.values()]})

    def get_by_identifier(self, identifier: str) -> DeviceEntry | None:
        for device in self.devices.values():
            if identifier in device.identifiers:
                return device
        return None

    async def async_get_or_create(
        self,
        identifiers: list[str],
        name: str,
        platform: str,
        manufacturer: str | None = None,
        model: str | None = None,
        sw_version: str | None = None,
        area_id: str | None = None,
    ) -> DeviceEntry:
        for identifier in identifiers:
            existing = self.get_by_identifier(identifier)
            if existing:
                # keep metadata fresh
                existing.name = name or existing.name
                existing.manufacturer = manufacturer or existing.manufacturer
                existing.model = model or existing.model
                existing.sw_version = sw_version or existing.sw_version
                await self.save()
                return existing
        device = DeviceEntry(
            id=uuid.uuid4().hex[:12],
            name=name,
            manufacturer=manufacturer,
            model=model,
            sw_version=sw_version,
            identifiers=list(identifiers),
            area_id=area_id,
            platform=platform,
        )
        self.devices[device.id] = device
        self._bus.fire(
            EVENT_DEVICE_REGISTRY_UPDATED, {"action": "create", "device_id": device.id}
        )
        await self.save()
        return device

    async def update(self, device_id: str, **changes: Any) -> DeviceEntry | None:
        device = self.devices.get(device_id)
        if device is None:
            return None
        for key, value in changes.items():
            if hasattr(device, key) and value is not None:
                setattr(device, key, value)
        self._bus.fire(
            EVENT_DEVICE_REGISTRY_UPDATED, {"action": "update", "device_id": device_id}
        )
        await self.save()
        return device

    async def remove(self, device_id: str) -> bool:
        """Forget a device. The entities that hang off it are the caller's to
        remove first (`Jarvis.async_remove_device` does); this only drops the
        record, or an entity would keep naming a device that is not there."""
        if self.devices.pop(device_id, None) is None:
            return False
        self._bus.fire(
            EVENT_DEVICE_REGISTRY_UPDATED, {"action": "remove", "device_id": device_id}
        )
        await self.save()
        return True


class EntityRegistry:
    def __init__(self, bus: EventBus, store: Store) -> None:
        self._bus, self._store = bus, store
        self.entities: dict[str, EntityEntry] = {}

    async def load(self) -> None:
        data = await self._store.load() or {}
        for raw in data.get("entities", []):
            self.entities[raw["entity_id"]] = EntityEntry(**raw)

    async def save(self) -> None:
        await self._store.save(
            {"entities": [e.as_dict() for e in self.entities.values()]}
        )

    def get(self, entity_id: str) -> EntityEntry | None:
        return self.entities.get(entity_id)

    def get_by_unique_id(self, platform: str, unique_id: str) -> EntityEntry | None:
        for entry in self.entities.values():
            if entry.platform == platform and entry.unique_id == unique_id:
                return entry
        return None

    def entities_in_area(self, area_id: str, devices: DeviceRegistry) -> list[EntityEntry]:
        out = []
        for entry in self.entities.values():
            if entry.area_id == area_id:
                out.append(entry)
            elif entry.device_id:
                device = devices.devices.get(entry.device_id)
                if device and device.area_id == area_id:
                    out.append(entry)
        return out

    async def async_get_or_create(
        self,
        domain: str,
        platform: str,
        unique_id: str,
        suggested_object_id: str,
        name: str | None = None,
        device_id: str | None = None,
        area_id: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> EntityEntry:
        existing = self.get_by_unique_id(platform, unique_id)
        if existing:
            if capabilities:
                existing.capabilities = capabilities
            return existing

        base = slugify(suggested_object_id)
        entity_id = f"{domain}.{base}"
        suffix = 2
        while entity_id in self.entities:
            entity_id = f"{domain}.{base}_{suffix}"
            suffix += 1

        entry = EntityEntry(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=platform,
            original_name=name,
            device_id=device_id,
            area_id=area_id,
            capabilities=capabilities or {},
        )
        self.entities[entity_id] = entry
        self._bus.fire(
            EVENT_ENTITY_REGISTRY_UPDATED, {"action": "create", "entity_id": entity_id}
        )
        await self.save()
        return entry

    async def update(self, entity_id: str, **changes: Any) -> EntityEntry | None:
        entry = self.entities.get(entity_id)
        if entry is None:
            return None
        for key, value in changes.items():
            if hasattr(entry, key) and value is not None:
                setattr(entry, key, value)
        self._bus.fire(
            EVENT_ENTITY_REGISTRY_UPDATED, {"action": "update", "entity_id": entity_id}
        )
        await self.save()
        return entry

    async def rename(self, entity_id: str, new_entity_id: str) -> "EntityEntry | None":
        """Give an entity a new `entity_id`. Returns the entry, or None.

        Raises `ValueError` with a sentence when the new id is unusable —
        malformed, already taken, or in a different domain. That last one is
        not fussiness: the domain is what decides which services an entity
        accepts, so `light.x` renamed to `switch.x` would be an entity whose
        id promises `switch.turn_on` and whose platform does not implement it.

        The state moves too. A registry entry under a new id whose state is
        still under the old one is an entity that exists twice and works
        neither way.
        """
        entry = self.entities.get(entity_id)
        if entry is None:
            return None
        new_entity_id = str(new_entity_id or "").strip().lower()
        if new_entity_id == entity_id:
            return entry
        if not valid_entity_id(new_entity_id):
            raise ValueError(
                f"{new_entity_id!r} is not a valid entity_id — it has to be "
                "`domain.object_id`, lowercase, with letters, digits and "
                "underscores."
            )
        if new_entity_id in self.entities:
            raise ValueError(f"{new_entity_id} already exists.")
        old_domain, _ = split_entity_id(entity_id)
        new_domain, _ = split_entity_id(new_entity_id)
        if old_domain != new_domain:
            raise ValueError(
                f"an entity cannot move between domains: {entity_id} is a "
                f"{old_domain}, and {new_entity_id} would be a {new_domain}."
            )

        del self.entities[entity_id]
        entry.entity_id = new_entity_id
        self.entities[new_entity_id] = entry
        self._bus.fire(
            EVENT_ENTITY_REGISTRY_UPDATED,
            {
                "action": "update",
                "entity_id": new_entity_id,
                # Named so a listener can follow the move rather than seeing
                # one entity vanish and an unrelated one appear.
                "old_entity_id": entity_id,
            },
        )
        await self.save()
        return entry

    async def remove(self, entity_id: str) -> bool:
        if self.entities.pop(entity_id, None) is None:
            return False
        self._bus.fire(
            EVENT_ENTITY_REGISTRY_UPDATED, {"action": "remove", "entity_id": entity_id}
        )
        await self.save()
        return True
