"""n8n — the operator's own automations, callable by name, and off until they say so.

    n8n:
      enabled: false                      # the default, and it means it
      url: !env_var N8N_URL http://127.0.0.1:5678
      api_key: !secret n8n_api_key
      workflows:                          # the allow-list. Empty = nothing runs.
        - name: bins
          id: 42
          webhook: bins-out
          description: Put the bins out reminder on the calendar
          tier: 3                         # 3 = a human says yes first (the default)

Somebody already has an n8n. It is where their house's odd jobs live — the
thing that renames the camera clips, the thing that emails the meter reading —
and none of that should be rebuilt here. This bridge exposes **named** workflows
as tools the model can call, and nothing else.

## Three refusals, in order

**Off by default.** `enabled: false` is the shipped value and an install that
never touches this file gets no tools, no HTTP client and no listener. This is
a reach surface — it runs code on another machine — and `PROCESS.md` §2d says a
reach surface is opt-in.

**An allow-list, not a discovery.** n8n's API can list every workflow on the
instance; this deliberately does not turn that into tools. A workflow appears
here because the operator typed its name, which means adding a workflow to n8n
can never silently add a capability to Jarvis. `workflows: []` with
`enabled: true` is a valid, useless configuration and stays that way.

**Tier 3 unless told otherwise.** Running somebody's automation is a
state-changing action with effects this process cannot see — it might send an
email or open a garage. Each entry may lower its own tier deliberately
(`tier: 1` for something read-only), and that is a sentence in their config
file rather than a default nobody chose.

## How a workflow is called

Through its **webhook**, not through the API's execution endpoint: n8n's public
API cannot start an arbitrary workflow, and a Webhook trigger node is the
supported way in. A configured workflow with no `webhook:` is listed by
`n8n.list` and refuses to run, saying which node it needs — which is a better
failure than a 404 from a URL nobody meant to call.

Services
    ``n8n.list``    → the allow-listed workflows, and whether each can be run
    ``n8n.run``     (name, data) → run one, by the name the operator gave it

LLM tools: one per allow-listed workflow, named ``n8n_<name>``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "n8n"
DEPENDENCIES = ["llm"]

#: How long a workflow may take before the tool gives up. Longer than a web
#: request because an n8n workflow does real work — but bounded, because the
#: turn behind it is somebody standing there.
DEFAULT_TIMEOUT = 60.0

#: Tools are named `n8n_<name>`, so the name has to survive being an identifier.
SAFE_NAME = re.compile(r"[^a-z0-9_]+")


class N8nError(RuntimeError):
    """The bridge could not do what was asked, and says which part."""


@dataclass
class Workflow:
    """One allow-listed workflow, exactly as the operator described it."""

    name: str
    id: str = ""
    webhook: str = ""
    description: str = ""
    tier: int = 3
    method: str = "POST"

    @property
    def tool_name(self) -> str:
        return f"n8n_{SAFE_NAME.sub('_', self.name.strip().lower()).strip('_')}"

    @property
    def runnable(self) -> bool:
        return bool(self.webhook)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id or None,
            "tool": self.tool_name,
            "runnable": self.runnable,
            "tier": self.tier,
            "description": self.description,
            "why_not": "" if self.runnable else "no webhook: node named in its config entry",
        }


@dataclass
class Bridge:
    """The configured bridge. Holds no credentials it was not given."""

    url: str = ""
    api_key: str = ""
    enabled: bool = False
    timeout: float = DEFAULT_TIMEOUT
    workflows: list[Workflow] = field(default_factory=list)
    #: Swapped in tests. Anything with `.post()` and `.get()` returning httpx-
    #: shaped responses will do.
    client: Any = None

    def find(self, name: str) -> Workflow | None:
        wanted = str(name or "").strip().lower()
        for workflow in self.workflows:
            if workflow.name.strip().lower() == wanted or workflow.tool_name == wanted:
                return workflow
        return None

    def webhook_url(self, workflow: Workflow) -> str:
        return f"{self.url.rstrip('/')}/webhook/{workflow.webhook.lstrip('/')}"

    async def run(self, name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one allow-listed workflow. Every refusal names itself."""
        if not self.enabled:
            raise N8nError(
                "the n8n bridge is off — set `n8n: enabled: true` in configuration.yaml"
            )
        workflow = self.find(name)
        if workflow is None:
            known = ", ".join(w.name for w in self.workflows) or "nothing"
            raise N8nError(
                f"{name!r} is not in the n8n allow-list, so it cannot be run. "
                f"Allowed: {known}"
            )
        if not workflow.runnable:
            raise N8nError(
                f"{workflow.name!r} has no `webhook:` in its config entry, so there is "
                "no supported way to start it — n8n's API cannot run an arbitrary "
                "workflow, only a Webhook trigger node can"
            )
        payload = dict(data or {})
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        url = self.webhook_url(workflow)
        try:
            if self.client is not None:
                answer = await self.client.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            else:
                import httpx

                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    answer = await http.post(url, json=payload, headers=headers)
        except Exception as err:  # noqa: BLE001 - one error type out of here
            raise N8nError(f"could not reach n8n at {url}: {type(err).__name__}") from err
        status = int(getattr(answer, "status_code", 0) or 0)
        if status >= 400:
            raise N8nError(f"n8n answered {status} for {workflow.name!r}")
        try:
            body = answer.json()
        except Exception:  # noqa: BLE001 - a workflow may answer with nothing at all
            body = {"body": str(getattr(answer, "text", "") or "")[:500]}
        return {"status": "ok", "workflow": workflow.name, "result": body}


