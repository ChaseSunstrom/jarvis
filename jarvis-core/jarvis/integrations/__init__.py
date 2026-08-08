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
CORE_INTEGRATIONS = ("homeassistant_compat", "domains", "voice", "llm", "automation")

# Keys in configuration.yaml that are NOT integrations.
NON_INTEGRATION_KEYS = {"jarvis", "packages", "secrets"}


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
