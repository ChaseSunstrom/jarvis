"""`automation:` — YAML automations plus their services.

Config is the familiar list-of-automations::

    automation:
      - id: hallway_motion
        alias: Hallway motion light
        mode: restart
        trigger:
          - platform: state
            entity_id: binary_sensor.hall_motion
            to: "on"
        condition:
          - condition: numeric_state
            entity_id: sensor.hall_lux
            below: 20
        action:
          - service: light.turn_on
            target: {entity_id: light.hall}
          - delay: "00:02:00"
          - service: light.turn_off
            target: {entity_id: light.hall}

Services registered: ``automation.trigger``, ``turn_on``, ``turn_off``,
``toggle`` and ``reload``.

This integration is always set up (it is in ``CORE_INTEGRATIONS``), so it
also bootstraps ``input_helpers`` when the configuration contains any
``input_*:`` block — those keys are not integration names, so the loader
would otherwise skip them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ...automation.authored import get_authored
from ...automation.engine import AutomationManager, async_await_run
from ...automation.util import as_list, result_as_boolean
from ...services import ServiceCall

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "automation"
DATA_MANAGER = "automation"

SERVICE_TRIGGER = "trigger"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_TOGGLE = "toggle"
SERVICE_RELOAD = "reload"

INPUT_DOMAINS = (
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
)


def _configs_from(jarvis: "Jarvis", config: Any) -> list[Any]:
    """The `automation:` block plus any `automation <name>:` split keys."""
    configs = list(as_list(config))
    for key, value in (jarvis.config or {}).items():
        if key == DOMAIN or not isinstance(key, str):
            continue
        if key.split(" ")[0] == DOMAIN:
            configs.extend(as_list(value))
    return configs


async def _async_setup_input_helpers(jarvis: "Jarvis") -> None:
    """Set up `input_helpers` when any input_* key is configured."""
    config = jarvis.config or {}
    if not any(config.get(domain) for domain in INPUT_DOMAINS):
        return
    if jarvis.data.get("input_helpers") is not None:
        return
    try:
        from ..input_helpers import async_setup as async_setup_inputs
    except ImportError:  # pragma: no cover - integration removed
        return
    try:
        await async_setup_inputs(jarvis, config.get("input_helpers"))
    except Exception:  # pragma: no cover - defensive
        _LOGGER.exception("Error setting up input helpers")


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    manager: AutomationManager = jarvis.data.get(DATA_MANAGER)
    if not isinstance(manager, AutomationManager):
        manager = AutomationManager(jarvis)
        jarvis.data[DATA_MANAGER] = manager

    await _async_setup_input_helpers(jarvis)

    # Automations the console created live in .storage, not in automations.yaml
    # — rewriting that file would reformat it and lose the user's comments. The
    # engine takes a list of configs and cannot tell the two apart.
    authored = get_authored(jarvis)
    stored = await authored.async_load()
    await manager.async_setup_automations(_configs_from(jarvis, config) + stored)

    # --- services ---------------------------------------------------------
    async def _handle_trigger(call: ServiceCall) -> None:
        variables = call.get("variables") or {}
        # `bool("false")` is True — service data crossing YAML/JSON/voice
        # arrives as strings often enough that plain bool() is a trap.
        skip_condition = result_as_boolean(call.get("skip_condition", True))
        for automation in manager.resolve(call.get("entity_id")):
            await async_await_run(
                await automation.async_trigger(
                    dict(variables), call.context, skip_condition=skip_condition
                )
            )

    async def _handle_turn_on(call: ServiceCall) -> None:
        for automation in manager.resolve(call.get("entity_id")):
            automation.turn_on()

    async def _handle_turn_off(call: ServiceCall) -> None:
        stop_actions = result_as_boolean(call.get("stop_actions", True))
        for automation in manager.resolve(call.get("entity_id")):
            automation.turn_off(stop_actions)

    async def _handle_toggle(call: ServiceCall) -> None:
        for automation in manager.resolve(call.get("entity_id")):
            if automation.enabled:
                automation.turn_off(result_as_boolean(call.get("stop_actions", True)))
            else:
                automation.turn_on()

    async def _handle_reload(call: ServiceCall) -> None:
        try:
            from ...config import load_config_with_provenance

            # load_config walks the config dir (!include/!secret) — real,
            # blocking file I/O, so keep it off the event loop.
            fresh, provenance = await asyncio.to_thread(
                load_config_with_provenance, jarvis.config_dir
            )
            # Through async_install_config, not a bare assignment: this replaces
            # jarvis.config wholesale, and without re-applying the overlay every
            # setting the console has set is silently dropped the first time
            # anyone edits an automation — invisible until the next restart.
            fresh = await jarvis.async_install_config(fresh, provenance)
            stored = await authored.async_load()
        except Exception:
            _LOGGER.exception("Could not re-read configuration; keeping automations")
            return
        await manager.async_reload(_configs_from(jarvis, fresh.get(DOMAIN)) + stored)
        _LOGGER.info("Reloaded %d automations", len(manager.automations))

    jarvis.services.register(
        DOMAIN,
        SERVICE_TRIGGER,
        _handle_trigger,
        description="Run an automation's actions.",
        fields={
            "entity_id": {"description": "Automation(s) to run.", "required": True},
            "skip_condition": {
                "description": "Run even when the conditions are false (default true).",
            },
            "variables": {"description": "Extra variables for the action sequence."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_TURN_ON,
        _handle_turn_on,
        description="Enable an automation.",
        fields={"entity_id": {"description": "Automation(s) to enable."}},
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_TURN_OFF,
        _handle_turn_off,
        description="Disable an automation.",
        fields={
            "entity_id": {"description": "Automation(s) to disable."},
            "stop_actions": {"description": "Also cancel running actions (default true)."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_TOGGLE,
        _handle_toggle,
        description="Toggle an automation on or off.",
        fields={"entity_id": {"description": "Automation(s) to toggle."}},
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
        description="Re-read automations from the configuration directory.",
    )

    async def _handle_check(call: ServiceCall) -> Any:
        """Review an automation without running a step of it.

        Takes either a whole `config:` — so the console can check a draft
        before it is saved — or an `entity_id` naming one that already exists.
        The only way to test an automation used to be `automation.trigger`,
        which actuates the house; asking "is this right?" should not require
        finding out by unlocking a door.
        """
        from ...automation.check import check

        config = call.data.get("config")
        if config is None:
            targets = manager.resolve(call.data.get("entity_id"))
            if not targets:
                return {
                    "ok": False,
                    "findings": [
                        {
                            "level": "error",
                            "where": "config",
                            "message": "Pass a `config:` to check, or an "
                            "`entity_id:` naming an automation that exists.",
                        }
                    ],
                    "reach": "",
                }
            return {
                automation.entity_id: check(jarvis, automation.config)
                for automation in targets
            }
        return check(jarvis, config)

    jarvis.services.register(
        DOMAIN,
        "check",
        _handle_check,
        description=(
            "Review an automation for mistakes without running it: services "
            "that do not exist, entity ids that do not resolve, templates that "
            "will not compile, and what it would be allowed to touch."
        ),
        fields={
            "config": {"description": "A draft automation to review."},
            "entity_id": {"description": "Or an existing automation to review."},
        },
        supports_response=True,
    )

    async def _shutdown() -> None:
        await manager.async_remove_all()

    jarvis.register_shutdown(_shutdown)
    return True


__all__ = ["DOMAIN", "async_setup"]