def _workflows(raw: Any) -> list[Workflow]:
    out: list[Workflow] = []
    for entry in raw or []:
        if isinstance(entry, str):
            out.append(Workflow(name=entry))
            continue
        if not isinstance(entry, dict) or not entry.get("name"):
            _LOGGER.warning("Ignoring an n8n workflow entry with no name: %r", entry)
            continue
        out.append(
            Workflow(
                name=str(entry["name"]),
                id=str(entry.get("id") or ""),
                webhook=str(entry.get("webhook") or ""),
                description=str(entry.get("description") or ""),
                # Tier 3 unless the operator deliberately lowered it. `int()` on
                # a bad value would raise at setup; a bad value should mean "the
                # safe one", not "no Jarvis today".
                tier=_tier(entry.get("tier")),
                method=str(entry.get("method") or "POST").upper(),
            )
        )
    return out


def _tier(value: Any) -> int:
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return 3
    return tier if tier in (1, 2, 3) else 3


def build(config: Any) -> Bridge:
    """The bridge a config block describes. Pure, so tests need no Jarvis."""
    options = config if isinstance(config, dict) else {}
    return Bridge(
        url=str(options.get("url") or "").strip(),
        api_key=str(options.get("api_key") or "").strip(),
        enabled=bool(options.get("enabled", False)),
        timeout=float(options.get("timeout") or DEFAULT_TIMEOUT),
        workflows=_workflows(options.get("workflows")),
    )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    bridge = build(config)
    jarvis.data[DOMAIN] = bridge

    async def _list(_call: Any) -> dict[str, Any]:
        return {
            "enabled": bridge.enabled,
            "url": bridge.url or None,
            "workflows": [w.as_dict() for w in bridge.workflows],
        }

    async def _run(call: Any) -> dict[str, Any]:
        try:
            return await bridge.run(
                str(call.data.get("name") or ""),
                call.data.get("data") if isinstance(call.data.get("data"), dict) else {},
            )
        except N8nError as err:
            return {"status": "error", "error": str(err)}

    jarvis.services.register(
        DOMAIN, "list", _list,
        description="The allow-listed n8n workflows, and whether each can be run.",
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN, "run", _run,
        description="Run one allow-listed n8n workflow by name.",
        fields={"name": {"description": "the name in the allow-list"},
                "data": {"description": "JSON body for the webhook"}},
        supports_response=True,
    )

    if not bridge.enabled:
        # Not a warning: this is the shipped state and the operator chose it by
        # not choosing anything.
        _LOGGER.info("n8n bridge: off (n8n: enabled: false). No tools registered.")
        return True
    if not bridge.url:
        _LOGGER.warning("n8n bridge: enabled but no `url:` — nothing can be called.")
        return True

    tools = jarvis.data.get("llm_tools") or getattr(jarvis.data.get("llm"), "tools", None)
    registered = 0
    for workflow in bridge.workflows:
        if tools is None:
            break
        registered += _register_tool(tools, bridge, workflow)
    _LOGGER.info(
        "n8n bridge: %d workflow(s) allow-listed, %d callable, against %s",
        len(bridge.workflows), registered, bridge.url,
    )
    return True


def _register_tool(tools: Any, bridge: Bridge, workflow: Workflow) -> int:
    """One tool per allow-listed workflow. Returns 1 if it can actually run."""
    if not workflow.runnable:
        _LOGGER.warning(
            "n8n workflow %r has no `webhook:`, so it is listed but not callable",
            workflow.name,
        )
        return 0

    async def _handler(call: Any) -> dict[str, Any]:
        data = dict(getattr(call, "arguments", None) or {})
        try:
            return await bridge.run(workflow.name, data)
        except N8nError as err:
            return {"status": "error", "error": str(err)}

    from ...llm.tools import TIER_APPROVAL

    tools.register(
        name=workflow.tool_name,
        description=(
            workflow.description
            or f"Run the {workflow.name!r} automation on the house's n8n."
        )[:200],
        parameters={
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "values to send to the workflow, if it takes any",
                }
            },
        },
        handler=_handler,
        tier=workflow.tier if workflow.tier in (1, 2, 3) else TIER_APPROVAL,
        domain=DOMAIN,
    )
    return 1
