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
      tools_dir: tools                 # *.tool.yaml manifests, under the config dir
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
import time
from typing import TYPE_CHECKING, Any

import httpx

from ...llm.agent import DEFAULT_MAX_TOOL_ROUNDS, ConversationAgent
from ...llm.memory import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TTL,
    ConversationStore,
)
from ...llm.ollama import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL, OllamaClient
from ...llm.openai_compat import OpenAICompatClient
from ...llm.authored_tools import get_authored_tools
from ...llm.tools import (
    DEFAULT_APPROVAL_TTL,
    EVENT_APPROVAL_REQUIRED,
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

#: Where `companion.ask` lives. Imported by name rather than from the module so
#: a build without the companion integration simply never finds the service.
COMPANION_DOMAIN = "companion"

#: Prepended to a question raised by a turn that had already read somebody
#: else's words. The console has a field for this; a phone notification does
#: not, so it goes in the sentence — see `_ask_on_a_device`.
UNTRUSTED_PREFIX = "[from an outside source]"

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


#: `llm: backend:` values, and what each is for.
#:
#: `ollama` is the native `/api/chat` wire — NDJSON, `keep_alive`, a separate
#: `thinking` field, `/api/tags` for model management.
#:
#: `openai` is `/v1/chat/completions`, which **vLLM**, llama.cpp's server, LM
#: Studio, TGI, SGLang and Ollama itself all serve. It is what makes the
#: inference server a deployment decision instead of an architectural one, and
#: it is the only one of the two that can do guided decoding or embeddings.
BACKENDS = ("ollama", "openai")


def _detect_backend(url: str) -> str:
    """Which wire a url is asking for, when nobody said.

    A url ending in `/v1` is unambiguous — Ollama's native API has no such
    path, and every OpenAI-compatible server serves exactly that. Anything else
    defaults to `ollama`, so an existing install that never heard of this
    setting keeps the behaviour it had.
    """
    tail = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    return "openai" if tail in ("v1", "openai") else "ollama"


def _build_model_client(
    options: dict[str, Any],
    url: str,
    model: str,
    timeout: float,
    client: Any,
) -> Any:
    """The chat client, on whichever wire the deployment asked for.

    Both classes present the same surface — `chat`, `list_models`,
    `is_available`, `aclose` — so `ConversationAgent` never learns which it
    got. That is the point: the agent's job is the conversation, not the
    transport.
    """
    backend = str(options.get("backend") or "").strip().lower()
    if not backend:
        backend = _detect_backend(url)
    elif backend not in BACKENDS:
        _LOGGER.warning(
            "Unknown llm backend %r; falling back to %r. Known: %s",
            backend,
            _detect_backend(url),
            ", ".join(BACKENDS),
        )
        backend = _detect_backend(url)

    if backend == "openai":
        _LOGGER.info("LLM backend: OpenAI-compatible at %s", url)
        return OpenAICompatClient(
            url=url,
            model=model,
            timeout=timeout,
            client=client,
            api_key=options.get("api_key") or None,
            label=str(options.get("backend_name") or "the model server"),
        )

    _LOGGER.info("LLM backend: Ollama at %s", url)
    return OllamaClient(
        url=url,
        model=model,
        timeout=timeout,
        client=client,
        keep_alive=options.get("keep_alive"),
    )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = _as_dict(config)

    url = str(options.get("url") or options.get("host") or DEFAULT_URL)
    model = str(options.get("model") or DEFAULT_MODEL)
    timeout = float(options.get("timeout") or DEFAULT_TIMEOUT)
    max_tool_rounds = int(options.get("max_tool_rounds") or DEFAULT_MAX_TOOL_ROUNDS)
    approval_ttl = float(options.get("approval_ttl") or DEFAULT_APPROVAL_TTL)

    client = create_http_client(jarvis, timeout)
    ollama = _build_model_client(options, url, model, timeout, client)

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

    # Console-created tools, built last so `validate`'s refusal to shadow an
    # existing name is enforced by the same ordering at boot as at create time:
    # everything else is already registered by the time these are added.
    authored = get_authored_tools(jarvis)
    authored_specs = await authored.async_load()
    if authored_specs:
        made = build_yaml_tools(registry, authored_specs, client_factory=lambda: client)
        _LOGGER.info("Registered %d console-defined tool(s)", len(made))

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
    _bridge_questions_to_the_phone(jarvis, registry)

    async def _shutdown() -> None:
        await ollama.aclose()
        if jarvis.data.get(DATA_CLIENT) is client and not client.is_closed:
            await client.aclose()

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "LLM agent ready: model=%s url=%s tools=%d", model, url, len(registry.tools)
    )
    return True


