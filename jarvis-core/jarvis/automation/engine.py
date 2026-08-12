"""Automation objects and the manager that owns them.

An :class:`Automation` wires triggers -> conditions -> actions, applies the
run ``mode`` (single/restart/queued/parallel) and keeps an
``automation.<slug>`` entity in sync (state ``on``/``off``, attributes
``last_triggered`` / ``current`` / ``mode``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..bus import Context
from ..const import EVENT_AUTOMATION_TRIGGERED, STATE_OFF, STATE_ON
from ..entity import Entity, EntityPlatform
from ..state import slugify
from .actions import ScriptRunner
from .conditions import async_check_all
from .triggers import async_attach_triggers
from .reach import part_of
from .util import as_list, result_as_boolean

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "automation"
DEFAULT_MAX = 10
MODES = ("single", "restart", "queued", "parallel")

ATTR_LAST_TRIGGERED = "last_triggered"
ATTR_CURRENT = "current"
ATTR_MODE = "mode"
ATTR_ID = "id"


# ---------------------------------------------------------------------------
# run modes
# ---------------------------------------------------------------------------
class ModeController:
    """Applies single/restart/queued/parallel semantics to a run factory.

    Shared by automations and scripts — both need the exact same bookkeeping
    (how many runs are live, whether a new one is allowed, how to cancel).
    """

    def __init__(
        self,
        jarvis: "Jarvis",
        name: str,
        mode: str = "single",
        max_runs: int = DEFAULT_MAX,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        mode = str(mode or "single").lower()
        if mode not in MODES:
            _LOGGER.warning("%s: unknown mode %r; using single", name, mode)
            mode = "single"
        self.jarvis = jarvis
        self.name = name
        self.mode = mode
        self.max_runs = max(1, int(max_runs or DEFAULT_MAX))
        self._on_change = on_change
        self._current = 0
        self._tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()

    @property
    def current(self) -> int:
        return self._current

    @property
    def is_running(self) -> bool:
        return self._current > 0

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("%s: error writing state", self.name)

    def _admit(self) -> bool:
        if self.mode == "single" and self._current > 0:
            _LOGGER.info("%s is already running (mode: single); skipping", self.name)
            return False
        if self.mode in ("queued", "parallel") and self._current >= self.max_runs:
            _LOGGER.warning(
                "%s already has %d runs (max: %d); skipping",
                self.name,
                self._current,
                self.max_runs,
            )
            return False
        return True

    def async_start(
        self, factory: Callable[[], Awaitable[Any]]
    ) -> asyncio.Task | None:
        """Admit a run and schedule it. Returns the task (None if skipped).

        The admission check and the counter bump happen *synchronously* so two
        triggers firing in the same event-loop pass can't both slip past a
        ``single`` guard.
        """
        if not self._admit():
            return None
        if self.mode == "restart":
            self.cancel()
        self._current += 1
        self._notify()
        task = self.jarvis.async_create_task(self._wrap(factory))
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task) -> None:
        """Retire a finished run, surfacing errors nobody awaited.

        The counter is decremented *here* rather than in :meth:`_wrap`'s
        ``finally``. A task cancelled before the loop ever ran its first step
        never enters the coroutine body, so no ``finally`` fires — releasing
        the slot there leaked one run per cancel-before-start, which wedged
        `single` mode permanently (``turn_off`` immediately after ``turn_on``
        is exactly that sequence: no await separates the two service calls).
        A done callback always runs, cancelled or not.
        """
        self._tasks.discard(task)
        self._current = max(0, self._current - 1)
        self._notify()
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.error("%s failed: %s", self.name, error, exc_info=error)

    async def _wrap(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        if self.mode == "queued":
            async with self._lock:
                return await factory()
        return await factory()

    def cancel(self) -> None:
        """Cancel every live run (used by `restart` and `turn_off`).

        Cancelled tasks stay in `_tasks` until their done callback fires, so
        `async_wait()` still waits for them to actually unwind and the run
        counter is released exactly once.
        """
        for task in list(self._tasks):
            if not task.done():
                task.cancel()

    async def async_wait(self) -> None:
        """Await every live run to finish (shutdown + test helper)."""
        while True:
            pending = [task for task in self._tasks if not task.done()]
            if not pending:
                # Let the done callbacks that release the run slots run before
                # callers read `current` / the entity state.
                await asyncio.sleep(0)
                return
            await asyncio.gather(*pending, return_exceptions=True)


async def async_await_run(task: asyncio.Task | None) -> Any:
    """Await a run started by :meth:`ModeController.async_start`.

    A cancelled run (mode `restart`, or a shutdown) must not cancel whoever
    was waiting on it, so that case returns ``None`` instead of propagating.
    """
    if task is None:
        return None
    try:
        return await task
    except asyncio.CancelledError:
        if task.cancelled():
            return None
        raise


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------
class AutomationEntity(Entity):
    """`automation.<slug>` — on/off plus run bookkeeping.

    Attributes with a ``None`` value are dropped by ``Entity``, so a missing
    ``last_triggered`` means "never run" rather than being reported as null.
    """

    def __init__(self, automation: "Automation") -> None:
        self._automation = automation
        # `name` decides the entity_id slug; friendly_name is overridden below.
        self._attr_name = automation.alias
        self._attr_unique_id = f"automation_{automation.unique_id}"
        self._attr_icon = "mdi:robot"

    @property
    def state(self) -> str:
        return STATE_ON if self._automation.enabled else STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        automation = self._automation
        return {
            "friendly_name": automation.alias,
            ATTR_ID: automation.automation_id,
            ATTR_LAST_TRIGGERED: automation.last_triggered,
            ATTR_MODE: automation.mode,
            ATTR_CURRENT: automation.current,
            "max": automation.max_runs,
            "description": automation.description or None,
        }


# ---------------------------------------------------------------------------
# automation
# ---------------------------------------------------------------------------
class Automation:
    """One `automation:` YAML entry."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any], index: int = 0) -> None:
        self.jarvis = jarvis
        self.config = dict(config or {})
        self.automation_id = str(
            self.config.get("id") or self.config.get("alias") or f"automation_{index}"
        )
        self.alias = str(self.config.get("alias") or self.automation_id)
        self.description = str(self.config.get("description") or "")
        self.unique_id = slugify(str(self.config.get("id") or self.alias))
        self.mode = str(self.config.get("mode", "single")).lower()
        self.max_runs = int(self.config.get("max", DEFAULT_MAX) or DEFAULT_MAX)
        # `initial_state: "off"` arrives as a *string* when it is quoted (or
        # read back from JSON), and every non-empty string is truthy — plain
        # bool() silently enabled automations the user had switched off.
        self.enabled = result_as_boolean(self.config.get("initial_state", True))
        self.last_triggered: str | None = None

        # Through `part_of` rather than inline, so that this precedence has
        # exactly ONE definition. It used to live here alone, and every reader
        # outside the engine — the approval gate, the console's automation list
        # — took the singular key by itself. An automation written with the
        # plural was therefore parsed and run by the engine while the gate
        # decided it had no actions to approve. See reach.part_of.
        self.triggers = as_list(part_of(self.config, "trigger"))
        self.conditions = as_list(part_of(self.config, "condition"))
        self.actions = as_list(part_of(self.config, "action"))
        self.base_variables = self.config.get("variables") or {}

        self.entity: AutomationEntity = AutomationEntity(self)
        self.runner = ModeController(
            jarvis, f"automation {self.alias}", self.mode, self.max_runs, self._write_state
        )
        # Report what the controller actually does, not the unvalidated YAML:
        # a typo'd `mode: paralel` runs as `single`, and the entity attribute
        # has to say so.
        self.mode = self.runner.mode
        self.max_runs = self.runner.max_runs
        self._detach_triggers: Callable[[], None] | None = None

    # --- properties -------------------------------------------------------
    @property
    def entity_id(self) -> str:
        return self.entity.entity_id

    @property
    def current(self) -> int:
        return self.runner.current

    # --- lifecycle --------------------------------------------------------
    def _write_state(self) -> None:
        self.entity.async_write_state()

    async def async_attach(self) -> None:
        """Subscribe every trigger (idempotent)."""
        await self.async_detach()
        self._detach_triggers = await async_attach_triggers(
            self.jarvis, self.triggers, self._async_trigger_fired
        )
        self._write_state()

    async def async_detach(self) -> None:
        if self._detach_triggers is not None:
            self._detach_triggers()
            self._detach_triggers = None

    async def async_remove(self) -> None:
        await self.async_detach()
        self.runner.cancel()
        if self.entity.entity_id:
            self.jarvis.states.remove(self.entity.entity_id)
            self.jarvis.data.get("entity_objects", {}).pop(self.entity.entity_id, None)

    def turn_on(self) -> None:
        self.enabled = True
        self._write_state()

    def turn_off(self, stop_actions: bool = True) -> None:
        self.enabled = False
        if stop_actions:
            self.runner.cancel()
        self._write_state()

    # --- triggering -------------------------------------------------------
    async def _async_trigger_fired(
        self, trigger: dict[str, Any], context: Context | None = None
    ) -> None:
        await self.async_trigger({"trigger": trigger}, context)

    async def async_trigger(
        self,
        variables: dict[str, Any] | None = None,
        context: Context | None = None,
        skip_condition: bool = False,
        wait: bool = False,
    ) -> Any:
        """Run this automation (conditions first, unless skipped)."""
        if not self.enabled:
            return None

        run_variables: dict[str, Any] = dict(self.base_variables)
        run_variables.update(variables or {})
        run_variables.setdefault("this", {"entity_id": self.entity_id, "alias": self.alias})

        run_context = Context(
            parent_id=context.id if context is not None else None, origin="automation"
        )

        if not skip_condition and self.conditions:
            if not await async_check_all(self.jarvis, self.conditions, run_variables):
                _LOGGER.debug("%s: conditions not met", self.alias)
                return None

        task = self.runner.async_start(
            lambda: self._async_execute(run_variables, run_context)
        )
        if wait:
            return await async_await_run(task)
        return task

    async def _async_execute(
        self, variables: dict[str, Any], context: Context
    ) -> Any:
        self.last_triggered = datetime.now().astimezone().isoformat()
        self._write_state()
        trigger = variables.get("trigger") or {}
        self.jarvis.bus.fire(
            EVENT_AUTOMATION_TRIGGERED,
            {
                "entity_id": self.entity_id,
                "name": self.alias,
                "id": self.automation_id,
                "source": trigger.get("description") if isinstance(trigger, dict) else None,
            },
            context,
        )
        runner = ScriptRunner(self.jarvis, variables, context, f"automation {self.alias}")
        try:
            return await runner.async_run(self.actions)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error running automation %s", self.alias)
            return None


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------
class AutomationManager:
    """Owns every :class:`Automation` and their entity platform."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.automations: dict[str, Automation] = {}
        self.platform = EntityPlatform(jarvis, DOMAIN, DOMAIN)

    def get(self, entity_id: str) -> Automation | None:
        return self.automations.get(entity_id)

    def all(self) -> list[Automation]:
        return list(self.automations.values())

    def resolve(self, entity_ids: Any) -> list[Automation]:
        """Entity ids (or an explicit ``"all"``/``"*"``) -> automation objects.

        An empty/absent target matches *nothing*. Fanning out to every
        automation on a missing `entity_id` would turn one malformed
        `automation.trigger` (or `turn_off`) call into a house-wide event; the
        blast radius has to be asked for by name.
        """
        wanted = [str(e) for e in as_list(entity_ids) if str(e).strip()]
        if not wanted:
            _LOGGER.warning(
                "automation service called without entity_id; "
                "pass entity_id: all to mean every automation"
            )
            return []
        if any(w in ("all", "*") for w in wanted):
            return self.all()
        found = []
        for entity_id in wanted:
            automation = self.automations.get(entity_id)
            if automation is None:
                _LOGGER.warning("No automation %s", entity_id)
                continue
            found.append(automation)
        return found

    async def async_add(self, config: dict[str, Any], index: int = 0) -> Automation | None:
        if not isinstance(config, dict):
            _LOGGER.warning("Automation config must be a mapping, got %r", config)
            return None
        automation = Automation(self.jarvis, config, index)
        await self.platform.async_add_entities([automation.entity])
        self.automations[automation.entity.entity_id] = automation
        await automation.async_attach()
        _LOGGER.debug("Loaded automation %s (%s)", automation.alias, automation.entity_id)
        return automation

    async def async_setup_automations(self, configs: Any) -> list[Automation]:
        created = []
        for index, config in enumerate(as_list(configs)):
            automation = await self.async_add(config, index)
            if automation is not None:
                created.append(automation)
        return created

    async def async_wait(self) -> None:
        """Await every automation's live runs (shutdown + test helper)."""
        for automation in list(self.automations.values()):
            await automation.runner.async_wait()

    async def async_remove_all(self) -> None:
        for automation in list(self.automations.values()):
            await automation.async_remove()
        self.automations.clear()
        self.platform.entities.clear()

    async def async_reload(self, configs: Any) -> list[Automation]:
        await self.async_remove_all()
        return await self.async_setup_automations(configs)


__all__ = [
    "Automation",
    "AutomationEntity",
    "AutomationManager",
    "ModeController",
    "async_await_run",
]
