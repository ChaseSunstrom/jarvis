"""`n8n` integration — Jarvis can read, write and manage n8n workflows.

    n8n:
      url: http://127.0.0.1:5678
      api_key: !env_var N8N_API_KEY ""
      allow_activate: false       # see "Activation" below
      tag: jarvis                 # applied to everything Jarvis writes

## Why this exists and Jarvis's own automations still do

n8n is not a second automation engine for the house. Jarvis already has
triggers, conditions, actions, a scheduler and a console for all of it, and
adding a second place a rule could live would mean two places to look when
something did not fire.

What n8n has that Jarvis does not, and never should, is a few hundred
maintained SaaS connectors. So the split is: **n8n owns the outside world,
Jarvis owns the house and the conversation.** Asked to file an expense, Jarvis
writes an n8n workflow; asked to turn the lights off, it does not.

## The three rules that make this safe to hand a model

**1. Jarvis never touches a credential.** Not to read one, not to create one,
not to attach one. A node the model writes arrives carrying
`"credentials": {"gmailOAuth2": {"id": "5"}}` — because every example on the
internet has one — and that id is a guess. `workflows.clean_workflow` strips
the block and REPORTS what it asked for, and a human attaches the real thing
in n8n, where the secrets already are. That report is the whole of "ask for
connections": Jarvis says *"this needs a Gmail credential"*, and a person
connects it.

**2. What Jarvis writes arrives switched off.** The create payload is built
here from four keys rather than forwarded, so a model that sets `active: true`
is not setting anything. A workflow nobody has read has not run.

**3. The model reads structure, not parameters.** `describe_graph` returns
node names, types, which nodes carry a credential, and the edges. It does not
return `parameters`, because that is where people type an API key into an HTTP
header field, and a read of a workflow must not be a read of somebody's secret.

## Activation

Off by default. Activating is the moment a workflow becomes live, and a
workflow Jarvis wrote usually cannot work until a human has attached the
credentials anyway — so the natural order is: Jarvis writes it, a person opens
n8n, connects what it asked for, and switches it on there.

`allow_activate: true` adds an `activate_n8n_workflow` tool at Tier 3 for
operators who would rather approve it from the console. Deactivating needs no
flag: turning something OFF is the safe direction, and it is the button you
want when a workflow is misbehaving at three in the morning.

## What is deliberately missing

*Deleting.* Jarvis does not delete a workflow, the same way it does not delete
a repository. Deactivate it and remove it yourself.

*Running.* n8n's public API has no "run this workflow" endpoint — running one
means calling its webhook, which is an ordinary HTTP call and already
expressible as a tool on the Tools page, with whatever tier the operator
thinks that particular workflow deserves.

*Creating credentials.* See rule 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import DEFAULT_TIMEOUT, N8nClient, N8nError
from .workflows import (
    WorkflowError,
    clean_workflow,
    describe_graph,
    needed_connections,
    summarise,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...services import ServiceCall

_LOGGER = logging.getLogger(__name__)

DOMAIN = "n8n"
DATA_CONFIG = "config"
DATA_CLIENT = "client"

#: Applied to every workflow Jarvis creates, so "what did the assistant write"
#: is one filter in n8n's own UI rather than an archaeology exercise.
DEFAULT_TAG = "jarvis"

__all__ = [
    "DOMAIN",
    "N8nConfig",
    "N8nError",
    "async_setup",
    "get_client",
    "get_config",
    "listing_payload",
]


@dataclass
class N8nConfig:
    url: str = ""
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    #: May Jarvis switch a workflow ON? Deactivating never needs this.
    allow_activate: bool = False
    tag: str = DEFAULT_TAG

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @classmethod
    def from_config(cls, config: Any) -> "N8nConfig":
        data = config if isinstance(config, dict) else {}
        try:
            timeout = float(data.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        return cls(
            url=str(data.get("url") or "").strip().rstrip("/"),
            api_key=str(data.get("api_key") or data.get("token") or "").strip(),
            timeout=max(1.0, min(timeout, 300.0)),
            allow_activate=bool(data.get("allow_activate")),
            tag=str(data.get("tag") or DEFAULT_TAG).strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        # Never the key. `has_key` is what a console needs in order to say
        # "configured" or "set N8N_API_KEY"; the value is of no use to it.
        return {
            "url": self.url,
            "has_key": bool(self.api_key),
            "allow_activate": self.allow_activate,
            "tag": self.tag,
            "configured": self.configured,
        }


def get_config(jarvis: "Jarvis") -> N8nConfig | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    cfg = store.get(DATA_CONFIG)
    return cfg if isinstance(cfg, N8nConfig) else None


def get_client(jarvis: "Jarvis") -> N8nClient | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    client = store.get(DATA_CLIENT)
    return client if isinstance(client, N8nClient) else None


def _require(jarvis: "Jarvis") -> tuple[N8nConfig, N8nClient]:
    cfg = get_config(jarvis)
    client = get_client(jarvis)
    if cfg is None or client is None or not cfg.configured:
        raise N8nError(
            "No n8n instance is configured. Set `n8n: url:` in "
            "configuration.yaml, and an `api_key:` from n8n's "
            "Settings -> n8n API."
        )
    return cfg, client


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = N8nConfig.from_config(config)
    client = N8nClient(cfg.url, cfg.api_key, timeout=cfg.timeout)
    jarvis.data[DOMAIN] = {DATA_CONFIG: cfg, DATA_CLIENT: client}

    _register_services(jarvis)
    _register_tools(jarvis)

    if not cfg.configured:
        _LOGGER.info("n8n: no url configured, so the tools will say so and do nothing")
    elif not cfg.api_key:
        _LOGGER.warning(
            "n8n: %s is configured but there is no api_key, so every call will "
            "be refused with 401. Create one under Settings -> n8n API.",
            cfg.url,
        )
    else:
        _LOGGER.info(
            "n8n ready: %s%s", cfg.url, "" if cfg.allow_activate else " (Jarvis may not activate)"
        )
    return True


# ---------------------------------------------------------------------------
# the operations, shared by the services, the tools and the API
# ---------------------------------------------------------------------------
async def async_list(jarvis: "Jarvis", *, limit: int = 50, active: bool | None = None):
    _cfg, client = _require(jarvis)
    workflows, cursor = await client.list_workflows(limit=limit, active=active)
    return [summarise(w) for w in workflows], cursor


async def async_graph(jarvis: "Jarvis", workflow_id: str) -> dict[str, Any]:
    """One workflow as structure. See `describe_graph` for why not in full."""
    _cfg, client = _require(jarvis)
    workflow = await client.get_workflow(workflow_id)
    graph = describe_graph(workflow)
    graph["connections_needed"] = needed_connections(workflow)
    return graph


async def async_create(
    jarvis: "Jarvis", workflow: Any
) -> tuple[dict[str, Any] | None, str]:
    """Write one. Returns `(result, "")` or `(None, why not)`.

    The workflow is rebuilt from four keys before it is sent, so it arrives
    inactive and with no credential attached whatever the caller wrote.
    """
    cfg, client = _require(jarvis)
    try:
        cleaned = clean_workflow(
            workflow,
            tag_note=(
                f"Tag it {cfg.tag!r} in n8n if you want to find everything "
                "Jarvis wrote." if cfg.tag else ""
            ),
        )
    except WorkflowError as err:
        return None, str(err)

    created = await client.create_workflow(cleaned.payload)
    result = {
        "id": str(created.get("id") or ""),
        "name": cleaned.payload["name"],
        "active": False,
        **cleaned.as_dict(),
    }
    _LOGGER.info(
        "n8n: created workflow %s (%s), %d connection(s) to attach",
        result["id"],
        result["name"],
        len(cleaned.connections_needed),
    )
    return result, ""


async def async_set_active(
    jarvis: "Jarvis", workflow_id: str, active: bool
) -> tuple[bool, str]:
    cfg, client = _require(jarvis)
    if active and not cfg.allow_activate:
        return False, (
            "Jarvis is not allowed to activate workflows here. Switch it on in "
            "n8n, or set `n8n: allow_activate: true` in configuration.yaml if "
            "you would rather approve it from the console."
        )
    await client.set_active(workflow_id, active)
    return True, (
        f"{workflow_id} is now {'active' if active else 'inactive'}."
    )


async def async_executions(
    jarvis: "Jarvis", *, workflow_id: str = "", limit: int = 20
) -> list[dict[str, Any]]:
    _cfg, client = _require(jarvis)
    runs = await client.executions(workflow_id=workflow_id, limit=limit)
    return [
        {
            "id": str(run.get("id") or ""),
            "workflow_id": str(run.get("workflowId") or ""),
            "status": str(run.get("status") or ""),
            "started_at": str(run.get("startedAt") or ""),
            "stopped_at": str(run.get("stoppedAt") or ""),
            "mode": str(run.get("mode") or ""),
        }
        for run in runs
    ]


async def async_probe(jarvis: "Jarvis") -> dict[str, Any]:
    cfg = get_config(jarvis)
    client = get_client(jarvis)
    if cfg is None or client is None:
        return {"ok": False, "detail": "the n8n integration is not set up on this server"}
    return await client.probe()


def listing_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """What the console's n8n panel needs to draw itself."""
    cfg = get_config(jarvis) or N8nConfig()
    return {"instance": cfg.as_dict()}


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------
def _register_services(jarvis: "Jarvis") -> None:
    async def handle_list(call: "ServiceCall") -> dict[str, Any]:
        try:
            rows, cursor = await async_list(jarvis, limit=int(call.get("limit") or 50))
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok", "workflows": rows, "next_cursor": cursor}

    async def handle_get(call: "ServiceCall") -> dict[str, Any]:
        try:
            return {"status": "ok", "workflow": await async_graph(jarvis, str(call.get("id") or ""))}
        except N8nError as err:
            return {"status": "error", "error": str(err)}

    async def handle_create(call: "ServiceCall") -> dict[str, Any]:
        try:
            result, why = await async_create(jarvis, call.get("workflow"))
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        if result is None:
            return {"status": "error", "error": why}
        return {"status": "ok", "workflow": result}

    async def handle_active(call: "ServiceCall") -> dict[str, Any]:
        try:
            ok, note = await async_set_active(
                jarvis, str(call.get("id") or ""), bool(call.get("active"))
            )
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok" if ok else "error", "message": note}

    async def handle_executions(call: "ServiceCall") -> dict[str, Any]:
        try:
            runs = await async_executions(
                jarvis,
                workflow_id=str(call.get("id") or ""),
                limit=int(call.get("limit") or 20),
            )
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok", "executions": runs}

    async def handle_check(call: "ServiceCall") -> dict[str, Any]:
        return await async_probe(jarvis)

    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Every workflow on the configured n8n instance.",
        fields={"limit": {"description": "How many, up to 100."}},
    )
    jarvis.services.register(
        DOMAIN, "get", handle_get, supports_response=True,
        description="One workflow's node graph and what it still needs connected.",
        fields={"id": {"description": "The workflow id.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN, "create", handle_create, supports_response=True,
        description="Create a workflow. It arrives deactivated, with no credentials attached.",
        fields={"workflow": {"description": "The workflow JSON.", "required": True}},
    )
    jarvis.services.register(
        DOMAIN, "set_active", handle_active, supports_response=True,
        description="Activate or deactivate a workflow.",
        fields={
            "id": {"description": "The workflow id.", "required": True},
            "active": {"description": "true to switch on, false to switch off."},
        },
    )
    jarvis.services.register(
        DOMAIN, "executions", handle_executions, supports_response=True,
        description="Recent runs, newest first.",
        fields={
            "id": {"description": "Restrict to one workflow."},
            "limit": {"description": "How many, up to 100."},
        },
    )
    jarvis.services.register(
        DOMAIN, "check", handle_check, supports_response=True,
        description="Whether this url, this key and this n8n version actually work.",
    )


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
def _register_tools(jarvis: "Jarvis") -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("n8n: no LLM tool registry; the services still work")
        return

    from ...llm.tools import TIER_APPROVAL, TIER_DIRECT, schema_object

    cfg = get_config(jarvis) or N8nConfig()

    async def tool_list(args: dict[str, Any], context: Any = None) -> Any:
        try:
            rows, _cursor = await async_list(jarvis, limit=int(args.get("limit") or 30))
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok", "workflows": rows}

    registry.register(
        name="list_n8n_workflows",
        description=(
            "List the workflows on the n8n instance — their names, ids, and "
            "whether each is active. n8n is where Jarvis reaches other people's "
            "services (email, spreadsheets, invoicing); call this before "
            "writing a new workflow so you do not duplicate one."
        ),
        parameters=schema_object(
            {"limit": {"type": "integer", "description": "how many, up to 100"}}, []
        ),
        handler=tool_list,
        tier=TIER_DIRECT,
    )

    async def tool_get(args: dict[str, Any], context: Any = None) -> Any:
        try:
            return {"status": "ok", "workflow": await async_graph(jarvis, str(args.get("id") or ""))}
        except N8nError as err:
            return {"status": "error", "error": str(err)}

    registry.register(
        name="read_n8n_workflow",
        description=(
            "Read one workflow's structure: its nodes, their types, which ones "
            "have a credential attached, and how they are wired. Node "
            "PARAMETERS are deliberately not included — people type API keys "
            "into them."
        ),
        parameters=schema_object(
            {"id": {"type": "string", "description": "the workflow id"}}, ["id"]
        ),
        handler=tool_get,
        tier=TIER_DIRECT,
    )

    async def tool_create(args: dict[str, Any], context: Any = None) -> Any:
        try:
            result, why = await async_create(jarvis, args.get("workflow"))
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        if result is None:
            return {"status": "error", "error": why}
        needed = result.get("connections_needed") or []
        message = f"Created {result['name']!r}. It is switched OFF."
        if needed:
            asked = ", ".join(
                f"{item['credential_type']} for {item['node']!r}" for item in needed
            )
            message += (
                f" Before it can run, connect {asked} in n8n (Credentials -> "
                "New), attach it to the node, then activate the workflow."
            )
        else:
            message += " Review it in n8n and activate it there."
        return {"status": "ok", **result, "message": message}

    registry.register(
        name="create_n8n_workflow",
        description=(
            "Create a workflow on the n8n instance from workflow JSON: an "
            "object with `name`, `nodes` and `connections` in n8n's own format. "
            "Use this for anything involving a third-party service — email, "
            "calendars, spreadsheets, payments — rather than saying you cannot. "
            "It is created SWITCHED OFF and with no credentials: say which "
            "credentials the user has to connect, and that they activate it in "
            "n8n once they have."
        ),
        parameters=schema_object(
            {
                "workflow": {
                    "type": "object",
                    "description": "n8n workflow JSON: name, nodes, connections",
                }
            },
            ["workflow"],
        ),
        handler=tool_create,
        # Tier 3, and not because creating is destructive — it is not. A
        # workflow is a program that will run against somebody's email and
        # somebody's money as soon as it is switched on, and the person who
        # will own that should see it being written.
        tier=TIER_APPROVAL,
    )

    async def tool_deactivate(args: dict[str, Any], context: Any = None) -> Any:
        try:
            ok, note = await async_set_active(jarvis, str(args.get("id") or ""), False)
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok" if ok else "error", "message": note}

    registry.register(
        name="deactivate_n8n_workflow",
        description=(
            "Switch a workflow off. Use it when one is misbehaving or the user "
            "asks for it to stop."
        ),
        parameters=schema_object(
            {"id": {"type": "string", "description": "the workflow id"}}, ["id"]
        ),
        handler=tool_deactivate,
        # Turning something OFF is the safe direction, but it still stops
        # something the household may depend on.
        tier=TIER_APPROVAL,
    )

    if cfg.allow_activate:
        async def tool_activate(args: dict[str, Any], context: Any = None) -> Any:
            try:
                ok, note = await async_set_active(jarvis, str(args.get("id") or ""), True)
            except N8nError as err:
                return {"status": "error", "error": str(err)}
            return {"status": "ok" if ok else "error", "message": note}

        registry.register(
            name="activate_n8n_workflow",
            description=(
                "Switch a workflow on, so its trigger starts firing. Only do "
                "this when the user has said the credentials are connected."
            ),
            parameters=schema_object(
                {"id": {"type": "string", "description": "the workflow id"}}, ["id"]
            ),
            handler=tool_activate,
            tier=TIER_APPROVAL,
        )
