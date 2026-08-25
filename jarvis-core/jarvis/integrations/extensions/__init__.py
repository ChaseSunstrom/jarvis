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

from .catalog import Catalog, CatalogError, Source
from .install import InstallError
from .manifest import KINDS, PERMISSIONS, Manifest, ManifestError, schema
from .registry import ExtensionRegistry, get_registry
from .scaffold import SKILL_TEMPLATE, ScaffoldError, scaffold_skill
from .state import ExtensionState, apply_decisions

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "extensions"

__all__ = [
    "Catalog",
    "CatalogError",
    "DOMAIN",
    "InstallError",
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

    # --- the catalog (M47) -------------------------------------------------
    def _catalog() -> Catalog:
        store = jarvis.data.get("extension_catalog")
        return store if isinstance(store, Catalog) else Catalog()

    async def service_sources(call: Any) -> Any:
        catalog = _catalog()
        return {
            "sources": [s.as_dict() for s in catalog.sources.values()],
            # Said out loud rather than left as an empty list: an operator
            # looking at nothing should learn that nothing is the default.
            "note": (
                "There is no default source. Nothing installs from an origin "
                "nobody named, so a fresh install can reach nothing at all."
            ),
        }

    async def service_browse(call: Any) -> Any:
        data = call.data or {}
        catalog = _catalog()
        if not catalog.sources:
            return {"entries": [], "sources": [], "error": "no catalog source is configured"}
        entries = catalog.search(str(data.get("query") or ""), str(data.get("kind") or ""))
        return {
            "entries": [e.as_dict() for e in entries],
            "sources": sorted(catalog.sources),
        }

    async def service_plan(call: Any) -> Any:
        """What would happen, and what it would cost. Fetches; installs nothing."""
        from .install import fetch_local, plan as build_plan, prepare

        data = call.data or {}
        try:
            entry = prepare(
                _catalog(),
                str(data.get("source") or ""),
                str(data.get("id") or ""),
                [str(r) for r in (data.get("refs") or [])],
            )
            files = fetch_local(entry)
            proposal = build_plan(entry, files, expected_sha=str(data.get("sha256") or ""))
        except (CatalogError, InstallError) as err:
            return {"error": str(err)}
        proposal["description"] = entry.description
        return {"plan": proposal}

    async def service_install(call: Any) -> Any:
        """Write what was approved. Refuses anything that was not."""
        from .install import apply as apply_install, fetch_local, prepare

        data = call.data or {}
        approved = data.get("approved")
        if not isinstance(approved, dict):
            return {
                "error": (
                    "install takes the plan a human approved. Call extensions.plan, "
                    "show its permissions and hooks to a person, and pass it back."
                )
            }
        try:
            entry = prepare(_catalog(), str(data.get("source") or ""), str(data.get("id") or ""))
            entry.ref = str(approved.get("ref") or entry.ref)
            files = fetch_local(entry)
            result = apply_install(jarvis, entry, files, approved)
        except (CatalogError, InstallError) as err:
            return {"error": str(err)}
        registry.index()
        apply_decisions(jarvis, registry, state)
        return result

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
    jarvis.services.register(DOMAIN, "sources", service_sources, supports_response=True)
    jarvis.services.register(DOMAIN, "browse", service_browse, supports_response=True)
    jarvis.services.register(DOMAIN, "plan", service_plan, supports_response=True)
    jarvis.services.register(DOMAIN, "install", service_install, supports_response=True)


def _build_catalog(raw: Any) -> Catalog:
    """The operator's allowlist of origins, and nothing else.

    A source that will not build is DROPPED with a warning rather than taken
    as far as it goes: half a source is an origin somebody half-allowed.
    """
    catalog = Catalog()
    for entry in (raw or {}).get("sources") or [] if isinstance(raw, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            catalog.add(
                Source(
                    name=str(entry.get("name") or ""),
                    url=str(entry.get("url") or ""),
                    kind=str(entry.get("kind") or "skill"),
                    enabled=bool(entry.get("enabled", True)),
                )
            )
        except CatalogError as err:
            _LOGGER.warning("extensions: catalog source ignored: %s", err)
    if catalog.sources:
        _LOGGER.info(
            "extensions: %d catalog source(s): %s",
            len(catalog.sources),
            ", ".join(sorted(catalog.sources)),
        )
    return catalog


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

    cfg = config if isinstance(config, dict) else {}
    jarvis.data["extension_catalog"] = _build_catalog(cfg.get("catalog"))

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
