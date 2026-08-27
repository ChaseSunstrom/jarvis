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

from ...const import EVENT_JARVIS_STOP
from .catalog import BUNDLED_SOURCE, Catalog, CatalogError, Source, bundled_source, resolve_ref
from .registries import RegistryError, fetch_remote, read_remote, reader_for
from .install import InstallError
from .manifest import KIND_MCP, KINDS, PERMISSIONS, Manifest, ManifestError, schema
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

    # --- the registries (M108) ---------------------------------------------
    # One http client for every remote source, made on first use and closed
    # with the house; and a five-minute memory of each (source, query) so a
    # console that re-renders the Tools page does not spend GitHub's sixty
    # unauthenticated requests an hour on the same listing.
    remote: dict[str, Any] = {"client": None, "cache": {}}
    REMOTE_TTL_S = 300.0

    def _client() -> Any:
        import httpx

        if remote["client"] is None:
            # No redirects: a registry that answered "see elsewhere" would be
            # a way off the allowlist, one hop at a time.
            remote["client"] = httpx.AsyncClient(follow_redirects=False)
        return remote["client"]

    async def _close_client(_event: Any = None) -> None:
        client = remote.pop("client", None)
        remote["client"] = None
        if client is not None:
            await client.aclose()

    jarvis.bus.listen_once(EVENT_JARVIS_STOP, _close_client)

    async def _read_remote(
        catalog: Catalog, query: str, kind: str, *, only: str = ""
    ) -> tuple[list[Any], list[dict[str, str]], int]:
        """Every remote source's entries, the failures per source, the skipped count."""
        import time

        rows: list[Any] = []
        errors: list[dict[str, str]] = []
        skipped = 0
        for source in catalog.sources.values():
            if not source.enabled or (kind and source.kind != kind):
                continue
            if only and source.name != only:
                continue
            try:
                if not reader_for(source):
                    continue
            except RegistryError as err:
                errors.append({"source": source.name, "error": str(err)})
                continue
            key = (source.name, str(query or "").strip().lower())
            hit = remote["cache"].get(key)
            if hit and time.monotonic() - hit[0] < REMOTE_TTL_S:
                rows.extend(hit[1])
                skipped += hit[2]
                continue
            try:
                entries, missed = await read_remote(_client(), source, query)
            except CatalogError as err:
                _LOGGER.warning("registry %s unreadable: %s", source.name, err)
                errors.append({"source": source.name, "error": str(err)})
                continue
            remote["cache"][key] = (time.monotonic(), entries, missed)
            rows.extend(entries)
            skipped += missed
        return rows, errors, skipped

    async def _find_entry(catalog: Catalog, source_name: str, entry_id: str, refs: list[str]) -> Any:
        """One entry, wherever its source is. Fetches the listing, never the thing."""
        from .install import prepare

        source = catalog.source_for(source_name)
        if not reader_for(source):
            return prepare(catalog, source_name, entry_id, refs)
        entries, errors, _ = await _read_remote(catalog, "", source.kind, only=source.name)
        for entry in entries:
            if entry.id == entry_id and entry.source == source.name:
                entry.ref = resolve_ref(entry, refs or [entry.ref])
                return entry
        if errors:
            raise CatalogError("; ".join(f"{e['source']}: {e['error']}" for e in errors))
        raise CatalogError(f"{source_name} does not offer {entry_id!r}")

    async def _fetch(entry: Any) -> dict[str, bytes]:
        from .install import fetch_local

        if entry.url.startswith("file://"):
            return fetch_local(entry)
        return await fetch_remote(_client(), entry)

    def _mcp_plan(entry: Any) -> dict[str, Any]:
        """What installing an MCP server would do: one `mcp:` entry, no files."""
        from ..mcp import get_manager

        manager = get_manager(jarvis)
        tier = manager.default_tier if manager is not None else 2
        return {
            "id": entry.id,
            "kind": entry.kind,
            "ref": entry.ref,
            "sha256": "",
            "files": [],
            "bytes": 0,
            "source": entry.source,
            "url": entry.url,
            "permissions": [],
            "hooks": [],
            "tier": tier,
            "note": (
                f"adds {entry.id} as an http MCP server at tier {tier}: every tool it "
                "offers is held for a person until approved, and nothing is downloaded"
            ),
        }

    async def service_sources(call: Any) -> Any:
        catalog = _catalog()
        return {
            "sources": [s.as_dict() for s in catalog.sources.values()],
            # Said out loud rather than left as a list: an operator should
            # learn that the one source they did not write is the package's
            # own folder, and that no remote origin is trusted by default.
            "note": (
                f"'{BUNDLED_SOURCE}' is this repository's own skills, read from "
                "the package on this machine. There is no default remote source: "
                "nothing installs from an origin nobody named."
            ),
        }

    async def service_browse(call: Any) -> Any:
        data = call.data or {}
        catalog = _catalog()
        live = sorted(name for name, source in catalog.sources.items() if source.enabled)
        if not live:
            return {
                "entries": [],
                "sources": [],
                "errors": [],
                "error": "no catalog source is configured",
            }
        query = str(data.get("query") or "")
        kind = str(data.get("kind") or "")
        entries, errors = catalog.read(query, kind)
        remote_entries, remote_errors, skipped = await _read_remote(catalog, query, kind)
        entries = sorted(entries + remote_entries, key=lambda e: (e.source, e.id))
        errors = errors + remote_errors
        rows = []
        for entry in entries:
            row = entry.as_dict()
            # Whether something of that kind and id is in the registry NOW.
            # The shipped skills are, on a fresh install, and a catalogue that
            # offered to install what is already in the prompt would be
            # offering a second copy in the operator's folder — which is a
            # real action (it overrides the shipped one) but not the one a
            # button called INSTALL means.
            row["installed"] = registry.get(f"{entry.kind}:{entry.id}") is not None
            rows.append(row)
        out: dict[str, Any] = {
            "entries": rows,
            "sources": live,
            "errors": errors,
            # Servers a registry lists that this house cannot install (a
            # package this machine would start, an `sse` remote, plain http):
            # said as a number so "the registry has more than this" is a
            # sentence the console can draw rather than a question.
            "skipped": skipped,
        }
        if not rows and errors:
            # Nothing to show AND a reason: the console draws the reason, not
            # "nothing matched", which is what an empty list would have said.
            out["error"] = "; ".join(f"{err['source']}: {err['error']}" for err in errors)
        return out

    async def service_plan(call: Any) -> Any:
        """What would happen, and what it would cost. Fetches; installs nothing."""
        from .install import plan as build_plan

        data = call.data or {}
        try:
            entry = await _find_entry(
                _catalog(),
                str(data.get("source") or ""),
                str(data.get("id") or ""),
                [str(r) for r in (data.get("refs") or [])],
            )
            if entry.kind == KIND_MCP:
                proposal = _mcp_plan(entry)
            else:
                files = await _fetch(entry)
                proposal = build_plan(entry, files, expected_sha=str(data.get("sha256") or ""))
        except (CatalogError, InstallError) as err:
            return {"error": str(err)}
        proposal["description"] = entry.description
        return {"plan": proposal}

    async def service_install(call: Any) -> Any:
        """Write what was approved. Refuses anything that was not."""
        from .install import apply as apply_install, install_mcp

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
            entry = await _find_entry(
                _catalog(), str(data.get("source") or ""), str(data.get("id") or ""), []
            )
            entry.ref = str(approved.get("ref") or entry.ref)
            if entry.kind == KIND_MCP:
                return await install_mcp(jarvis, entry, approved)
            files = await _fetch(entry)
            result = apply_install(jarvis, entry, files, approved)
        except (CatalogError, InstallError) as err:
            return {"error": str(err)}
        registry.index()
        apply_decisions(jarvis, registry, state)
        try:
            from ..notifications import note_capability

            await note_capability(jarvis, f"Installed: {data.get('id') or entry.ref}",
                                  f"from {data.get('source') or 'the catalogue'}")
        except Exception:  # noqa: BLE001 - installed either way
            _LOGGER.debug("Could not record the install", exc_info=True)
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
    # The package's own skills, unless the operator has named a source called
    # `bundled` themselves — their line wins, and `enabled: false` on it is
    # the off switch. Added by code rather than listed in configuration.yaml
    # so a fresh install has something to browse without anybody writing a
    # URL, and so M47's rule still holds as written: no shipped list of remote
    # origins. This source is this machine's own package, not somebody's
    # server (DEVIATIONS.md §21).
    if BUNDLED_SOURCE not in catalog.sources:
        try:
            catalog.add(bundled_source())
        except CatalogError as err:
            _LOGGER.warning("extensions: the bundled catalogue is unavailable: %s", err)
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
