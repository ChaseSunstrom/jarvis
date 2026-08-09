"""The Jarvis object — bus, states, services, registries, config, lifecycle.

Everything an integration needs is hung off this one object (deliberately
familiar if you've written Home Assistant integrations).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from .bus import Context, EventBus
from .const import EVENT_JARVIS_START, EVENT_JARVIS_STOP
from .registry import AreaRegistry, DeviceRegistry, EntityRegistry
from .services import ServiceRegistry
from .settings import SettingsOverlay
from .state import StateMachine
from .store import Store

_LOGGER = logging.getLogger(__name__)


class Jarvis:
    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.config: dict[str, Any] = {}
        #: The configuration exactly as the files give it, before the overlay.
        #: Kept so the console can show what a setting would revert to.
        self.raw_config: dict[str, Any] = {}
        self.bus = EventBus()
        self.states = StateMachine(self.bus)
        self.services = ServiceRegistry(self.bus)
        self.areas = AreaRegistry(self.bus, Store(self.config_dir, "area_registry"))
        self.devices = DeviceRegistry(self.bus, Store(self.config_dir, "device_registry"))
        self.entities = EntityRegistry(self.bus, Store(self.config_dir, "entity_registry"))
        # An attribute beside the registries rather than a `data` key: the
        # reload services have to reach it without importing an integration,
        # and it has the same lifecycle as the rest of the core infrastructure.
        # `data` stays what its comment below says it is.
        self.settings = SettingsOverlay(self.config_dir)
        # Free-form scratch space shared by integrations (keyed by domain).
        self.data: dict[str, Any] = {}
        self.is_running = False
        self._tasks: set[asyncio.Task] = set()
        self._shutdown_callbacks: list[Any] = []

    # --- task helpers -----------------------------------------------------
    def async_create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def register_shutdown(self, callback: Any) -> None:
        self._shutdown_callbacks.append(callback)

    # --- convenience ------------------------------------------------------
    async def async_call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        context: Context | None = None,
        return_response: bool = False,
    ) -> Any:
        return await self.services.async_call(
            domain, service, data, blocking=True, context=context,
            return_response=return_response,
        )

    def entity_object(self, entity_id: str) -> Any:
        """The live Entity instance behind an entity_id (if any)."""
        return self.data.get("entity_objects", {}).get(entity_id)

    def area_for_entity(self, entity_id: str) -> str | None:
        entry = self.entities.get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            device = self.devices.devices.get(entry.device_id)
            if device:
                return device.area_id
        return None

    # --- lifecycle --------------------------------------------------------
    async def async_install_config(
        self, config: dict[str, Any], package_provenance: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Merge the settings overlay over `config` and adopt the result.

        Returns the overlaid dict, and callers must use the return value rather
        than the dict they passed in. That is the whole point: integrations are
        built from the dict handed to `async_setup_integrations`, not from
        `self.config`, so an overlay applied only to the attribute would leave
        every LLM and voice setting inert at boot — the console would report a
        model that nothing was running.
        """
        self.raw_config = config
        merged, _unapplied = self.settings.apply(config, package_provenance or {})
        self.config = merged
        return merged

    async def async_setup(
        self,
        config: dict[str, Any],
        package_provenance: dict[str, str] | None = None,
    ) -> None:
        """Load registries and set up every configured integration."""
        await self.settings.async_load()
        # Rebinding the local on purpose — see async_install_config. Both the
        # areas loop below and async_setup_integrations must see the overlay.
        config = await self.async_install_config(config, package_provenance)
        await asyncio.gather(
            self.areas.load(), self.devices.load(), self.entities.load()
        )
        # Areas declared in YAML are created up-front so integrations can use them.
        for area in config.get("jarvis", {}).get("areas", []) or []:
            if isinstance(area, str):
                await self.areas.create(area)
            elif isinstance(area, dict) and area.get("name"):
                await self.areas.create(area["name"], area.get("aliases"))

        from .integrations import async_setup_integrations  # local: avoids cycle

        await async_setup_integrations(self, config)

    async def async_start(self) -> None:
        self.is_running = True
        await self.bus.async_fire(EVENT_JARVIS_START)
        _LOGGER.info("Jarvis started with %d entities", len(self.states.all()))

    async def async_stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        await self.bus.async_fire(EVENT_JARVIS_STOP)
        for callback in reversed(self._shutdown_callbacks):
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _LOGGER.exception("Error during shutdown callback")
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await asyncio.gather(
            self.areas.save(), self.devices.save(), self.entities.save()
        )
        _LOGGER.info("Jarvis stopped")

    async def async_block_till_done(self) -> None:
        await self.bus.async_block_till_done()
