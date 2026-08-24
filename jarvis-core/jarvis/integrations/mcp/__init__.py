"""`mcp` integration — any MCP server's tools, inside Jarvis.

    mcp:
      allow_stdio: false          # see "The stdio line" below
      default_tier: 2
      servers:
        - name: nextcloud
          url: http://127.0.0.1:9100/mcp
          token: !env_var NEXTCLOUD_MCP_TOKEN ""
          tier: 2

Servers may also be added from the console at runtime, which is the point:
*"allow Jarvis to use any MCP server? with allowing the download of any MCP
server/tool in the settings, so I can easily add new ones"*. Those live in
`<config>/.storage/mcp_servers.json`, are marked `editable`, and are the only
ones the API may change — a server written in `configuration.yaml` is the
operator's, and a web request does not get to rewrite a file they hand-edited.

## What arrives, and what it is

Everything a server says is a **claim**: its tool names, its descriptions and
its results are written by a party that is not the user. So three rules, all
enforced in `catalog.py` and all tested:

* tool names are namespaced `mcp_<server>_<tool>`, so a server offering
  `control_device` cannot shadow the real one;
* descriptions are flattened and prefixed with their provenance, because the
  description field is quoted verbatim into the system prompt and is therefore
  the cheapest prompt-injection surface in the protocol;
* **results are fenced** and mark the turn untrusted, exactly as `web` does. A
  page and an MCP tool result are the same kind of thing: somebody else's text.

## The tier

Chosen here, never by the server. The default is **Tier 2**, because an MCP tool
is third-party code with side effects nobody in this process can see, and Tier 1
means "run it and answer". An operator who trusts a particular server may lower
it per server in their own config; a server cannot ask.

## The stdio line

An `http` server is a URL Jarvis talks to. A `stdio` server is **a program
Jarvis starts** — `npx -y some-package`, and now arbitrary code is running as
the jarvis-core user with its network, its filesystem and its token.

That is a real capability and it is what people actually want from MCP, so it is
supported. It is off by default, and turning it on is `allow_stdio: true` in
`configuration.yaml` — a file on disk, edited by a person with shell access.
**No API call can set it**, which means no compromised browser session, no
model tool call, and no cross-site request can turn a Jarvis that reads URLs
into a Jarvis that runs commands. With it on, the console may add stdio servers
freely, because at that point the operator has said so in the one place a
request cannot reach.

There is no `mcp_add_server` tool. The model may use servers; it may not
install them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from ...api.devices import mark_untrusted_result
from ...services import ServiceCall
from ...store import Store
from ..web.fence import fence, sanitize_untrusted
from .catalog import (
    MAX_TOOLS_PER_SERVER,
    MCPTool,
    ServerSpec,
    describe_tool,
    namespaced,
    safe_server_name,
    sanitise_schema,
    server_from_dict,
)
from .client import HttpTransport, MCPClient, MCPError, StdioTransport

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "mcp"
#: `llm` owns the tool registry these land in. Without it there is nothing to
#: register into and this integration has no purpose.
DEPENDENCIES = ["llm"]

STORE_KEY = "mcp_servers"
DATA_MANAGER = "manager"

DEFAULT_TIER = 2
CONNECT_TIMEOUT = 20.0
#: The reconnect watcher. `TICK` is how often it looks; `BASE` and `CEILING`
#: bound the per-server backoff (30 s doubling to 30 minutes).
RECONNECT_TICK = 10.0
RECONNECT_BASE = 30.0
RECONNECT_CEILING = 1800.0


class MCPManager:
    """Every configured server, its live client, and the tools it lent us."""

    def __init__(
        self,
        jarvis: "Jarvis",
        *,
        store: Store | None = None,
        allow_stdio: bool = False,
        default_tier: int = DEFAULT_TIER,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.jarvis = jarvis
        self.store = store
        self.allow_stdio = bool(allow_stdio)
        self.default_tier = min(3, max(1, int(default_tier or DEFAULT_TIER)))
        self.http_client = http_client
        #: name -> spec, config-authored first then store-authored.
        self.servers: dict[str, ServerSpec] = {}
        self.clients: dict[str, MCPClient] = {}
        self.tools: dict[str, list[MCPTool]] = {}
        #: name -> why it is not working, for the console to show.
        self.errors: dict[str, str] = {}
        #: How many times in a row a server has failed to connect, and when it
        #: may be tried again. Both keyed by name; both cleared on success.
        self.attempts: dict[str, int] = {}
        self.next_attempt: dict[str, float] = {}

    # --- configuration ----------------------------------------------------
    def add_from_config(self, raw: Any) -> None:
        for entry in raw or []:
            spec = server_from_dict(entry, editable=False)
            if spec is None:
                _LOGGER.warning("mcp: skipping a server entry with no usable name")
                continue
            spec.tier = spec.tier or self.default_tier
            self.servers[spec.name] = spec

    async def async_load(self) -> None:
        """Read the console-added servers.

        Config wins on a name collision: the file is the operator's statement
        and the store is a convenience, and silently letting a web request
        shadow a hand-edited server would be the wrong way round.
        """
        if self.store is None:
            return
        data = await self.store.load()
        for entry in (data or {}).get("servers") or []:
            spec = server_from_dict(entry, editable=True)
            if spec is None:
                continue
            if spec.name in self.servers:
                _LOGGER.warning(
                    "mcp: ignoring the stored server %r — configuration.yaml already "
                    "defines one by that name",
                    spec.name,
                )
                continue
            self.servers[spec.name] = spec

    async def async_save(self) -> None:
        if self.store is None:
            return
        editable = [s.as_dict(redact=False) for s in self.servers.values() if s.editable]
        try:
            await self.store.save({"servers": editable})
        except Exception:  # pragma: no cover - a full disk is not a tool failure
            _LOGGER.exception("mcp: could not save the server list")

    # --- connecting -------------------------------------------------------
    def _transport(self, spec: ServerSpec) -> Any:
        if spec.is_stdio:
            if not self.allow_stdio:
                raise MCPError(
                    "this server would start a program, and `mcp: allow_stdio:` "
                    "is not set in configuration.yaml"
                )
            if not spec.command:
                raise MCPError("a stdio server needs a command")
            return StdioTransport(spec.command, spec.args, spec.env, timeout=CONNECT_TIMEOUT)
        if not spec.url:
            raise MCPError("an http server needs a url")
        return HttpTransport(
            spec.url, spec.token, client=self.http_client, timeout=CONNECT_TIMEOUT
        )

    async def async_connect(self, spec: ServerSpec) -> bool:
        """Bring one server up and register its tools. Never raises."""
        await self.async_disconnect(spec.name)
        if not spec.enabled:
            return False
        try:
            client = MCPClient(self._transport(spec), name=spec.name)
            await asyncio.wait_for(client.async_initialize(), CONNECT_TIMEOUT)
            listed = await asyncio.wait_for(client.async_list_tools(), CONNECT_TIMEOUT)
        except (MCPError, asyncio.TimeoutError, TimeoutError) as err:
            self.errors[spec.name] = str(err)[:300] or "could not connect"
            _LOGGER.warning("mcp: %s is not available: %s", spec.name, err)
            return False
        except Exception as err:  # noqa: BLE001 - a bad server is not a crash
            self.errors[spec.name] = f"{type(err).__name__}: {err}"[:300]
            _LOGGER.exception("mcp: %s failed unexpectedly", spec.name)
            return False

        self.clients[spec.name] = client
        self.errors.pop(spec.name, None)
        self.tools[spec.name] = self._register(spec, listed)
        _LOGGER.info(
            "mcp: %s ready with %d tool(s) at tier %d",
            spec.name,
            len(self.tools[spec.name]),
            spec.tier,
        )
        return True

    async def async_disconnect(self, name: str) -> None:
        for tool in self.tools.pop(name, []):
            registry = self._registry()
            remove = getattr(registry, "remove", None) if registry else None
            if remove is not None:
                try:
                    remove(tool.name)
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("mcp: could not unregister %s", tool.name)
        client = self.clients.pop(name, None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("mcp: %s did not close cleanly", name)

    async def async_connect_all(self) -> None:
        # Concurrently: a server that is down costs CONNECT_TIMEOUT, and doing
        # six of those in sequence would be two minutes of startup for a
        # feature that is meant to be optional.
        await asyncio.gather(
            *(self.async_connect(spec) for spec in list(self.servers.values())),
            return_exceptions=True,
        )

    async def async_shutdown(self) -> None:
        for name in list(self.clients):
            await self.async_disconnect(name)

    # --- registering ------------------------------------------------------
    def _registry(self) -> Any:
        return self.jarvis.data.get("llm_tools")

    def _register(self, spec: ServerSpec, listed: list[dict[str, Any]]) -> list[MCPTool]:
        registry = self._registry()
        if registry is None:
            _LOGGER.debug("mcp: no LLM tool registry; %s is connected but idle", spec.name)
            return []

        out: list[MCPTool] = []
        for raw in listed[:MAX_TOOLS_PER_SERVER]:
            remote = str(raw.get("name") or "")
            name = namespaced(spec.name, remote)
            if not name:
                _LOGGER.warning("mcp: %s offered a tool with an unusable name", spec.name)
                continue
            tool = MCPTool(
                server=spec.name,
                remote_name=remote,
                name=name,
                description=describe_tool(
                    spec.name, raw.get("description"), url=spec.url
                ),
                parameters=sanitise_schema(raw.get("inputSchema")),
                tier=spec.tier,
            )
            try:
                registry.register(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=self._handler(tool),
                    tier=tool.tier,
                    domain=DOMAIN,
                )
            except Exception as err:  # noqa: BLE001
                # The registry refuses a registration that would weaken an
                # existing tool. Namespacing should make that impossible, so
                # reaching here means two servers share a name — which is worth
                # a line in the log and not worth failing setup over.
                _LOGGER.warning("mcp: could not register %s: %s", tool.name, err)
                continue
            out.append(tool)
        return out

    def _handler(self, tool: MCPTool) -> Any:
        async def call(args: dict[str, Any], context: Any = None) -> Any:
            client = self.clients.get(tool.server)
            if client is None:
                return {
                    "status": "error",
                    "error": f"the MCP server '{tool.server}' is not connected",
                }
            try:
                result = await client.async_call_tool(tool.remote_name, args or {})
            except MCPError as err:
                return {"status": "error", "error": str(err)[:300]}
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("mcp: %s raised", tool.name)
                return {"status": "error", "error": f"{type(err).__name__}: {err}"[:300]}

            # Fenced, and the turn marked — the same treatment `web` gives a
            # page, for the same reason. This text was written by a third party
            # and must not be able to choose an action.
            payload = {
                "status": "ok" if result["ok"] else "error",
                "server": tool.server,
                "tool": tool.remote_name,
                "content_is_untrusted": True,
                "text": fence(
                    sanitize_untrusted(result["text"]) or "(the tool returned nothing)",
                    source=f"mcp:{tool.server}/{tool.remote_name}",
                ),
                "truncated": result["truncated"],
            }
            if not result["ok"]:
                payload["error"] = "the tool reported a failure; its words are in `text`"
            return mark_untrusted_result(self.jarvis, context, payload)

        return call

    # --- staying connected ------------------------------------------------
    #
    # A server that was down when Jarvis booted stayed down until somebody
    # pressed reconnect. That is the wrong default for a thing whose whole job
    # is to be reachable: an MCP server in the same compose file starts a few
    # seconds later than jarvis-core roughly every time, and the result was a
    # house whose extra tools existed only after a human noticed.
    #
    # Backoff, not a retry loop: a server that is *gone* must not be dialled
    # every ten seconds for a week. Doubling from 30 s to a 30-minute ceiling
    # means a slow starter is picked up in under a minute and a decommissioned
    # one costs two requests an hour.
    def backoff(self, attempts: int) -> float:
        """Seconds before the next attempt at a server that is not answering."""
        delay = RECONNECT_BASE * (2 ** max(0, attempts - 1))
        return float(min(delay, RECONNECT_CEILING))

    async def async_watch(self) -> None:
        """Reconnect anything that is down, for as long as the server runs."""
        while True:
            try:
                await asyncio.sleep(RECONNECT_TICK)
                await self._retry_the_dead()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - the watcher must not die
                _LOGGER.exception("mcp: the reconnect watcher raised")

    async def _retry_the_dead(self) -> None:
        now = time.monotonic()
        for spec in list(self.servers.values()):
            if not spec.enabled or spec.name in self.clients:
                self.attempts.pop(spec.name, None)
                continue
            attempts = self.attempts.get(spec.name, 0)
            due = self.next_attempt.get(spec.name, 0.0)
            if now < due:
                continue
            self.attempts[spec.name] = attempts + 1
            ok = await self.async_connect(spec)
            if ok:
                self.attempts.pop(spec.name, None)
                self.next_attempt.pop(spec.name, None)
                _LOGGER.info("mcp: %s came back after %d attempt(s)", spec.name, attempts + 1)
            else:
                wait = self.backoff(attempts + 1)
                self.next_attempt[spec.name] = now + wait
                _LOGGER.debug("mcp: %s still down; next attempt in %.0fs", spec.name, wait)

    # --- what the console sees --------------------------------------------
    def inspect(self, name: str) -> dict[str, Any]:
        """Everything knowable about one server, including why it is not up.

        The listing is deliberately thin — it is drawn for every server on one
        page — so the schemas, the protocol version and the last error live
        here, behind a click. `last_error` is the field that matters: a server
        that is simply absent from the tool list tells nobody why.
        """
        spec = self.servers.get(str(name or ""))
        if spec is None:
            raise KeyError(name)
        client = self.clients.get(spec.name)
        tools = self.tools.get(spec.name, [])
        return {
            "name": spec.name,
            "transport": "stdio" if spec.is_stdio else "http",
            "url": spec.url,
            "command": spec.command,
            "enabled": spec.enabled,
            "editable": spec.editable,
            "tier": spec.tier,
            "connected": client is not None,
            "server_info": client.server_info if client else {},
            "protocol_version": getattr(client, "protocol_version", "") if client else "",
            "last_error": self.errors.get(spec.name, ""),
            "attempts": self.attempts.get(spec.name, 0),
            "next_attempt_in": max(
                0.0, round(self.next_attempt.get(spec.name, 0.0) - time.monotonic(), 1)
            ),
            "tools": [
                {
                    "name": tool.name,
                    "remote_name": tool.remote_name,
                    "description": tool.description,
                    # The whole schema: this is the page somebody reads when a
                    # tool call keeps failing, and "arguments" is the answer
                    # about nine times in ten.
                    "parameters": tool.parameters,
                    "tier": tool.tier,
                }
                for tool in tools
            ],
        }

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for spec in self.servers.values():
            row = spec.as_dict()
            row["connected"] = spec.name in self.clients
            row["error"] = self.errors.get(spec.name, "")
            row["tools"] = [
                {"name": t.name, "remote_name": t.remote_name, "description": t.description}
                for t in self.tools.get(spec.name, [])
            ]
            row["tool_count"] = len(row["tools"])
            info = self.clients.get(spec.name)
            row["server_info"] = info.server_info if info else {}
            out.append(row)
        return out


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def get_manager(jarvis: "Jarvis") -> MCPManager | None:
    store = jarvis.data.get(DOMAIN)
    return store.get(DATA_MANAGER) if isinstance(store, dict) else None


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    store = jarvis.data.setdefault(DOMAIN, {})

    manager = MCPManager(
        jarvis,
        store=Store(jarvis.config_dir, STORE_KEY),
        # Read ONLY from the file. The API deliberately has no way to set it —
        # see the module docstring: this is the line between "Jarvis reads a
        # URL" and "Jarvis runs a program".
        allow_stdio=bool(cfg.get("allow_stdio")),
        default_tier=int(cfg.get("default_tier") or DEFAULT_TIER),
        http_client=store.get("client"),
    )
    manager.add_from_config(cfg.get("servers"))
    await manager.async_load()
    store[DATA_MANAGER] = manager

    # Not awaited: a server that is down should not hold up a boot, and every
    # tool it would have registered simply is not there until it answers.
    jarvis.async_create_task(manager.async_connect_all())
    # …and keeps trying, with backoff. Without this, a server that lost its
    # network for a minute was gone until somebody pressed a button.
    jarvis.async_create_task(manager.async_watch())

    _register_services(jarvis, manager)
    jarvis.register_shutdown(manager.async_shutdown)

    if manager.allow_stdio:
        _LOGGER.warning(
            "mcp: allow_stdio is ON — a configured stdio server starts a program "
            "as this user. Only add servers you would run by hand."
        )
    _LOGGER.info(
        "mcp ready: %d server(s) configured, stdio %s",
        len(manager.servers),
        "allowed" if manager.allow_stdio else "refused",
    )
    return True


def _register_services(jarvis: "Jarvis", manager: MCPManager) -> None:
    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        return {"servers": manager.listing(), "allow_stdio": manager.allow_stdio}

    async def handle_add(call: ServiceCall) -> dict[str, Any]:
        return await async_add_server(manager, dict(call.data))

    async def handle_remove(call: ServiceCall) -> dict[str, Any]:
        return await async_remove_server(manager, str(call.get("name") or ""))

    async def handle_reconnect(call: ServiceCall) -> dict[str, Any]:
        name = safe_server_name(call.get("name"))
        if not name:
            await manager.async_connect_all()
            return {"reconnected": "all", "servers": manager.listing()}
        spec = manager.servers.get(name)
        if spec is None:
            return {"status": "error", "error": f"no MCP server called {name!r}"}
        ok = await manager.async_connect(spec)
        return {"reconnected": name, "connected": ok, "servers": manager.listing()}

    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Every configured MCP server, its tools and whether it is up.",
    )
    jarvis.services.register(
        DOMAIN, "add", handle_add, supports_response=True,
        description="Add an MCP server and connect to it.",
        fields={
            "name": {"description": "A short name; becomes part of each tool's name.", "required": True},
            "url": {"description": "For an http server: its endpoint."},
            "token": {"description": "Optional bearer token for that endpoint."},
            "transport": {"description": "'http' (default) or 'stdio'."},
            "command": {"description": "For a stdio server: the program to run."},
            "args": {"description": "Its arguments."},
            "tier": {"description": "1 direct, 2 confirm, 3 approval. Defaults to 2."},
        },
    )
    jarvis.services.register(
        DOMAIN, "remove", handle_remove, supports_response=True,
        description="Forget a console-added MCP server and unregister its tools.",
        fields={"name": {"description": "The server's name.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN, "reconnect", handle_reconnect, supports_response=True,
        description="Reconnect one server, or all of them, and refresh their tools.",
        fields={"name": {"description": "One server, or omit for all."}},
    )


# --- the operations the API and the services share -----------------------------

async def async_add_server(manager: MCPManager, data: dict[str, Any]) -> dict[str, Any]:
    """Add (or replace) a console-authored server, and connect to it."""
    spec = server_from_dict(data, editable=True)
    if spec is None:
        return {"status": "error", "error": "an MCP server needs a name"}

    existing = manager.servers.get(spec.name)
    if existing is not None and not existing.editable:
        # The file is the authority for its own servers. Letting a request
        # rewrite one would make `configuration.yaml` a suggestion.
        return {
            "status": "error",
            "error": f"{spec.name!r} is defined in configuration.yaml; edit it there",
        }
    if spec.is_stdio and not manager.allow_stdio:
        return {
            "status": "error",
            "error": (
                "a stdio server starts a program on the Jarvis host. Set "
                "`mcp: allow_stdio: true` in configuration.yaml first — it is "
                "deliberately not something this request can turn on."
            ),
        }
    if not spec.is_stdio and not spec.url:
        return {"status": "error", "error": "an http MCP server needs a url"}

    spec.tier = spec.tier or manager.default_tier
    manager.servers[spec.name] = spec
    await manager.async_save()
    connected = await manager.async_connect(spec)
    return {
        "status": "ok",
        "name": spec.name,
        "connected": connected,
        "error": manager.errors.get(spec.name, ""),
        "servers": manager.listing(),
    }


async def async_remove_server(manager: MCPManager, name: str) -> dict[str, Any]:
    key = safe_server_name(name)
    spec = manager.servers.get(key)
    if spec is None:
        return {"status": "error", "error": f"no MCP server called {name!r}"}
    if not spec.editable:
        return {
            "status": "error",
            "error": f"{key!r} comes from configuration.yaml; remove it there",
        }
    await manager.async_disconnect(key)
    manager.servers.pop(key, None)
    manager.errors.pop(key, None)
    await manager.async_save()
    return {"status": "ok", "removed": key, "servers": manager.listing()}
