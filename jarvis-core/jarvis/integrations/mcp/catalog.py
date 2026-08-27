"""What an MCP server is allowed to become inside Jarvis.

No I/O and no protocol here — this is the part that decides what a third party's
tool list is permitted to turn into once it reaches the model, and it is
deliberately the part that is easiest to test.

## The threat, stated plainly

An MCP server is somebody else's code, reached over somebody else's transport,
returning somebody else's JSON. Everything it says is a *claim*: its tool names
are claims, its descriptions are claims, and its results are text written by a
party that is not the user. Three things follow, and each of them is a function
in this file:

**A tool name is namespaced, never taken as given.** `mcp_<server>_<tool>`.
Without this, a server that exposes a tool called `control_device` or `ask_user`
is trying to shadow a built-in — and the registry's own re-registration guard is
about *weakening*, not about impersonation. Namespacing means the collision
cannot arise, and `safe_tool_name` refuses anything that would still be
ambiguous.

**A description is data, not instruction.** It is put in front of the model, so
it is exactly a prompt-injection surface. It is clipped, stripped of the
control characters that would let it break out of its own field, and prefixed
with where it came from.

**A tier is ours to choose, never the server's.** The tool list arrives with no
notion of risk and a server has every incentive to be convenient. The default is
CONFIRM, and only the operator's own configuration may lower it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_TOOLS_PER_SERVER",
    "MCPTool",
    "ServerSpec",
    "describe_tool",
    "namespaced",
    "safe_server_name",
    "safe_tool_name",
    "sanitise_schema",
    "server_from_dict",
]

MAX_TOOLS_PER_SERVER = 64
MAX_DESCRIPTION_CHARS = 600
MAX_NAME_CHARS = 48
#: How deep a JSON Schema may nest before it is refused. A model provider will
#: reject a pathological schema anyway; refusing here means one bad server
#: cannot make every subsequent turn fail.
MAX_SCHEMA_DEPTH = 8

#: The prefix every MCP tool carries. Chosen so it can never collide with a
#: built-in: no built-in starts with it, and `safe_tool_name` will not produce
#: a name that is only this.
PREFIX = "mcp"

_NAME_OK = re.compile(r"[^a-z0-9_]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_server_name(raw: Any) -> str:
    """A short, lowercase, identifier-shaped name for one server.

    The name reaches the model as part of a tool name, so it may not contain
    anything that would confuse a function-calling schema.
    """
    text = _NAME_OK.sub("_", str(raw or "").strip().lower()).strip("_")
    return text[:MAX_NAME_CHARS]


def safe_tool_name(raw: Any) -> str:
    """The same treatment for the server's own name for a tool."""
    return safe_server_name(raw)


def namespaced(server: str, tool: str) -> str:
    """`mcp_<server>_<tool>`, or "" if either half is unusable.

    Empty rather than a fallback: a tool whose name did not survive
    normalisation is one nobody can call deliberately, and inventing a name for
    it puts an unidentifiable capability in front of the model.
    """
    left, right = safe_server_name(server), safe_tool_name(tool)
    if not left or not right:
        return ""
    return f"{PREFIX}_{left}_{right}"


def describe_tool(server: str, description: Any, *, url: str = "") -> str:
    """The description the model sees, said to be a third party's words.

    Two things are doing work here. The **provenance** — a model deciding
    whether to call a tool should know it is not one of the house's own. And the
    **flattening**: a description containing newlines and role-ish text is the
    cheapest prompt injection there is, because it is the one field a server
    fully controls that is quoted verbatim into the system prompt.
    """
    text = _CONTROL.sub(" ", str(description or "")).strip()
    text = " ".join(text.split())[:MAX_DESCRIPTION_CHARS]
    where = f" ({url})" if url else ""
    if not text:
        text = "no description given"
    return f"[from the MCP server '{server}'{where}] {text}"


def sanitise_schema(raw: Any, *, depth: int = 0) -> dict[str, Any]:
    """Keep a JSON Schema the model provider will accept, or fall back to open.

    A server's `inputSchema` goes straight into a function-calling request. A
    malformed or absurdly nested one does not break this integration — it breaks
    **every turn**, including the ones that have nothing to do with MCP, because
    the whole tool list is sent on every call. So anything unusable becomes a
    permissive object rather than an exception.
    """
    if not isinstance(raw, dict) or depth > MAX_SCHEMA_DEPTH:
        return {"type": "object", "properties": {}}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            out[key] = sanitise_schema(value, depth=depth + 1)
        elif isinstance(value, list):
            out[key] = [
                sanitise_schema(v, depth=depth + 1) if isinstance(v, dict) else v
                for v in value[:64]
            ]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    out.setdefault("type", "object")
    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


@dataclass
class MCPTool:
    """One tool a server offered, after it has been made safe to register."""

    server: str
    #: What the server calls it — what goes back over the wire on a call.
    remote_name: str
    #: What Jarvis calls it. Namespaced, so it cannot shadow anything.
    name: str
    description: str
    parameters: dict[str, Any]
    tier: int


@dataclass
class ServerSpec:
    """One configured MCP server.

    `transport` is `http` or `stdio`, and the difference is not a detail — see
    the module docstring in `__init__.py`. An http server is a URL Jarvis talks
    to; a stdio server is a **process Jarvis starts**, which is arbitrary code
    execution on the host and is gated accordingly.
    """

    name: str
    transport: str = "http"
    url: str = ""
    token: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: What tier this server's tools register at. Never taken from the server.
    tier: int = 2
    enabled: bool = True
    #: Set when the console added it, so config-authored servers cannot be
    #: edited or deleted through the API — the file is the authority for those.
    editable: bool = False

    @property
    def is_stdio(self) -> bool:
        return self.transport == "stdio"

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        """For the API and the store.

        `redact` because this is what the console lists, and a bearer token has
        no business travelling to a browser that is only drawing a row.
        """
        out: dict[str, Any] = {
            "name": self.name,
            "transport": self.transport,
            "url": self.url,
            "command": self.command,
            "args": list(self.args),
            "tier": self.tier,
            "enabled": self.enabled,
            "editable": self.editable,
            "has_token": bool(self.token),
        }
        if not redact:
            out["token"] = self.token
            out["env"] = dict(self.env)
        return out


def server_from_dict(raw: Any, *, editable: bool = False) -> ServerSpec | None:
    """Read one server out of YAML or the store. None if it is not one."""
    if not isinstance(raw, dict):
        return None
    name = safe_server_name(raw.get("name"))
    if not name:
        return None
    transport = str(raw.get("transport") or "").strip().lower()
    if transport not in ("http", "stdio"):
        # Inferred rather than defaulted: a record with a command and no
        # transport plainly means stdio, and guessing http for it would make
        # a stdio server silently never start.
        transport = "stdio" if raw.get("command") else "http"

    try:
        tier = int(raw.get("tier") or 2)
    except (TypeError, ValueError):
        tier = 2
    return ServerSpec(
        name=name,
        transport=transport,
        url=str(raw.get("url") or "").strip(),
        token=str(raw.get("token") or "").strip(),
        command=str(raw.get("command") or "").strip(),
        args=[str(a) for a in (raw.get("args") or []) if str(a)][:32],
        env={
            str(k): str(v)
            for k, v in (raw.get("env") or {}).items()
            if isinstance(k, str)
        },
        # Clamped to the three real tiers. A server list that said `tier: 0`
        # would otherwise register something below Tier 1, which is not a thing
        # and would sail past every gate that checks `>=`.
        tier=min(3, max(1, tier)),
        enabled=bool(raw.get("enabled", True)),
        editable=editable,
    )
