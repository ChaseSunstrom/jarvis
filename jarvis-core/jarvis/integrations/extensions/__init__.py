"""The extension registry: one answer to "what is installed, and what may it do".

Skills, MCP servers and tool plugins each arrive by their own road and each
kept their own vocabulary. This integration reads all three and publishes one
index — see `registry.py` for why it reads rather than owns, and `manifest.py`
for the shape and the schema.

Indexing waits for `EVENT_JARVIS_START` rather than declaring the three as
`DEPENDENCIES`. A dependency would have FORCED them to set up — an install with
no `mcp:` block would have grown an MCP integration, services and all, because
something wanted to list it. Waiting for the start event gets the ordering with
none of that: whatever loaded is what gets indexed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .manifest import KINDS, Manifest, ManifestError, schema
from .registry import ExtensionRegistry, get_registry

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "extensions"

__all__ = [
    "DOMAIN",
    "ExtensionRegistry",
    "Manifest",
    "ManifestError",
    "get_registry",
    "schema",
]


def _register_services(jarvis: "Jarvis", registry: ExtensionRegistry) -> None:
    async def service_list(call: Any) -> Any:
        kind = str((call.data or {}).get("kind") or "")
        if kind and kind not in KINDS:
            return {"error": f"no such kind {kind!r}", "kinds": list(KINDS)}
        return {
            "extensions": registry.listing(kind),
            "errors": list(registry.errors),
            "counts": {
                k: len([r for r in registry.records.values() if r.manifest.kind == k])
                for k in KINDS
            },
        }

    async def service_get(call: Any) -> Any:
        key = str((call.data or {}).get("key") or "")
        record = registry.get(key)
        if record is None:
            return {"error": f"nothing installed called {key!r}", "keys": sorted(registry.records)}
        return {"extension": record.as_dict()}

    async def service_reload(call: Any) -> Any:
        # The skill store is re-read first: the registry indexes what is
        # loaded, so reloading it alone would re-index the same stale copy and
        # report success.
        store = jarvis.data.get("skills")
        if store is not None and hasattr(store, "load"):
            store.load()
        indexed = registry.index()
        return {"indexed": indexed, "errors": list(registry.errors)}

    async def service_health(call: Any) -> Any:
        return {"health": await registry.health()}

    async def service_permissions(call: Any) -> Any:
        return {"scope": registry.permission_scope()}

    async def service_schema(call: Any) -> Any:
        return {"schema": schema()}

    jarvis.services.register(DOMAIN, "list", service_list, supports_response=True)
    jarvis.services.register(DOMAIN, "get", service_get, supports_response=True)
    jarvis.services.register(DOMAIN, "reload", service_reload, supports_response=True)
    jarvis.services.register(DOMAIN, "health", service_health, supports_response=True)
    jarvis.services.register(DOMAIN, "permissions", service_permissions, supports_response=True)
    jarvis.services.register(DOMAIN, "schema", service_schema, supports_response=True)


async def _index_and_report(jarvis: "Jarvis", registry: ExtensionRegistry) -> int:
    indexed = registry.index()
    if registry.errors:
        # WARNING, not debug: something an operator installed is not running,
        # and the reason is the first thing they will look for.
        for problem in registry.errors:
            _LOGGER.warning(
                "extensions: %s %s not loaded: %s",
                problem.get("kind", "?"),
                problem.get("id", "?"),
                problem.get("error", ""),
            )
    _LOGGER.info(
        "extensions ready: %d indexed (%s)%s",
        indexed,
        ", ".join(
            f"{len([r for r in registry.records.values() if r.manifest.kind == k])} {k}"
            for k in KINDS
        ),
        f", {len(registry.errors)} rejected" if registry.errors else "",
    )
    return indexed


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    from ...const import EVENT_JARVIS_START

    registry = get_registry(jarvis)
    _register_services(jarvis, registry)

    async def _on_start(event: Any = None) -> None:
        await _index_and_report(jarvis, registry)

    jarvis.bus.listen_once(EVENT_JARVIS_START, _on_start)
    # And once now, so a caller that never starts the bus — every test that
    # builds a Jarvis and asks it a question — still sees what is installed.
    await _index_and_report(jarvis, registry)
    return True