def _bridge_questions_to_the_phone(jarvis: "Jarvis", registry: ToolRegistry) -> None:
    """Put a question on the user's phone as well as on the console.

    A held request that names an `answerable` argument is a QUESTION rather than
    an action — see `ask_user`. The console draws it in its approvals banner,
    which is the right place when somebody is sitting at a screen. Most of the
    time they are not: they asked out loud, walked off, and the console is in
    another room with nobody looking at it.

    `companion.ask` already knows how to reach whichever device the user is
    actually at, render options as buttons and take a spoken answer. So a
    question goes to both, and **whichever answers first wins** — safely and
    without any coordination, because `approve_request` pops the request before
    it does anything. The loser gets "unknown, expired or already-used", which
    is exactly the truth.

    Only questions are bridged. A tier-3 ACTION deliberately stays on the
    surfaces that already show it: this is a route for supplying a fact, not a
    second, quieter way to consent to unlocking a door.
    """

    def _on_request(event: Any) -> None:
        data = getattr(event, "data", None) or {}
        if not data.get("answerable"):
            return
        request_id = str(data.get("request_id") or "")
        if not request_id:
            return
        arguments = data.get("arguments") or {}
        question = str(arguments.get("question") or data.get("description") or "").strip()
        if not question:
            return
        if not jarvis.services.has_service(COMPANION_DOMAIN, "ask"):
            return
        jarvis.async_create_task(_ask_on_a_device(jarvis, registry, request_id, question, data))

    jarvis.bus.listen(EVENT_APPROVAL_REQUIRED, _on_request)


async def _ask_on_a_device(
    jarvis: "Jarvis",
    registry: ToolRegistry,
    request_id: str,
    question: str,
    data: dict[str, Any],
) -> None:
    """Deliver one question and, if it is answered there, resolve it."""
    tainted = bool(data.get("tainted"))
    try:
        answered = await jarvis.services.async_call(
            COMPANION_DOMAIN,
            "ask",
            {
                # The phone renders this verbatim and has no field for
                # provenance, so provenance goes in the words. A turn that has
                # read a hostile page can compose this sentence, and somebody
                # glancing at a lock screen has no other way to know that.
                "question": f"{UNTRUSTED_PREFIX} {question}" if tainted else question,
                "options": list(data.get("choices") or []),
                # The clock the request is already on. Asking a phone for longer
                # than the request lives would put a live-looking prompt in
                # somebody's hand for an answer nothing can still accept.
                "timeout": max(5.0, float(data.get("expires_at", 0)) - time.time()),
            },
            blocking=True,
            return_response=True,
        )
    except Exception:  # pragma: no cover - a phone that is not there is normal
        _LOGGER.debug("Could not put the question on a device", exc_info=True)
        return
    reply = (answered or {}).get("answer") if isinstance(answered, dict) else None
    if reply is None or str(reply).strip() == "":
        # Dismissed, timed out, or no device. The console's copy is still live.
        return
    result = await registry.approve_request(request_id, True, str(reply))
    if result.get("status") == "error":
        # Somebody answered on the console first. Nothing to do and nothing
        # wrong: the pop is what made the race safe.
        _LOGGER.debug("The question %s was already answered elsewhere", request_id)


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
        # `answer` reaches exactly one argument, named by the tool itself, and
        # is ignored for every tool that did not opt in — see
        # `ToolRegistry.approve_request` and `Tool.answerable`.
        return await registry.approve_request(
            request_id, parse_approved(call.get("approved")), call.get("answer")
        )

    jarvis.services.register(
        DOMAIN,
        "approve",
        handle_approve,
        description="Approve (or deny) an action the safety gate is holding.",
        fields={
            "request_id": {"description": "Id from the approval request.", "required": True},
            "approved": {"description": "true to execute, false to discard."},
            "answer": {
                "description": (
                    "What the human replied, for a request that is a question. "
                    "Ignored by tools that do not take an answer."
                )
            },
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
