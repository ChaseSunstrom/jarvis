"""`llm` integration — wires the Ollama agent, its tools and the gate into Jarvis.

Configuration::

    llm:
      url: http://127.0.0.1:11434
      model: qwen3:8b
      persona_file: prompts/jarvis.txt
      max_tool_rounds: 5
      max_concurrent: 2        # model calls in flight at once (subagents)
      call_timeout: 300        # seconds for one whole model call, stall or not
      approval_ttl: 300        # seconds a held ACTION waits for a yes
      question_ttl: 1800       # seconds a QUESTION (ask_user) waits for its answer
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

import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

from typing import TYPE_CHECKING, Any

import httpx

from ...llm.agent import DEFAULT_MAX_TOOL_ROUNDS, ConversationAgent
from ...llm.history import (
    DEFAULT_MAX_CONVERSATIONS,
    ConversationArchive,
)
from ...llm.memory import (
    DEFAULT_MAX_TURNS,
    DEFAULT_TTL,
    ConversationStore,
)
from ...store import Store
from ...llm.ollama import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL, OllamaClient
from ...llm.openai_compat import OpenAICompatClient
from ...llm.authored_tools import get_authored_tools
from ...llm.tools import (
    DEFAULT_APPROVAL_TTL,
    DEFAULT_QUESTION_TTL,
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
#: Where the conversation archive lands in `jarvis.data`, and its file name
#: under `<config>/.storage/`. The API layer reads the first without importing
#: this module, the way every other registry is reached.
DATA_HISTORY = "llm_history"
#: How many model calls may be in flight at once. Read by `llm/pool.py` through
#: `max_concurrent_for`; see `DEFAULT_MAX_CONCURRENT` there for why it is two.
DATA_MAX_CONCURRENT = "llm_max_concurrent"
HISTORY_STORE_KEY = "conversations"

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


def _bounded_concurrency(value: Any) -> int:
    """A sane `max_concurrent`, whatever the config file says.

    Bounded at eight because this is one model server: a config that asked for
    forty would not get forty times the work, it would get a queue inside
    llama-swap instead of one here, where it can be seen.
    """
    from ...llm.pool import DEFAULT_MAX_CONCURRENT

    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT
    return max(1, min(number, 8))


def max_concurrent_for(jarvis: "Jarvis") -> int:
    """What the operator set, or the default if the llm block never loaded."""
    from ...llm.pool import DEFAULT_MAX_CONCURRENT

    return _bounded_concurrency(jarvis.data.get(DATA_MAX_CONCURRENT) or DEFAULT_MAX_CONCURRENT)


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


def _tristate(value: Any) -> bool | None:
    """`true`/`false` from config, or None for "don't mention it".

    Three states, not two: a missing `think:` must leave the request field out
    entirely so the model's own default applies, which is what every install
    had before the key existed. Coercing absence to `False` would be a silent
    behaviour change for everyone who never opted in.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    _LOGGER.warning("llm: think: %r is not a boolean; leaving it unset", value)
    return None


