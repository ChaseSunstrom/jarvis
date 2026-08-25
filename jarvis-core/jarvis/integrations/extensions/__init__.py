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

from .manifest import KINDS, PERMISSIONS, Manifest, ManifestError, schema
from .registry import ExtensionRegistry, get_registry
from .scaffold import SKILL_TEMPLATE, ScaffoldError, scaffold_skill
from .state import ExtensionState, apply_decisions

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "extensions"

__all__ = [
    "DOMAIN",
    "ExtensionRegistry",
    "ExtensionState",
    "Manifest",
    "ManifestError",
    "ScaffoldError",
    "apply_decisions",
    "get_registry",
    "scaffold_skill",
    "schema",
]


def _register_services(
    jarvis: "Jarvis", registry: ExtensionRegistry, state: ExtensionState
) -> None:
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

    async def service_set(call: Any) -> Any:
        """Turn one off, or narrow what it holds. Applied before it returns.

        `enabled` and `permissions` in one call because the console's row has
        both and a half-applied change is the state nobody can reason about.
        """
        data = call.data or {}
        key = str(data.get("key") or "")
        record = registry.get(key)
        if record is None:
            return {"error": f"nothing installed called {key!r}", "keys": sorted(registry.records)}
        if "enabled" in data:
            state.set_enabled(key, bool(data.get("enabled")))
        if "permissions" in data:
            wanted = data.get("permissions")
            if wanted is not None and not isinstance(wanted, list):
                return {"error": "permissions must be a list, or null for 'as declared'"}
            if isinstance(wanted, list):
                unknown = sorted({str(p) for p in wanted} - set(PERMISSIONS))
                if unknown:
                    return {"error": f"no such permission: {', '.join(unknown)}"}
                # Narrowing only. Granting a permission the manifest never
                # declared would make the two disagree, and the manifest is the
                # one people read.
                wanted = [p for p in wanted if p in record.manifest.permissions]
            state.set_permissions(key, wanted)
        changed = apply_decisions(jarvis, registry, state)
        await state.async_save()
        return {"extension": record.as_dict(), **changed}

    async def service_scaffold(call: Any) -> Any:
        """Write a new SKILL.md from the template.

        A management surface people have to edit JSON behind is not one.
        """
        data = call.data or {}
        store = jarvis.data.get("skills")
        root = getattr(store, "root", None)
        if root is None:
            return {"error": "skills are not set up, so there is nowhere to put one"}
        try:
            path = scaffold_skill(
                root,
                name=str(data.get("name") or ""),
                description=str(data.get("description") or ""),
                tools=[str(t) for t in (data.get("tools") or [])],
                permissions=[str(p) for p in (data.get("permissions") or [])],
                body=str(data.get("body") or ""),
            )
        except ScaffoldError as err:
            return {"error": str(err)}
        store.load()
        registry.index()
        apply_decisions(jarvis, registry, state)
        return {"created": str(path), "skills": sorted(getattr(store, "skills", {}))}

    async def service_template(call: Any) -> Any:
        return {"template": SKILL_TEMPLATE, "permissions": list(PERMISSIONS)}

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
        apply_decisions(jarvis, registry, state)
        return {"indexed": indexed, "errors": list(registry.errors)}

    async def service_health(call: Any) -> Any:
        return {"health": await registry.health()}

    async def service_permissions(call: Any) -> Any:
        return {"scope": registry.permission_scope()}

    async def service_schema(call: Any) -> Any:
        return {"schema": schema()}

    jarvis.services.register(DOMAIN, "list", service_list, supports_response=True)
    jarvis.services.register(DOMAIN, "get", service_get, supports_response=True)
    jarvis.services.register(DOMAIN, "set", service_set, supports_response=True)
    jarvis.services.register(DOMAIN, "scaffold", service_scaffold, supports_response=True)
    jarvis.services.register(DOMAIN, "template", service_template, supports_response=True)
    jarvis.services.register(DOMAIN, "reload", service_reload, supports_response=True)
    jarvis.services.register(DOMAIN, "health", service_health, supports_response=True)
    jarvis.services.register(DOMAIN, "permissions", service_permissions, supports_response=True)
    jarvis.services.register(DOMAIN, "schema", service_schema, supports_response=True)


async def _index_and_report(
    jarvis: "Jarvis", registry: ExtensionRegistry, state: ExtensionState
) -> int:
    indexed = registry.index()
    for key, record in registry.records.items():
        record.last_used = state.last_used.get(key)
    apply_decisions(jarvis, registry, state)
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


def _track_use(jarvis: "Jarvis", registry: ExtensionRegistry, state: ExtensionState) -> None:
    """Remember when each extension last did something.

    From the events the subsystems already fire rather than by wrapping the
    tool registry: a second wrapper around every call is a second place for a
    tool to be slow, and this only needs a timestamp.
    """
    from ..plugins import EVENT_PLUGIN_CALL

    def _plugin_called(event: Any) -> None:
        domain = str((getattr(event, "data", None) or {}).get("plugin") or "")
        if not domain:
            return
        key = f"plugin:{domain}"
        state.mark_used(key)
        record = registry.get(key)
        if record is not None:
            record.last_used = state.last_used.get(key)

    jarvis.bus.listen(EVENT_PLUGIN_CALL, _plugin_called)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    from ...const import EVENT_JARVIS_START

    registry = get_registry(jarvis)
    state = ExtensionState(jarvis)
    await state.async_load()
    jarvis.data["extensions_state"] = state
    _register_services(jarvis, registry, state)
    _track_use(jarvis, registry, state)

    async def _on_start(event: Any = None) -> None:
        await _index_and_report(jarvis, registry, state)

    jarvis.bus.listen_once(EVENT_JARVIS_START, _on_start)
    # And once now, so a caller that never starts the bus — every test that
    # builds a Jarvis and asks it a question — still sees what is installed.
    await _index_and_report(jarvis, registry, state)
    return True
