"""`llm` integration — wires the Ollama agent, its tools and the gate into Jarvis.

Configuration::

    llm:
      url: http://127.0.0.1:11434
      model: qwen3:8b
      persona_file: prompts/jarvis.txt
      max_tool_rounds: 5
      approval_ttl: 300
      options: {temperature: 0.6}
      expose:
        domains: [light, switch, cover, climate, media_player]
        entities: [sensor.back_door_battery]
        areas: [kitchen]
        exclude_entities: [light.awkward_lamp]
      user_context:
        presence: person.chris
        driving: binary_sensor.chris_driving
        awake: binary_sensor.chris_awake
        active_device: sensor.chris_active_device
      conversation: {ttl: 900, max_turns: 20}
      tools_dir: jarvis_tools          # *.tool.yaml manifests
      tools:                           # or declare them inline
        - name: paperless_search
          description: "Search Paperless-ngx documents by query text"
          tier: 1
          service:
            method: GET
            url: "http://paperless.lan/api/documents/?query={{ query }}"
            headers: {Authorization: "Token abc123"}
            fields:
              query: {description: "search text", required: true}

A top-level ``tools:`` block in configuration.yaml is picked up too.

Services registered:

* ``conversation.process`` (text, conversation_id, agent_id) -> HA-shaped response
* ``llm.approve`` (request_id, approved) -> executes or discards a gated action
* ``llm.pending_requests`` -> what is waiting on approval
* ``llm.clear_conversation`` (conversation_id)

The agent lands in ``jarvis.data["llm"]`` so the voice pipeline finds it.

Tests inject a fake Ollama with ``jarvis.data["llm_transport"] =
httpx.MockTransport(handler)`` (or a ready client in ``jarvis.data["llm_client"]``)
before calling :func:`async_setup`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from ...llm.agent import DEFAULT_MAX_TOOL_ROUNDS, ConversationAgent
from ...llm.memory import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TTL,
    ConversationStore,
)
from ...llm.ollama import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL, OllamaClient
from ...llm.tools import (
    DEFAULT_APPROVAL_TTL,
    Exposure,
    ToolRegistry,
    build_yaml_tools,
    load_tool_manifests,
    register_builtin_tools,
)
from ...services import ServiceCall

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "llm"
DEPENDENCIES = ["domains"]

CONVERSATION_DOMAIN = "conversation"
DATA_TRANSPORT = "llm_transport"
DATA_CLIENT = "llm_client"
DATA_TOOLS = "llm_tools"

AGENT_ID = "jarvis"


#: Strings a caller may send for "yes, run it". Everything else is a refusal.
TRUTHY = frozenset({"true", "yes", "y", "1", "on", "approve", "approved", "ok"})


def parse_approved(value: Any) -> bool:
    """Interpret the ``approved`` flag, failing *closed*.

    ``bool("false")`` is ``True``, so a plain cast turns a refusal that arrived
    over a form post, a query string, YAML (``approved: "false"``) or an MQTT
    payload into an execution of the very action the gate was holding. Only an
    explicit affirmative counts; anything unrecognised denies.

    Omitting the field entirely still means approve — ``llm.approve`` is the
    service you call to say yes, and the REST/websocket layers rely on that.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in TRUTHY
    return False


