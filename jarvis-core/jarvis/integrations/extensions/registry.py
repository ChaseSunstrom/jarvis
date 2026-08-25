"""One index over skills, MCP servers and tool plugins.

The registry OWNS nothing. Each subsystem keeps loading its own things exactly
as it did; this reads them, derives a [Manifest] for each, and answers the four
questions a management surface asks — what is installed, what is enabled, what
may it reach, and is it working. Building it the other way round (a registry
that loads everything and hands it out) would have meant rewriting three
working subsystems to be told what to do by a fourth.

What it does enforce: an extension whose manifest fails validation is not
indexed, and where the subsystem allows it, is taken out of service — a skill
is dropped from the store so the model never sees it in the prompt, and an MCP
server is disabled so nothing dials it. That is the whole of "rejected rather
than half-loaded" for the sources that exist today; a third party's manifest
arrives with the catalog in M47, and the gate for that is the same call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .manifest import (
    KIND_MCP,
    KIND_PLUGIN,
    KIND_SKILL,
    Manifest,
    ManifestError,
    Record,
    needs_act,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Frontmatter keys a skill may use to declare more than the Agent Skills
#: format carries. Under `metadata:`, which is free-form in that format and
#: ignored by every other reader — a skill written here still loads elsewhere.
SKILL_METADATA_KEYS = ("author", "source_url", "permissions", "network", "filesystem")


def _first_line(text: Any, limit: int = 600) -> str:
    lines = str(text or "").strip().splitlines()
    return lines[0][:limit] if lines else ""


def _clean_id(raw: Any) -> str:
    """A manifest id from a name that was never meant to be one."""
    text = str(raw or "").strip().lower()
    out = []
    for ch in text:
        out.append(ch if (ch.isalnum() or ch in "._-") else "-")
    cleaned = "".join(out).strip("-")
    return cleaned[:64] or "unnamed"


def skill_manifest(skill: Any) -> Manifest:
    """Derive a manifest from one `SKILL.md`.

    The declarations live under `metadata:` and every one of them is optional:
    a skill that declares nothing gets a manifest saying it declared nothing,
    which is honest and is what an operator should see. Inventing permissions
    from the tool list instead would have made every skill look deliberate.
    """
    meta = dict(getattr(skill, "metadata", {}) or {})
    network = meta.get("network") if isinstance(meta.get("network"), dict) else {}
    filesystem = meta.get("filesystem") if isinstance(meta.get("filesystem"), dict) else {}
    raw: dict[str, Any] = {
        "id": _clean_id(getattr(skill, "name", "")),
        "kind": KIND_SKILL,
        "version": str(getattr(skill, "version", "") or "0"),
        "description": str(getattr(skill, "description", "") or "(no description)")[:600],
        "tools": list(getattr(skill, "allowed_tools", ()) or ()),
    }
    if meta.get("author"):
        raw["author"] = str(meta["author"])[:120]
    if meta.get("source_url"):
        raw["source_url"] = str(meta["source_url"])
    if meta.get("permissions"):
        raw["permissions"] = list(meta["permissions"])
    if network:
        raw["network"] = network
    if filesystem:
        raw["filesystem"] = filesystem
    return Manifest.from_raw(raw, source=str(getattr(skill, "path", "") or raw["id"]))


def mcp_manifest(spec: Any) -> Manifest:
    """Derive a manifest from one configured MCP server.

    A stdio server is a process Jarvis starts, which is `run_process` and is
    stated as such rather than left for somebody to work out from `command:`.
    """
    is_stdio = bool(getattr(spec, "is_stdio", False))
    permissions = ["read_state"]
    if is_stdio:
        permissions.append("run_process")
    else:
        permissions.append("network")
    # An MCP server's tools are whatever it answers with, so its tier is what
    # actually bounds it — tier 3 means every call asks a human first.
    if int(getattr(spec, "tier", 2) or 2) >= 2:
        permissions.append("act")
    url = str(getattr(spec, "url", "") or "")
    host = ""
    if url:
        rest = url.split("://", 1)[-1]
        host = rest.split("/", 1)[0]
    raw: dict[str, Any] = {
        "id": _clean_id(getattr(spec, "name", "")),
        "kind": KIND_MCP,
        "version": "0",
        "description": (
            f"MCP server over {getattr(spec, 'transport', '?')}"
            + (f" at {host}" if host else "")
            + (f", starting {str(getattr(spec, 'command', ''))[:60]}" if is_stdio else "")
        )[:600],
        "permissions": sorted(set(permissions)),
        "network": {"needs": not is_stdio, "hosts": [host] if host else []},
    }
    if url.startswith(("http://", "https://")):
        raw["source_url"] = url[:500]
    return Manifest.from_raw(raw, source=f"mcp:{getattr(spec, 'name', '?')}")


def plugin_manifest(plugin: Any, read_only: frozenset[str]) -> Manifest:
    """Derive a manifest from a loaded `ToolPlugin`.

    Unlike a skill, a plugin's tools are Python and their read/write nature is
    known here rather than declared: `PluginTool.read_only` is the same field
    M43's taint escalation reads, so the manifest cannot disagree with the gate.
    """
    tools = []
    writes = False
    for tool in plugin.tools():
        tools.append(str(getattr(tool, "name", "")))
        if not getattr(tool, "read_only", False):
            writes = True
    permissions = ["read_state"]
    if writes:
        permissions.append("act")
    declared = getattr(plugin, "manifest_permissions", ()) or ()
    permissions.extend(str(p) for p in declared)
    raw: dict[str, Any] = {
        "id": _clean_id(getattr(plugin, "domain", "")),
        "kind": KIND_PLUGIN,
        "version": str(getattr(plugin, "version", "") or "0"),
        # `.splitlines()[0]` on a plugin with no docstring is an IndexError,
        # which is a registry that cannot list a house because one plugin
        # author did not write a sentence.
        "description": _first_line(
            getattr(plugin, "manifest_description", "") or plugin.__class__.__doc__
        )
        or f"{getattr(plugin, 'domain', 'plugin')} tools",
        "author": "Jarvis",
        "tools": sorted(t for t in tools if t),
        "permissions": sorted(set(permissions)),
    }
    if needs_act(tuple(raw["tools"]), read_only) and "act" not in raw["permissions"]:
        raw["permissions"] = sorted({*raw["permissions"], "act"})
    return Manifest.from_raw(raw, source=f"plugin:{getattr(plugin, 'domain', '?')}")


class ExtensionRegistry:
    """Every extensible thing this install has, in one shape."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.records: dict[str, Record] = {}
        #: Things that would not validate, kept so a surface can say WHY one an
        #: operator dropped in is missing. A silent skip is how a typo becomes
        #: an afternoon — the same reasoning as `SkillStore.errors`.
        self.errors: list[dict[str, str]] = []

    # --- building ---------------------------------------------------------
    def index(self) -> int:
        """Re-read all three subsystems. Returns how many are indexed."""
        self.records.clear()
        self.errors.clear()
        self._index_skills()
        self._index_mcp()
        self._index_plugins()
        return len(self.records)

    def _reject(self, kind: str, name: str, err: Exception, location: str = "") -> None:
        self.errors.append(
            {
                "kind": kind,
                "id": str(name),
                "location": location,
                "error": str(err),
            }
        )
        _LOGGER.warning("extensions: %s %r rejected: %s", kind, name, err)

    def _index_skills(self) -> None:
        store = self.jarvis.data.get("skills")
        if store is None:
            return
        bundled_root = str(getattr(store, "bundled_root", "") or "")
        for name, skill in list(getattr(store, "skills", {}).items()):
            try:
                manifest = skill_manifest(skill)
            except ManifestError as err:
                # Out of the store as well as out of the index: an invalid
                # manifest must not leave the skill in the system prompt, where
                # the model would go on using it.
                store.skills.pop(name, None)
                self._reject(KIND_SKILL, name, err, str(getattr(skill, "path", "")))
                continue
            path = str(getattr(skill, "path", ""))
            self.records[manifest.key] = Record(
                manifest=manifest,
                origin="bundled" if bundled_root and path.startswith(bundled_root) else "user",
                enabled=True,
                location=path,
            )
        for failure in list(getattr(store, "errors", []) or []):
            self.errors.append(
                {
                    "kind": KIND_SKILL,
                    "id": str(failure.get("path", "")),
                    "location": str(failure.get("path", "")),
                    "error": str(failure.get("error", "")),
                }
            )

    def _mcp_manager(self) -> Any:
        from ..mcp import get_manager

        return get_manager(self.jarvis)

    def _index_mcp(self) -> None:
        manager = self._mcp_manager()
        servers = getattr(manager, "servers", None)
        if not isinstance(servers, dict):
            return
        for name, spec in list(servers.items()):
            try:
                manifest = mcp_manifest(spec)
            except ManifestError as err:
                # Disabled rather than merely unlisted: an unvalidatable server
                # is one nothing should dial.
                try:
                    spec.enabled = False
                except AttributeError:
                    pass
                self._reject(KIND_MCP, name, err, str(getattr(spec, "url", "")))
                continue
            self.records[manifest.key] = Record(
                manifest=manifest,
                origin="user" if getattr(spec, "editable", False) else "bundled",
                enabled=bool(getattr(spec, "enabled", True)),
                location=str(getattr(spec, "url", "") or getattr(spec, "command", "")),
            )

    def _index_plugins(self) -> None:
        store = self.jarvis.data.get("plugins")
        plugins = getattr(store, "plugins", None)
        if not isinstance(plugins, dict):
            return
        from ...llm.tools import READ_ONLY_TOOLS

        for domain, plugin in list(plugins.items()):
            try:
                manifest = plugin_manifest(plugin, READ_ONLY_TOOLS)
            except Exception as err:  # noqa: BLE001 - a bad plugin is reported, not fatal
                self._reject(KIND_PLUGIN, domain, err)
                continue
            self.records[manifest.key] = Record(
                manifest=manifest,
                origin="bundled",
                enabled=True,
                location=plugin.__class__.__module__,
            )

    # --- reading ----------------------------------------------------------
    def listing(self, kind: str = "") -> list[dict[str, Any]]:
        rows = [
            record.as_dict()
            for record in self.records.values()
            if not kind or record.manifest.kind == kind
        ]
        return sorted(rows, key=lambda row: (row["kind"], row["id"]))

    def get(self, key: str) -> Record | None:
        return self.records.get(key)

    def permission_scope(self) -> dict[str, list[str]]:
        """Which extensions hold each permission — the audit view.

        The question worth being able to answer in one place is not "what can
        this skill do" but its inverse: everything in this house that can write
        to memory, or start a process, in one list.
        """
        scope: dict[str, list[str]] = {}
        for record in self.records.values():
            if not record.enabled:
                continue
            for permission in record.manifest.permissions:
                scope.setdefault(permission, []).append(record.manifest.key)
        return {name: sorted(keys) for name, keys in sorted(scope.items())}

    async def health(self) -> dict[str, dict[str, Any]]:
        """Ask each subsystem, and never raise.

        A registry that throws when one server is down is a registry that shows
        nothing when one server is down — which is the moment somebody opened
        it.
        """
        out: dict[str, dict[str, Any]] = {}
        plugins = getattr(self.jarvis.data.get("plugins"), "plugins", {}) or {}
        manager = self._mcp_manager()
        # `listing()` is what the MCP page already shows, so a row here and a
        # row there cannot disagree about whether a server is connected.
        mcp_rows = {}
        if manager is not None:
            try:
                mcp_rows = {_clean_id(row.get("name")): row for row in manager.listing()}
            except Exception as err:  # noqa: BLE001 - report it
                _LOGGER.warning("extensions: mcp listing failed: %s", err)
        for key, record in self.records.items():
            kind = record.manifest.kind
            if kind == KIND_SKILL:
                # A skill is text that was read at load. It is either there or
                # it is one of `self.errors`; there is nothing to ask.
                state: dict[str, Any] = {"ok": True, "detail": "loaded"}
            elif kind == KIND_PLUGIN:
                plugin = plugins.get(record.manifest.id)
                state = {"ok": False, "detail": "not loaded"}
                if plugin is not None:
                    try:
                        state = dict(await plugin.health())
                    except Exception as err:  # noqa: BLE001 - report it
                        state = {"ok": False, "detail": str(err)}
            else:
                row = mcp_rows.get(record.manifest.id)
                if row is None:
                    state = {"ok": False, "detail": "no longer configured"}
                elif not row.get("enabled", True):
                    state = {"ok": True, "detail": "disabled"}
                elif row.get("connected"):
                    state = {"ok": True, "detail": f"connected, {row.get('tool_count', 0)} tools"}
                else:
                    state = {"ok": False, "detail": row.get("error") or "not connected"}
            record.health = state
            out[key] = state
        return out


def get_registry(jarvis: "Jarvis") -> ExtensionRegistry:
    store = jarvis.data.get("extensions")
    if not isinstance(store, ExtensionRegistry):
        store = ExtensionRegistry(jarvis)
        jarvis.data["extensions"] = store
    return store
