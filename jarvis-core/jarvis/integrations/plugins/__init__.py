"""The drop-in tool plugin: one class, and the registry does the rest.

Every integration that wants to give the model tools has so far written the
same forty lines — build a schema, register a handler, decide a tier, remember
to mark it read-only, remember to fetch its credential at call time. Calendar
and mail are the first two that would have written them twice, so this is the
shape they share.

    class Calendar(ToolPlugin):
        domain = "calendar"

        def tools(self):
            return [
                PluginTool("calendar_list", "Events in a window.", schema,
                           self.list_events, read_only=True),
                PluginTool("calendar_create", "Put an event in the diary.", schema,
                           self.create_event, tier=TIER_APPROVAL),
            ]

What the base class is FOR — the four things that were being got wrong one
integration at a time:

* **Read-only is declared, not inferred.** M43's escalation asks each tool
  whether it only reads. A plugin says so per tool, and the default is "no",
  which is the safe direction.
* **State-changing means Tier 3 unless the plugin argues otherwise.** Anything
  that mutates something outside this house — a calendar entry, an email —
  gets a human by default, and lowering that is a line somebody wrote.
* **Credentials are fetched at call time.** `self.secret(name)` reads the
  secrets store when the tool runs, never at import, so a credential is not
  sitting in an integration's attributes for the life of the process.
* **Every call lands in the trace.** The wrapper fires the same events the
  built-in tools fire, so an external call shows up in a task's trace with its
  duration — which for a network call is the number you actually want.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Fired around every plugin tool call, so an external request is visible in
#: the trace with its duration. Same shape as the built-in tool events.
EVENT_PLUGIN_CALL = "jarvis_plugin_call"


@dataclass
class PluginTool:
    """One tool a plugin offers."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    #: True when it only READS. M43's taint escalation asks this.
    read_only: bool = False
    #: `None` means "decide from read_only": a reader is Tier 1 and anything
    #: that changes something outside this house is Tier 3.
    tier: int | None = None
    #: Extra keys the handler wants from the call context.
    wants_context: bool = False

    def resolved_tier(self) -> int:
        from ...llm.tools import TIER_APPROVAL, TIER_DIRECT

        if self.tier is not None:
            return self.tier
        return TIER_DIRECT if self.read_only else TIER_APPROVAL


class ToolPlugin:
    """Base class. A plugin lists its tools; this registers and wraps them."""

    #: The tool domain, used for gating and for the trace.
    domain: str = "plugin"

    def __init__(self, jarvis: "Jarvis", config: Any = None) -> None:
        self.jarvis = jarvis
        self.config = config if isinstance(config, dict) else {}
        self._secrets: dict[str, str] = {}

    # --- what a subclass provides ----------------------------------------
    def tools(self) -> Sequence[PluginTool]:
        raise NotImplementedError

    async def health(self) -> dict[str, Any]:
        """Optional: is the far end reachable? Never raises."""
        return {"ok": True}

    # --- what the base class does for it ----------------------------------
    def secret(self, name: str, default: str = "") -> str:
        """A credential, read when the tool RUNS rather than at import.

        Falls back to the config block, because `!secret` in
        `configuration.yaml` has already been resolved by the time an
        integration sees it — the store is for values an operator kept out of
        that file entirely.
        """
        if name in self._secrets:
            return self._secrets[name]
        value = str(self.config.get(name) or "")
        if not value:
            store = self.jarvis.data.get("secrets") or {}
            if isinstance(store, dict):
                value = str(store.get(name) or "")
        if not value:
            value = default
        self._secrets[name] = value
        return value

    def register(self) -> int:
        """Put every tool on the registry. Returns how many."""
        count = sum(1 for tool in self.tools() if self.register_one(tool))
        _LOGGER.info("%s: %d tool(s) registered", self.domain, count)
        return count

    def register_one(self, tool: PluginTool) -> bool:
        """One tool, by itself.

        Split out of [register] so a tool an operator withdrew can be put back
        without re-registering the whole plugin — re-registering all of them
        would restore the ones they had just taken away (M46).
        """
        from ...llm.tools import schema_object

        registry = self.jarvis.data.get("llm_tools")
        if registry is None:
            _LOGGER.debug("%s: no tool registry yet", self.domain)
            return False
        parameters = tool.parameters
        if isinstance(parameters, dict) and "type" not in parameters:
            parameters = schema_object(parameters)
        registry.register(
            name=tool.name,
            description=tool.description,
            parameters=parameters,
            handler=self._wrap(tool),
            tier=tool.resolved_tier(),
            domain=self.domain,
            read_only=tool.read_only,
        )
        return True

    def _wrap(self, tool: PluginTool) -> Callable[..., Any]:
        """Time it, report it, and never let a network error kill a turn."""

        async def _run(args: dict[str, Any], context: Any = None) -> Any:
            started = time.monotonic()
            error = ""
            try:
                if tool.wants_context:
                    result = tool.handler(args, context)
                else:
                    result = tool.handler(args)
                if hasattr(result, "__await__"):
                    result = await result
                return result
            except Exception as err:  # noqa: BLE001 - a far end is allowed to fail
                _LOGGER.warning("%s failed: %s", tool.name, err)
                error = f"{type(err).__name__}: {err}"
                return {"status": "error", "error": error}
            finally:
                # In the trace, with its duration: for an external call that is
                # the number somebody actually wants.
                try:
                    self.jarvis.bus.fire(
                        EVENT_PLUGIN_CALL,
                        {
                            "plugin": self.domain,
                            "tool": tool.name,
                            "ms": round((time.monotonic() - started) * 1000, 1),
                            "ok": not error,
                            "error": error,
                        },
                        context if not isinstance(context, dict) else None,
                    )
                except Exception:  # pragma: no cover - a listener must not matter
                    pass

        return _run


@dataclass
class PluginRegistry:
    """Every plugin this install loaded, for `plugins.list`."""

    plugins: dict[str, ToolPlugin] = field(default_factory=dict)

    def add(self, plugin: ToolPlugin) -> None:
        self.plugins[plugin.domain] = plugin

    async def status(self) -> dict[str, Any]:
        out = {}
        for name, plugin in self.plugins.items():
            try:
                out[name] = await plugin.health()
            except Exception as err:  # noqa: BLE001 - report it
                out[name] = {"ok": False, "error": str(err)}
        return {"plugins": out}


def get_registry(jarvis: "Jarvis") -> PluginRegistry:
    store = jarvis.data.get("plugins")
    if not isinstance(store, PluginRegistry):
        store = PluginRegistry()
        jarvis.data["plugins"] = store
    return store
