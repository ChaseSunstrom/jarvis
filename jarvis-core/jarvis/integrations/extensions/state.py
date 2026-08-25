"""What the operator has decided, and making the decision bite.

The registry (`registry.py`) reads what each subsystem loaded. This is the
other direction: an operator turns something off or takes a permission away,
and the next tool call has to see it.

The distinction that runs through this file is which extensions OWN tools and
which merely name them:

* a **plugin** and an **MCP server** own theirs. Disabling one removes its
  tools from the registry the model is offered, so "not offered to the model at
  all" is literally true and is what the tests assert;
* a **skill** is a document. Its `allowed-tools` list narrows what the model is
  TOLD it may use — nothing in this repository has ever enforced it, and this
  does not pretend to start. Disabling a skill is still real: it leaves the
  system prompt and `use_skill` refuses it, so the instructions never arrive.
  What stops a skill's suggestion from being obeyed is each tool's own tier,
  which is where it has always been.

Saying that plainly costs nothing. Implying a skill's allowlist is a sandbox
would cost somebody the assumption that it is.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ...store import Store
from .manifest import TOOL_PERMISSIONS

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from .registry import ExtensionRegistry

_LOGGER = logging.getLogger(__name__)

STORE_KEY = "extensions"


class ExtensionState:
    """Enable/disable and permission grants, persisted and applied."""

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.store = Store(jarvis.config_dir, STORE_KEY)
        #: `key -> {"enabled": bool, "permissions": [...] | None}`. A `None`
        #: permission list means "whatever the manifest declares" — an operator
        #: who has never opened the page has granted nothing and revoked
        #: nothing, and storing a copy of the manifest for them would freeze it
        #: at the version they never looked at.
        self.decisions: dict[str, dict[str, Any]] = {}
        #: `key -> epoch seconds`, last time one of its tools ran.
        self.last_used: dict[str, float] = {}

    async def async_load(self) -> None:
        data = await self.store.load() or {}
        raw = data.get("decisions")
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(value, dict):
                    continue
                permissions = value.get("permissions")
                self.decisions[str(key)] = {
                    "enabled": bool(value.get("enabled", True)),
                    "permissions": (
                        [str(p) for p in permissions] if isinstance(permissions, list) else None
                    ),
                }
        used = data.get("last_used")
        if isinstance(used, dict):
            for key, value in used.items():
                try:
                    self.last_used[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue

    async def async_save(self) -> None:
        await self.store.save(
            {"decisions": self.decisions, "last_used": {k: round(v, 3) for k, v in self.last_used.items()}}
        )

    # --- what the operator decided ---------------------------------------
    def is_enabled(self, key: str) -> bool:
        return bool(self.decisions.get(key, {}).get("enabled", True))

    def granted(self, key: str, declared: tuple[str, ...]) -> tuple[str, ...]:
        """The permissions this extension actually holds.

        The manifest's list, unless an operator has narrowed it. Narrowed
        only: a grant cannot add a permission the manifest never declared,
        because the manifest is the author's statement of what the thing needs
        and an operator granting more does not make it need more — it makes the
        two disagree, and the manifest is the one people read.
        """
        chosen = self.decisions.get(key, {}).get("permissions")
        if chosen is None:
            return tuple(declared)
        return tuple(p for p in declared if p in set(chosen))

    def mark_used(self, key: str, when: float | None = None) -> None:
        self.last_used[key] = time.time() if when is None else float(when)

    def as_dict(self, key: str) -> dict[str, Any]:
        return {
            "enabled": self.is_enabled(key),
            "granted": self.decisions.get(key, {}).get("permissions"),
            "last_used": self.last_used.get(key),
        }

    def set_enabled(self, key: str, enabled: bool) -> None:
        self.decisions.setdefault(key, {"enabled": True, "permissions": None})["enabled"] = bool(
            enabled
        )

    def set_permissions(self, key: str, permissions: list[str] | None) -> None:
        entry = self.decisions.setdefault(key, {"enabled": True, "permissions": None})
        entry["permissions"] = None if permissions is None else [str(p) for p in permissions]


def _apply_one_skill(jarvis: "Jarvis", record: Any, enabled: bool) -> None:
    """A skill owns no tools, so this is the whole of turning one off.

    Out of the store means out of the prompt's skill index and out of
    `use_skill`, which is where a skill's influence begins and ends.
    """
    store = jarvis.data.get("skills")
    if store is not None and not enabled:
        store.skills.pop(record.manifest.id, None)


def _apply_skills(jarvis: "Jarvis", registry: "ExtensionRegistry", state: ExtensionState) -> None:
    for key, record in registry.records.items():
        if record.manifest.kind == "skill":
            record.enabled = state.is_enabled(key)
            _apply_one_skill(jarvis, record, record.enabled)


def tools_needing(permission: str, tools: tuple[str, ...]) -> list[str]:
    """Which of these tools cannot run without that permission."""
    return [tool for tool in tools if TOOL_PERMISSIONS.get(tool) == permission]


def apply_decisions(jarvis: "Jarvis", registry: "ExtensionRegistry", state: ExtensionState) -> dict[str, Any]:
    """Make the operator's decisions true of the running system.

    Called after every change and at boot. Idempotent on purpose: it computes
    the tools that SHOULD be registered and registers or removes to match,
    rather than remembering what it did last time — a registry that has to
    remember is one that drifts the first time something else touches it.
    """
    removed: list[str] = []
    restored: list[str] = []

    # A skill that was turned off was POPPED from the store, and nothing else
    # ever puts it back — so turning one on again was a one-way door until the
    # next reload. The live suite found this: two turns proved the switch
    # worked and the third proved it only worked once.
    #
    # Re-reading the folder brings back every skill, including any whose
    # manifest does not validate, so the registry re-indexes straight after —
    # that pass is what drops those, and it must not be skipped here.
    store = jarvis.data.get("skills")
    if store is not None and hasattr(store, "load"):
        absent = [
            record
            for record in registry.records.values()
            if record.manifest.kind == "skill"
            and state.is_enabled(record.manifest.key)
            and record.manifest.id not in getattr(store, "skills", {})
        ]
        if absent:
            store.load()
            registry.index()
            _LOGGER.info("extensions: %d skill(s) came back", len(absent))

    tool_registry = jarvis.data.get("llm_tools")
    if tool_registry is None or not hasattr(tool_registry, "remove"):
        # Skills still have to be applied: they own no tools, so the tool
        # registry's absence is not a reason to leave a disabled one loaded.
        _apply_skills(jarvis, registry, state)
        return {"removed": removed, "restored": restored}

    plugins = getattr(jarvis.data.get("plugins"), "plugins", {}) or {}
    for key, record in registry.records.items():
        kind = record.manifest.kind
        enabled = state.is_enabled(key)
        record.enabled = enabled
        granted = set(state.granted(key, record.manifest.permissions))
        revoked = set(record.manifest.permissions) - granted
        record.granted = tuple(sorted(granted))

        if kind == "skill":
            _apply_one_skill(jarvis, record, enabled)
            continue

        if kind != "plugin":
            continue

        plugin = plugins.get(record.manifest.id)
        if plugin is None:
            continue
        for tool in plugin.tools():
            name = str(getattr(tool, "name", ""))
            if not name:
                continue
            needed = TOOL_PERMISSIONS.get(name)
            read_only = bool(getattr(tool, "read_only", False))
            allowed = enabled
            if allowed and needed and needed in revoked:
                allowed = False
            if allowed and not read_only and "act" in revoked:
                allowed = False
            present = tool_registry.get(name) is not None
            if allowed and not present:
                plugin.register_one(tool)
                restored.append(name)
            elif not allowed and present:
                tool_registry.remove(name)
                removed.append(name)

    if removed or restored:
        _LOGGER.info(
            "extensions: %d tool(s) withdrawn, %d restored", len(removed), len(restored)
        )
    return {"removed": sorted(removed), "restored": sorted(restored)}