def _scalar(value: Any) -> str:
    """A YAML scalar with the `!env_var NAME ""` artefact stripped.

    `config.py` keeps an `!env_var` default token verbatim, so an unset variable
    written with an empty-string default arrives here as the two *characters*
    `""` rather than as an empty string. Passed through, that becomes
    `Authorization: Bearer ""` — a 401 from the proxy with a config file that
    looks entirely correct.
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _header_map(raw: Any) -> dict[str, str] | None:
    """`llm: headers:` — whatever else the proxy in front wants.

    LiteLLM routes on `x-litellm-tags`, some deployments want a tenant or an
    organisation id, and none of that is worth a config key each. Values go
    through `_scalar` for the same env-var reason the key does.
    """
    if not isinstance(raw, dict):
        return None
    out = {str(key): _scalar(value) for key, value in raw.items() if _scalar(value)}
    return out or None


def _detect_backend(url: str) -> str:
    """Which wire a url is asking for, when nobody said.

    A url with `/v1` in it is unambiguous — Ollama's native API has no such
    path, and every OpenAI-compatible server serves exactly that. Anything else
    defaults to `ollama`, so an existing install that never heard of this
    setting keeps the behaviour it had.

    Matched anywhere in the path and case-insensitively, to agree with
    `normalise_base_url`: that function accepted `/v1` anywhere while this one
    looked only at the final segment, so `http://litellm:4000/v1/chat/completions`
    was normalised as OpenAI and then dispatched to Ollama's `/api/chat`.

    A bare `http://host:4000` still reads as Ollama. That is deliberate and is
    the only safe default — every existing install writes its Ollama url that
    way — so a LiteLLM on a bare port needs either `/v1` on the url or an
    explicit `backend: openai`. Both are in `docs/openai-compat.md`.
    """
    text = str(url or "").rstrip("/").lower()
    tail = text.rsplit("/", 1)[-1]
    if tail in ("v1", "openai") or "/v1/" in f"{text}/":
        return "openai"
    return "ollama"


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
        api_key = _scalar(options.get("api_key"))
        _LOGGER.info(
            "LLM backend: OpenAI-compatible at %s (api key %s)",
            url,
            "set" if api_key else "not set",
        )
        return OpenAICompatClient(
            url=url,
            model=model,
            timeout=timeout,
            client=client,
            api_key=api_key or None,
            headers=_header_map(options.get("headers")),
            label=_scalar(options.get("backend_name")) or "the model server",
        )

    _LOGGER.info("LLM backend: Ollama at %s", url)
    return OllamaClient(
        url=url,
        model=model,
        timeout=timeout,
        client=client,
        keep_alive=options.get("keep_alive"),
    )


#: Public aliases. `integrations/vision` builds its own model client and had no
#: way to honour `backend:`/`api_key:` without duplicating the logic above; it
#: infers the wire from a url with the same rule, so the two blocks cannot read
#: one `LLM_URL` two ways.
build_model_client = _build_model_client
detect_backend = _detect_backend



# ---------------------------------------------------------------------------
# "100% local", checked rather than trusted
# ---------------------------------------------------------------------------

#: Networks a model server may live on: this machine, the LAN, a link-local
#: address, or the CGNAT range Tailscale and similar overlays use. Everything
#: else is somebody else's computer.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def is_local_url(url: str) -> tuple[bool, str]:
    """Does this URL point at a machine the operator plausibly owns?

    Returns `(ok, why not)`. A name that does not resolve is NOT treated as a
    failure: a compose service that has not started yet is the ordinary case on
    a first boot, and refusing to start because DNS was not ready would be a
    worse bug than the one this prevents.
    """
    try:
        host = urlparse(url).hostname or ""
    except ValueError as err:
        return False, f"{url!r} is not a URL: {err}"
    if not host:
        return False, f"{url!r} names no host"
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True, ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        # Unresolvable today, and possibly a container that is still starting.
        return True, ""
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover
            continue
        if not any(parsed in network for network in _PRIVATE_NETWORKS):
            return False, (
                f"{host} resolves to {address}, which is a public address. "
                "Jarvis runs its model locally by design; point `llm: url:` at a "
                "server you run, or set `llm: local_only: false` if you really "
                "mean to send every conversation off this network."
            )
    return True, ""


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = _as_dict(config)

    url = str(options.get("url") or options.get("host") or DEFAULT_URL)
    model = str(options.get("model") or DEFAULT_MODEL)

    # The promise the whole project rests on, checked rather than trusted.
    # Nothing else in the code stops `url:` naming a cloud endpoint, and a
    # promise nothing verifies is a hope.
    if options.get("local_only", True):
        local, why = is_local_url(url)
        if not local:
            _LOGGER.error("llm: refusing to use a non-local model server. %s", why)
            return False
    timeout = float(options.get("timeout") or DEFAULT_TIMEOUT)
    # The absolute bound on ONE call, as distinct from `timeout`, which is
    # httpx's per-read one and is reset by every keepalive byte. See
    # `OllamaClient.call_timeout`: without this a stalled server hangs a turn
    # for ever, which is what it did.
    call_timeout = float(options.get("call_timeout") or 0.0)
    max_tool_rounds = int(options.get("max_tool_rounds") or DEFAULT_MAX_TOOL_ROUNDS)
    address = str(options.get("address") or "Sir").strip() or "Sir"
    # Kept on the instance rather than read where it is used: the pool is built
    # lazily by whoever fans out first, and by then the config is gone.
    jarvis.data[DATA_MAX_CONCURRENT] = _bounded_concurrency(options.get("max_concurrent"))
    approval_ttl = float(options.get("approval_ttl") or DEFAULT_APPROVAL_TTL)
    # Its own clock, never derived from `approval_ttl`: the two are different
    # waits for different reasons (see `DEFAULT_QUESTION_TTL`), and an
    # operator shortening approvals to a minute should not have every
    # question lapse in a minute too.
    question_ttl = float(options.get("question_ttl") or DEFAULT_QUESTION_TTL)

    client = create_http_client(jarvis, timeout)
    ollama = _build_model_client(options, url, model, timeout, client)
    if call_timeout:
        # Never below the per-read timeout: raising `timeout` must not quietly
        # lower the real bound.
        ollama.call_timeout = max(call_timeout, timeout)

    exposure = Exposure.from_config(options.get("expose"))
    registry = ToolRegistry(
        jarvis, exposure=exposure, approval_ttl=approval_ttl, question_ttl=question_ttl
    )
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

    # The durable half. `history: false` turns it off for anyone who would
    # rather nothing was written down — the console's chat mode then has no
    # past conversations to list, and everything else works exactly as before.
    archive = ConversationArchive(
        store=(
            Store(jarvis.config_dir, HISTORY_STORE_KEY)
            if conversation_options.get("history", True)
            else None
        ),
        max_conversations=int(
            conversation_options.get("history_limit") or DEFAULT_MAX_CONVERSATIONS
        ),
        scheduler=jarvis.async_create_task,
    )
    restored = await archive.async_load()
    if restored:
        _LOGGER.info("Restored %d archived conversation(s)", restored)

    agent = ConversationAgent(
        jarvis,
        ollama,
        registry,
        model=model,
        persona=options.get("persona"),
        persona_file=options.get("persona_file"),
        max_tool_rounds=max_tool_rounds,
        address=address,
        constrained_retry=bool(options.get("constrained_tool_calls", True)),
        memory=memory,
        options=_as_dict(options.get("options")),
        language=str(options.get("language") or "en"),
        # Unset leaves the model's own default alone, which is what every
        # install had before this key existed.
        think=_tristate(options.get("think")),
        archive=archive,
        # With reasoning off, the model may still raise it for one turn it
        # judges needs working out. Off makes the latency predictable at the
        # cost of the hard turns; see `THINK_TOOL_NAME` in llm/agent.py.
        allow_think_escalation=bool(options.get("allow_think_escalation", True)),
    )

    jarvis.data[DOMAIN] = agent
    jarvis.data[DATA_TOOLS] = registry
    jarvis.data[DATA_HISTORY] = archive

    _register_services(jarvis, agent, registry)
    _bridge_questions_to_the_phone(jarvis, registry)

    async def _shutdown() -> None:
        # Flushed before the client goes: `schedule_save` is fire-and-forget,
        # so a turn that finished in the last moments of the process has a
        # queued save that the loop will never get round to running.
        if archive.dirty:
            await archive.async_save()
        await ollama.aclose()
        if jarvis.data.get(DATA_CLIENT) is client and not client.is_closed:
            await client.aclose()

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "LLM agent ready: model=%s url=%s tools=%d", model, url, len(registry.tools)
    )
    # A reachability probe, in the background so a model server that is still
    # starting does not hold up boot. It does two jobs: it fills
    # `client.known_models`, which the settings page reads to offer a model
    # dropdown and which was empty on every install because nothing ever called
    # `list_models`; and it puts one clear line in the log when the model
    # server cannot be reached, which is the single most common way this
    # install is misconfigured and previously produced no output at all until
    # somebody spoke to it.
    jarvis.async_create_task(_probe_model_server(ollama, url, agent))
    return True


async def _probe_model_server(client: Any, url: str, agent: Any = None) -> None:
    """Warm the model list, and say plainly what is wrong before anybody speaks.

    Two failures, both of which used to be silent until the first turn:

    * the server is not there at all;
    * the server is there and does not have the model this install is set to.
      That is not hypothetical — putting the LiteLLM gateway in front of the
      model server (M40) renamed every model, and a `llm.model` an operator had
      chosen in the console went on pointing at the OLD name. Each turn then
      came back as a 400 from the proxy with the model name in it, which is a
      log line that means nothing to anybody who did not build the gateway.
    """
    try:
        models = await client.list_models()
    except Exception as err:
        _LOGGER.warning(
            "Could not reach the model server at %s: %s. Jarvis will start, but "
            "every turn will fail until it is reachable.",
            url,
            err,
        )
        return
    _LOGGER.info("Model server at %s is serving %d model(s)", url, len(models))

    # Only when the server actually answered with a list: an endpoint that
    # serves no `/models` at all is not evidence that the model is missing.
    wanted = str(getattr(agent, "model", "") or getattr(client, "model", "") or "")
    if wanted and models and wanted not in models:
        _LOGGER.error(
            "The model this install is set to (%r) is not one %s serves. It has: %s. "
            "Every turn will fail until this is changed — in the console under "
            "Settings, or by clearing the stored `llm.model` override so the "
            "LLM_MODEL environment variable is used again.",
            wanted,
            url,
            ", ".join(sorted(models)[:12]) or "nothing",
        )


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
                # The single voice (M66): a question raised by a spoken turn is
                # said once, by the reply, on the surface the user spoke to.
                # The phone gets it as a card to tap and does not read it out
                # again — it used to, and the operator heard the question
                # twice. A typed turn's question is still spoken by the phone,
                # because nothing else will say it.
                "spoken": bool(data.get("spoken")),
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
            # Both halves, or "delete" would remove a conversation from the
            # model's memory and leave it sitting in the console's history list
            # — which is the opposite of what anybody pressing delete means.
            live = agent.memory.remove(str(conversation_id))
            archived = agent.archive.remove(str(conversation_id))
            return {"cleared": live or archived}
        agent.memory.clear()
        agent.archive.clear()
        return {"cleared": True}

    jarvis.services.register(
        DOMAIN,
        "clear_conversation",
        handle_clear,
        description="Forget one conversation, or all of them.",
        fields={"conversation_id": {"description": "Leave empty to clear everything."}},
        supports_response=True,
    )

    async def handle_list_conversations(call: ServiceCall) -> dict[str, Any]:
        return {"conversations": agent.archive.listing()}

    jarvis.services.register(
        DOMAIN,
        "list_conversations",
        handle_list_conversations,
        description="Past conversations, most recent first (summaries only).",
        supports_response=True,
    )

    async def handle_get_conversation(call: ServiceCall) -> dict[str, Any]:
        conversation = agent.archive.get(str(call.get("conversation_id") or ""))
        return {"conversation": conversation.as_dict() if conversation else None}

    jarvis.services.register(
        DOMAIN,
        "get_conversation",
        handle_get_conversation,
        description="One past conversation in full, with its tool calls.",
        fields={
            "conversation_id": {"description": "Which conversation.", "required": True}
        },
        supports_response=True,
    )

    async def handle_rename_conversation(call: ServiceCall) -> dict[str, Any]:
        renamed = agent.archive.rename(
            str(call.get("conversation_id") or ""), str(call.get("title") or "")
        )
        return {"renamed": renamed}

    jarvis.services.register(
        DOMAIN,
        "rename_conversation",
        handle_rename_conversation,
        description="Give a past conversation a name of your own.",
        fields={
            "conversation_id": {"description": "Which conversation.", "required": True},
            "title": {"description": "The new name.", "required": True},
        },
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
