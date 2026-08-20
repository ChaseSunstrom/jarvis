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

## The optional login

    n8n:
      login:
        email: !env_var N8N_LOGIN_EMAIL ""
        password: !env_var N8N_LOGIN_PASSWORD ""

Off by default and never required. n8n has two HTTP surfaces — `/api/v1`,
opened by the API key, and `/rest`, opened only by a session cookie — and
three things live on the second one: the instance's settings (which say
whether its AI builder is licensed), the node type catalogue (which is how a
model stops inventing node names), and the AI builder itself.

Say plainly what that costs: **a login is strictly more powerful than an API
key.** A session also authenticates `/api/v1`, while a key never authenticates
`/rest`, and `/rest` includes the endpoint that mints API keys. Use a
dedicated non-owner n8n user, and put the password in the environment rather
than the file. Everything still works without it; only the three things above
are missing, and each of them says so by name.

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

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .capabilities import N8nCapabilities
from .client import DEFAULT_TIMEOUT, N8nClient, N8nError
from .builder import BuilderClient
from .health import async_health
from .nodes import NodeCatalogue
from .nodes import load as load_catalogue
from .nodes import validate as validate_workflow
from .relay import run_in_background, synthetic_workflow_id
from .session import DEFAULT_REST_PATH, N8nSession
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
DATA_SESSION = "session"
DATA_CAPABILITIES = "capabilities"
DATA_CATALOGUE = "catalogue"
DATA_RELAYS = "relays"
DATA_TRANSCRIPTS = "transcripts"

#: The task kind, so the console can tell a build apart from a coding job.
KIND = "n8n_build"

#: Applied to every workflow Jarvis creates, so "what did the assistant write"
#: is one filter in n8n's own UI rather than an archaeology exercise.
DEFAULT_TAG = "jarvis"

__all__ = [
    "DOMAIN",
    "N8nConfig",
    "N8nError",
    "N8nLogin",
    "async_setup",
    "async_catalogue",
    "async_health",
    "async_build_with_ai",
    "get_capabilities",
    "get_client",
    "get_config",
    "get_session",
    "listing_payload",
]