def _as_dict(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    if isinstance(config, list) and config and isinstance(config[0], dict):
        return config[0]
    return {}


def create_http_client(jarvis: "Jarvis", timeout: float) -> httpx.AsyncClient:
    """Shared AsyncClient for both Ollama and YAML tools, honouring injection."""
    existing = jarvis.data.get(DATA_CLIENT)
    if isinstance(existing, httpx.AsyncClient):
        return existing
    transport = jarvis.data.get(DATA_TRANSPORT)
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )
    jarvis.data[DATA_CLIENT] = client
    return client


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = _as_dict(config)

    url = str(options.get("url") or options.get("host") or DEFAULT_URL)
    model = str(options.get("model") or DEFAULT_MODEL)
    timeout = float(options.get("timeout") or DEFAULT_TIMEOUT)
    max_tool_rounds = int(options.get("max_tool_rounds") or DEFAULT_MAX_TOOL_ROUNDS)
    approval_ttl = float(options.get("approval_ttl") or DEFAULT_APPROVAL_TTL)

    client = create_http_client(jarvis, timeout)
    ollama = OllamaClient(
        url=url,
        model=model,
        timeout=timeout,
        client=client,
        keep_alive=options.get("keep_alive"),
    )

    exposure = Exposure.from_config(options.get("expose"))
    registry = ToolRegistry(jarvis, exposure=exposure, approval_ttl=approval_ttl)
    register_builtin_tools(registry, _as_dict(options.get("user_context")))

    specs: list[Any] = []
    if options.get("tools_dir"):
        directory = jarvis.config_dir / str(options["tools_dir"])
        specs.extend(load_tool_manifests(directory))
    for source in (options.get("tools"), (jarvis.config or {}).get("tools")):
        if isinstance(source, list):
            specs.extend(source)
        elif isinstance(source, dict):
            specs.extend(
                {**value, "name": value.get("name", key)}
                for key, value in source.items()
                if isinstance(value, dict)
            )
    if specs:
        built = build_yaml_tools(registry, specs, client_factory=lambda: client)
        _LOGGER.info("Registered %d YAML-defined tool(s)", len(built))

    conversation_options = _as_dict(options.get("conversation"))
    memory = ConversationStore(
        max_turns=int(conversation_options.get("max_turns") or DEFAULT_MAX_TURNS),
        ttl=float(conversation_options.get("ttl") or DEFAULT_TTL),
    )

    agent = ConversationAgent(
        jarvis,
        ollama,
        registry,
        model=model,
        persona=options.get("persona"),
        persona_file=options.get("persona_file"),
        max_tool_rounds=max_tool_rounds,
        memory=memory,
        options=_as_dict(options.get("options")),
        language=str(options.get("language") or "en"),
    )

    jarvis.data[DOMAIN] = agent
    jarvis.data[DATA_TOOLS] = registry

    _register_services(jarvis, agent, registry)

    async def _shutdown() -> None:
        await ollama.aclose()
        if jarvis.data.get(DATA_CLIENT) is client and not client.is_closed:
            await client.aclose()

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "LLM agent ready: model=%s url=%s tools=%d", model, url, len(registry.tools)
    )
    return True


def _register_services(
    jarvis: "Jarvis", agent: ConversationAgent, registry: ToolRegistry
) -> None:
    async def handle_process(call: ServiceCall) -> dict[str, Any]:
        text = str(call.get("text") or call.get("input") or "")
        agent_id = call.get("agent_id")
        if agent_id and str(agent_id) not in (AGENT_ID, DOMAIN, agent.model):
            _LOGGER.debug("conversation.process for agent_id=%r handled by jarvis", agent_id)
        result = await agent.process(text, call.get("conversation_id"))
        return result.as_conversation_response(
            str(call.get("language") or agent.language)
        )

    jarvis.services.register(
        CONVERSATION_DOMAIN,
        "process",
        handle_process,
        description="Send text to the conversation agent and get its reply.",
        fields={
            "text": {"description": "What the user said.", "required": True},
            "conversation_id": {"description": "Continue an existing conversation."},
            "agent_id": {"description": "Which agent to use (default: jarvis)."},
            "language": {"description": "Response language hint."},
        },
        supports_response=True,
    )

    async def handle_approve(call: ServiceCall) -> dict[str, Any]:
        request_id = str(call.get("request_id") or "")
        return await registry.approve_request(
            request_id, parse_approved(call.get("approved"))
        )

    jarvis.services.register(
        DOMAIN,
        "approve",
        handle_approve,
        description="Approve (or deny) an action the safety gate is holding.",
        fields={
            "request_id": {"description": "Id from the approval request.", "required": True},
            "approved": {"description": "true to execute, false to discard."},
        },
        supports_response=True,
    )

    async def handle_pending(call: ServiceCall) -> dict[str, Any]:
        return {"pending": registry.pending_requests()}

    jarvis.services.register(
        DOMAIN,
        "pending_requests",
        handle_pending,
        description="List actions waiting on human approval.",
        supports_response=True,
    )

    async def handle_clear(call: ServiceCall) -> dict[str, Any]:
        conversation_id = call.get("conversation_id")
        if conversation_id:
            return {"cleared": agent.memory.remove(str(conversation_id))}
        agent.memory.clear()
        return {"cleared": True}

    jarvis.services.register(
        DOMAIN,
        "clear_conversation",
        handle_clear,
        description="Forget one conversation, or all of them.",
        fields={"conversation_id": {"description": "Leave empty to clear everything."}},
        supports_response=True,
    )

    async def handle_list_models(call: ServiceCall) -> dict[str, Any]:
        return {"models": await agent.client.list_models()}

    jarvis.services.register(
        DOMAIN,
        "list_models",
        handle_list_models,
        description="Models the local Ollama has available.",
        supports_response=True,
    )
