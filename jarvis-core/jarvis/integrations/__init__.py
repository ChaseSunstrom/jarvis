"""Integration loader.

An integration is a package under `jarvis/integrations/<domain>/` exposing:

    DOMAIN = "mqtt"
    DEPENDENCIES: list[str] = []          # optional, set up first
    async def async_setup(jarvis, config) -> bool: ...

`config` is the YAML block for that domain (dict, list, or None). Setup
order respects DEPENDENCIES; a failing integration is logged and skipped
rather than taking the whole system down.

Adding an integration = dropping a package here (or a YAML-defined one via
the `yaml_tools`/`rest`/`template` integrations — no Python required).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

# Always set up (they provide the base service layer), even if absent from YAML.
#: Integrations that load whether or not configuration.yaml mentions them.
#:
#: `device_control` and `companion` are here for a reason worth writing down.
#: Both need no configuration at all — their whole job is to exist so that a
#: device which connects can be reached — and neither was in the shipped
#: configuration.yaml, because that file was deliberately emptied of anything
#: describing a house nobody owns yet.
#:
#: The result was a failure with no error anywhere. A phone paired, registered,
#: and appeared in the console's device list, because that list is read from the
#: websocket layer, which is always on. But `device_control.async_setup` never
#: ran, so `control_device` was never registered, so the model had no tool that
#: could reach the phone — and answered, correctly and uselessly, that its
#: capabilities were confined to the house. Nothing logged a warning: an
#: integration that is never asked for is not an error.
#:
#: `companion` is the same shape: without it `companion.notify` and
#: `companion.ask` do not exist, so Jarvis cannot reach the user on the device
#: they are actually at, and a question raised by `ask_user` never leaves the
#: console.
#:
#: A config block can still tune either (timeouts, taint TTL); it is no longer
#: what decides whether they exist.
CORE_INTEGRATIONS = (
    "homeassistant_compat",
    "domains",
    "voice",
    "llm",
    "automation",
    "device_control",
    "companion",
)

# Keys in configuration.yaml that are NOT integrations.
#: Top-level keys that configure something other than an integration. `metrics`
#: is the dashboards integration's data-source block (`metrics: sources:`), read
#: by `integrations/dashboards` — the loader warned "No integration named
#: 'metrics'" at every start for a key that was doing its job.
NON_INTEGRATION_KEYS = {"jarvis", "packages", "secrets", "metrics"}

#: Config keys that ARE features but are not integration names, because another
#: integration consumes them. Without this the loader warned
#:
#:     No integration named 'input_boolean' (config key ignored)
#:
#: about five keys in the shipped configuration.yaml — and the "ignored" was
#: simply untrue: `automation._async_setup_input_helpers` bootstraps
#: `input_helpers` whenever any of them is present, so the entities exist. Five
#: lines of false alarm at every start, each listing all 31 integrations, in the
#: first screen anyone reads when something has gone wrong.
KEYS_HANDLED_BY_ANOTHER_INTEGRATION = {
    "input_boolean",
    "input_number",
    "input_select",
    "input_text",
    "input_datetime",
}


def available_integrations() -> list[str]:
    root = Path(__file__).parent
    return sorted(
        m.name for m in pkgutil.iter_modules([str(root)]) if not m.name.startswith("_")
    )


def _load_module(name: str) -> Any | None:
    try:
        return importlib.import_module(f".{name}", __package__)
    except ModuleNotFoundError:
        return None
    except Exception:
        _LOGGER.exception("Failed importing integration %s", name)
        return None


def _resolve_order(requested: list[str]) -> list[str]:
    """Topological-ish ordering honouring DEPENDENCIES (cycles are broken)."""
    ordered: list[str] = []
    seen: set[str] = set()
    modules = {name: _load_module(name) for name in requested}

    def visit(name: str, stack: set[str]) -> None:
        if name in seen or name in stack:
            return
        stack.add(name)
        module = modules.get(name) or _load_module(name)
        if module is None:
            return
        modules[name] = module
        for dep in getattr(module, "DEPENDENCIES", []):
            if dep not in modules:
                modules[dep] = _load_module(dep)
            visit(dep, stack)
        stack.discard(name)
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    for name in requested:
        visit(name, set())
    return ordered


async def async_setup_integrations(jarvis: "Jarvis", config: dict[str, Any]) -> None:
    available = set(available_integrations())
    requested = [
        key.split(" ")[0]
        for key in config
        if key not in NON_INTEGRATION_KEYS
        and key not in KEYS_HANDLED_BY_ANOTHER_INTEGRATION
    ]
    wanted: list[str] = []
    for name in (*CORE_INTEGRATIONS, *requested):
        if name in wanted:
            continue
        if name not in available:
            if name in requested:
                _LOGGER.warning(
                    "No integration named %r (config key ignored). Available: %s",
                    name, ", ".join(sorted(available)),
                )
            continue
        wanted.append(name)

    for name in _resolve_order(wanted):
        module = _load_module(name)
        setup = getattr(module, "async_setup", None) if module else None
        if setup is None:
            continue
        try:
            result = await setup(jarvis, config.get(name))
            if result is False:
                _LOGGER.error("Integration %s failed to set up", name)
            else:
                _LOGGER.info("Set up integration: %s", name)
        except Exception:
            _LOGGER.exception("Error setting up integration %s", name)