@dataclass
class N8nLogin:
    """The optional `/rest` login. See the module docstring for what it costs."""

    email: str = ""
    password: str = ""
    #: A TOTP code, for an account with 2FA on. Of limited use in a config
    #: file — it expires in thirty seconds — but it makes a one-off probe
    #: possible, and the sentence that suggests it says so.
    mfa_code: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.email and self.password)

    @classmethod
    def from_config(cls, data: Any) -> "N8nLogin":
        raw = data if isinstance(data, dict) else {}
        return cls(
            email=str(raw.get("email") or raw.get("user") or "").strip(),
            password=str(raw.get("password") or ""),
            mfa_code=str(raw.get("mfa_code") or raw.get("mfa") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        # Never the password, and never the code either — a TOTP is a
        # credential for the thirty seconds it is worth anything.
        return {"email": self.email, "has_password": bool(self.password)}


@dataclass
class N8nConfig:
    url: str = ""
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    #: May Jarvis switch a workflow ON? Deactivating never needs this.
    allow_activate: bool = False
    tag: str = DEFAULT_TAG
    login: N8nLogin = field(default_factory=N8nLogin)
    #: n8n's REST prefix, which `N8N_ENDPOINT_REST` can move. Configurable
    #: rather than assumed, because an instance behind a path rewrite answers
    #: 404 for everything and 404 reads as "too old".
    rest_path: str = DEFAULT_REST_PATH

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
            login=N8nLogin.from_config(data.get("login")),
            rest_path=str(data.get("rest_path") or DEFAULT_REST_PATH).strip(),
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
            "login": self.login.as_dict(),
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


def get_session(jarvis: "Jarvis") -> N8nSession | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    session = store.get(DATA_SESSION)
    return session if isinstance(session, N8nSession) else None


def get_capabilities(jarvis: "Jarvis") -> N8nCapabilities | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    caps = store.get(DATA_CAPABILITIES)
    return caps if isinstance(caps, N8nCapabilities) else None


def get_catalogue(jarvis: "Jarvis") -> NodeCatalogue | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    catalogue = store.get(DATA_CATALOGUE)
    return catalogue if isinstance(catalogue, NodeCatalogue) else None


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
    session = N8nSession(
        cfg.url,
        cfg.login.email,
        cfg.login.password,
        mfa_code=cfg.login.mfa_code,
        rest_path=cfg.rest_path,
        timeout=cfg.timeout,
    )
    jarvis.data[DOMAIN] = {
        DATA_CONFIG: cfg,
        DATA_CLIENT: client,
        DATA_SESSION: session,
        DATA_CAPABILITIES: N8nCapabilities(client=client, session=session),
        DATA_CATALOGUE: NodeCatalogue(),
    }

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
        cleaned = clean_workflow(workflow)
    except WorkflowError as err:
        return None, str(err)

    created = await client.create_workflow(cleaned.payload)
    workflow_id = str(created.get("id") or "")
    tagged = await _tag(client, cfg.tag, workflow_id)
    result = {
        "id": workflow_id,
        "name": cleaned.payload["name"],
        "active": False,
        "tagged": tagged,
        **cleaned.as_dict(),
    }
    _LOGGER.info(
        "n8n: created workflow %s (%s), %d connection(s) to attach",
        result["id"],
        result["name"],
        len(cleaned.connections_needed),
    )
    return result, ""


async def _tag(client: N8nClient, tag: str, workflow_id: str) -> bool:
    """Actually apply the configured tag. Best effort, never fatal.

    This used to be a sentence in the tool result asking the model to tell the
    user to tag it themselves, which is not the same thing as the config
    option claiming everything Jarvis writes is tagged. n8n takes tags in a
    separate call — they are read-only on the workflow — as a list of ids, so
    the tag has to exist first.

    A failure here is logged and swallowed: the workflow is already written,
    and refusing to report a successful create because a label did not stick
    would be the wrong trade.
    """
    if not tag or not workflow_id:
        return False
    try:
        existing = await client.list_tags()
        found = next(
            (t for t in existing if str(t.get("name") or "").lower() == tag.lower()), None
        )
        if found is None:
            found = await client.create_tag(tag)
        tag_id = str(found.get("id") or "")
        if not tag_id:
            return False
        await client.set_workflow_tags(workflow_id, [tag_id])
    except N8nError as err:
        _LOGGER.info("n8n: could not tag %s as %r: %s", workflow_id, tag, err)
        return False
    return True


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


async def async_catalogue(jarvis: "Jarvis", *, force: bool = False) -> NodeCatalogue:
    """This instance's node types, harvested and — with a login — catalogued.

    Cached on `jarvis.data` rather than rebuilt per call, because the harvest
    reads fifty workflows and a model that asks twice in one turn should not
    pay for it twice.
    """
    store = jarvis.data.get(DOMAIN)
    catalogue = await load_catalogue(
        get_client(jarvis),
        get_session(jarvis),
        existing=get_catalogue(jarvis),
        force=force,
    )
    if isinstance(store, dict):
        store[DATA_CATALOGUE] = catalogue
    return catalogue


async def async_validate(jarvis: "Jarvis", workflow: Any) -> dict[str, Any]:
    """Check a workflow without writing it. A report, never a refusal."""
    catalogue = await async_catalogue(jarvis)
    return validate_workflow(workflow, catalogue)


async def async_build_with_ai(
    jarvis: "Jarvis", instruction: str, *, source: str = ""
) -> "Any":
    """Hand a request to n8n's own AI builder. Returns a `Task` or a sentence.

    Fire-and-forget on purpose: the builder can stop to ask questions, and a
    question cannot be answered inside the turn that asked it. See relay.py.
    """
    said = " ".join(str(instruction or "").split())[:2000]
    if not said:
        return "I need to know what the workflow should do."

    cfg = get_config(jarvis)
    session = get_session(jarvis)
    caps = get_capabilities(jarvis)
    if cfg is None or session is None or caps is None:
        return "the n8n integration is not set up on this server"
    if not cfg.configured:
        return (
            "No n8n instance is configured. Set `n8n: url:` in "
            "configuration.yaml."
        )

    await caps.refresh()
    if not caps.builder.available:
        return caps.builder.detail or "n8n's AI builder is not available here."

    registry = getattr(jarvis, "tasks", None)
    if registry is None:  # pragma: no cover - core always builds one
        return "this server has no task registry, so nothing could report progress"

    task = await registry.async_add(
        f"n8n builder: {said}",
        kind=KIND,
        steps=["ask n8n's builder"],
        open_ended=True,
        source=source,
        detail="asking n8n's builder",
    )
    catalogue = await async_catalogue(jarvis)
    builder = BuilderClient(
        session, capabilities=caps, workflow_id=synthetic_workflow_id()
    )
    store = jarvis.data.get(DOMAIN)
    relays = store.setdefault(DATA_RELAYS, {}) if isinstance(store, dict) else {}
    run = asyncio.ensure_future(
        run_in_background(
            jarvis, task, said, builder=builder, node_types=catalogue.listing(limit=60)
        )
    )
    relays[task.id] = run
    run.add_done_callback(lambda _f, tid=task.id: relays.pop(tid, None))
    return task


def transcript_of(jarvis: "Jarvis", task_id: str) -> list[dict[str, str]]:
    """What the builder and the household said to each other.

    For the console only. The model gets one sentence: this is prose written
    by a different AI, and a tool result is read as instructions-adjacent text.
    """
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return []
    rows = store.get(DATA_TRANSCRIPTS) or {}
    found = rows.get(str(task_id or "")) if isinstance(rows, dict) else None
    return list(found) if isinstance(found, list) else []


async def async_probe(jarvis: "Jarvis", *, deep: bool = False) -> dict[str, Any]:
    """"Does this work?" — one line, or three.

    The shallow answer is the public API alone, which is what the console has
    always shown and what every ordinary tool depends on. The deep one logs in
    and asks the instance about itself, so CHECK can say which of the three
    layers is the broken one instead of "n8n: no".
    """
    cfg = get_config(jarvis)
    client = get_client(jarvis)
    if cfg is None or client is None:
        return {"ok": False, "detail": "the n8n integration is not set up on this server"}
    result = await client.probe()
    if not deep:
        return result
    caps = get_capabilities(jarvis)
    if caps is None:
        return result
    await caps.refresh(force=True)
    return {**result, "capabilities": caps.as_dict(), "summary": caps.summary()}


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

    async def tool_node_types(args: dict[str, Any], context: Any = None) -> Any:
        try:
            catalogue = await async_catalogue(jarvis)
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        rows = catalogue.listing(search=str(args.get("search") or ""))
        if not rows:
            return {
                "status": "ok",
                "node_types": [],
                "note": (
                    "Jarvis could not read this instance's node types. Write "
                    "the workflow from what you know and it will still be "
                    "checked when it is created."
                ),
            }
        return {
            "status": "ok",
            "node_types": rows,
            "source": catalogue.source,
            "note": (
                "These are the node types this instance actually has, at the "
                "newest version it has. Use these exact strings and versions."
            ),
        }

    registry.register(
        name="list_n8n_node_types",
        description=(
            "List the node types this n8n instance actually has, with the "
            "version number to write. Call this BEFORE writing workflow JSON: "
            "node type names and versions differ between n8n installs, and a "
            "workflow naming one that is not there saves without complaint "
            "and then does nothing. Pass `search` to narrow it (e.g. 'gmail')."
        ),
        parameters=schema_object(
            {"search": {"type": "string", "description": "narrow it, e.g. 'slack'"}}, []
        ),
        handler=tool_node_types,
        tier=TIER_DIRECT,
    )

    async def tool_validate(args: dict[str, Any], context: Any = None) -> Any:
        try:
            report = await async_validate(jarvis, args.get("workflow"))
        except N8nError as err:
            return {"status": "error", "error": str(err)}
        return {"status": "ok", **report}

    registry.register(
        name="check_n8n_workflow",
        description=(
            "Check workflow JSON without creating anything: node types that do "
            "not exist here, versions that are too new, a missing trigger, and "
            "which credentials a person would have to attach. Returns findings, "
            "not a verdict — fix the errors and call create_n8n_workflow. Free "
            "and instant, so use it before asking the user to approve one."
        ),
        parameters=schema_object(
            {"workflow": {"type": "object", "description": "n8n workflow JSON"}},
            ["workflow"],
        ),
        handler=tool_validate,
        # Tier 1: it writes nothing and reads only structure. The whole value
        # is that a model can iterate on its own JSON without spending an
        # approval to discover a typo.
        tier=TIER_DIRECT,
    )

    if cfg.login.configured:
        # Registered only when a login exists. There is no hide-from-the-model
        # mechanism, so a tool that is always present is a tool the model will
        # try — and "the AI builder is not configured" is a worse answer than
        # never offering it.
        async def tool_build_with_ai(args: dict[str, Any], context: Any = None) -> Any:
            caps = get_capabilities(jarvis)
            started = await async_build_with_ai(
                jarvis, str(args.get("instruction") or ""), source="conversation"
            )
            if isinstance(started, str):
                if caps is not None and not caps.builder.available:
                    return caps.instead()
                return {"status": "error", "error": started}
            return {
                "status": "started",
                "task_id": started.id,
                "message": (
                    "n8n's own workflow builder is working on it now. Tell the "
                    "user it is under way and that it may come back with a "
                    "question they will be asked to answer. Progress is on the "
                    "n8n page. Do not invent a workflow — there is none yet."
                ),
            }

        registry.register(
            name="build_n8n_workflow_with_ai",
            description=(
                "Hand a workflow request to n8n's OWN AI builder, which knows "
                "this instance's nodes and expression language first-hand. Use "
                "it for a workflow that is complicated or involves a service "
                "you are unsure how to wire; write simple ones yourself with "
                "create_n8n_workflow. It runs in the background and may stop "
                "to ask the user a question. The workflow it produces still "
                "arrives switched off with no credentials attached."
            ),
            parameters=schema_object(
                {
                    "instruction": {
                        "type": "string",
                        "description": "what the workflow should do, in plain words",
                    }
                },
                ["instruction"],
            ),
            handler=tool_build_with_ai,
            # Tier 3 for the same reason `create_n8n_workflow` is: it ends in a
            # program written into somebody's automation platform. And no
            # service twin — an automation firing at 3am that puts questions on
            # a lock screen is not a feature.
            tier=TIER_APPROVAL,
        )

    async def tool_health(args: dict[str, Any], context: Any = None) -> Any:
        try:
            return {"status": "ok", **await async_health(jarvis, str(args.get("id") or ""))}
        except N8nError as err:
            return {"status": "error", "error": str(err)}

    registry.register(
        name="check_n8n_health",
        description=(
            "Is a workflow actually working? Joins three things: whether its "
            "credentials are attached, whether it is switched on, and whether "
            "it has run and succeeded. Use this when somebody asks about a "
            "workflow you set up earlier — 'did that run?', 'is that working?' "
            "— instead of saying you cannot tell. Reads run status and timing "
            "only, never what went through the workflow."
        ),
        parameters=schema_object(
            {"id": {"type": "string", "description": "the workflow id"}}, ["id"]
        ),
        handler=tool_health,
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
