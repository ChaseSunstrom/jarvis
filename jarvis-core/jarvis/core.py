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
from .taskengine import TaskEngine
from .tasks import STORE_KEY as TASKS_STORE_KEY, TaskRegistry

_LOGGER = logging.getLogger(__name__)


class Jarvis:
    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).resolve()
        self.config: dict[str, Any] = {}
        #: The configuration exactly as the files give it, before the overlay.
        #: Kept so the console can show what a setting would revert to.
        self.raw_config: dict[str, Any] = {}
        #: Which package supplied which top-level key. The console needs it to
        #: answer "why can I not change this here" with the file to edit.
        self.package_provenance: dict[str, str] = {}
        self.bus = EventBus()
        self.states = StateMachine(self.bus)
        self.services = ServiceRegistry(self.bus)
        self.areas = AreaRegistry(self.bus, Store(self.config_dir, "area_registry"))
        self.devices = DeviceRegistry(self.bus, Store(self.config_dir, "device_registry"))
        self.entities = EntityRegistry(self.bus, Store(self.config_dir, "entity_registry"))
        # Long work, on one list. Beside the registries rather than in `data`
        # for the same reason `settings` is: four unrelated things report
        # through it — a task the model accepted, a research job, something
        # scheduled, a coding job — and none of them should have to import an
        # integration to find it, nor be ordered after one that might not be
        # configured.
        self.tasks = TaskRegistry(self, Store(self.config_dir, TASKS_STORE_KEY))
        # And the thing that actually runs what the registry records. Beside it
        # for the same reason: every kind of slow work goes through one queue,
        # and a worker should not have to find an integration to be run by it.
        # `llm.max_concurrent` sets the width; the engine is built with the
        # default and re-sized when that integration sets up.
        self.taskengine = TaskEngine(self, self.tasks)
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

    # --- taking things out of the house (M69) -------------------------------
    async def async_remove_entity(
        self, entity_id: str, context: Context | None = None
    ) -> dict[str, Any]:
        """Take one entity out of the house: its live object, its state, its
        registry entry. The one delete path.

        The console's `config/entity_registry/remove`, its REST twin and the
        assistant's `remove_entities` all come here, so "removed" cannot mean
        three different things: the platform's live object is told and
        dropped (so polling cannot write the state back), the state machine
        fires `state_changed` with no new state (so every surface drops the
        row live and a dashboard tile says it is gone), and the registry entry
        is gone and saved gone — which is what takes it out of the exposure
        list and the house summary the model reads, both of which are derived.

        What it does NOT do: keep an integration that recreates the entity on
        its next update from doing so. A thing that keeps publishing comes
        back, under a fresh registry entry; remove its device, or the
        integration, to keep it gone. And automations that name the id are
        not edited — they keep naming something that no longer answers, which
        the automation check reports.
        """
        entity_id = str(entity_id or "").strip().lower()
        had_state = self.states.get(entity_id) is not None
        outcome: dict[str, Any] = {
            "entity_id": entity_id,
            "object": False,
            "state": False,
            "registry": False,
        }
        entity = self.entity_object(entity_id)
        if entity is not None:
            remover = getattr(getattr(entity, "platform", None), "async_remove_entity", None)
            if callable(remover):
                await remover(entity_id)
            else:
                self.data.get("entity_objects", {}).pop(entity_id, None)
            outcome["object"] = True
        # After the object, so a platform that removed the state itself is
        # not double-counted and one that did not still loses it here.
        outcome["state"] = had_state and (
            self.states.remove(entity_id, context) or self.states.get(entity_id) is None
        )
        outcome["registry"] = await self.entities.remove(entity_id)
        outcome["removed"] = bool(outcome["object"] or outcome["state"] or outcome["registry"])
        if outcome["removed"]:
            _LOGGER.info("Removed %s from the house", entity_id)
        return outcome

    async def async_remove_device(
        self, device_id: str, context: Context | None = None
    ) -> dict[str, Any]:
        """Take a device out of the house, and every entity that hangs off it.

        Entities first, through `async_remove_entity`, then the device record
        — the other order would leave entities naming a device that is gone.
        """
        device_id = str(device_id or "").strip()
        device = self.devices.devices.get(device_id)
        if device is None:
            return {"device_id": device_id, "removed": False, "entities": []}
        entity_ids = sorted(
            entry.entity_id
            for entry in list(self.entities.entities.values())
            if entry.device_id == device_id
        )
        for entity_id in entity_ids:
            await self.async_remove_entity(entity_id, context)
        await self.devices.remove(device_id)
        _LOGGER.info("Removed device %s (%s) and %d entities", device_id, device.name, len(entity_ids))
        return {
            "device_id": device_id,
            "name": device.name,
            "removed": True,
            "entities": entity_ids,
        }

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
        self.package_provenance = package_provenance or {}
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
        # Before the integrations, so anything that wants to record work at
        # setup finds a loaded list — and so a task interrupted by the last
        # shutdown is marked as such before a surface can read it half-loaded.
        await self.tasks.async_load()
        # The queue lives in the same store as the tasks, so work that was
        # WAITING when the process died is still waiting. Work that was running
        # is only resumed if it said it was idempotent (see taskengine.py).
        self.taskengine.load(await self.tasks.store.load() or {})
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
        # The pump starts with the house, not with the first submission: a
        # queue restored by `taskengine.load` at setup sat "queued — picked
        # back up after a restart" on the nineteenth house (27 Aug 2026) for
        # eight minutes, because only `submit` ever started it. By now every
        # integration has registered its kinds, so what was restored can run.
        self.taskengine.start()
        _LOGGER.info("Jarvis started with %d entities", len(self.states.all()))

    async def async_stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        await self.taskengine.async_stop()
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
