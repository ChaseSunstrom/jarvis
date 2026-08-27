"""What the model server actually serves, resolved as far as the local services allow.

The settings page used to offer "a model" from `client.known_models`, which on
the deployed stack is the gateway's list: `house` and `house-fast`. Those are
LiteLLM aliases — names *Jarvis* uses — and not what the operator means by
"the model". The models are what llama-swap serves behind the gateway:
`qwen3.8-27b`, `qwen3.6-35b`, and beside them the embedder and the reranker
that run in their own containers. This module walks that chain and answers
with the real ids, what each one is (as far as any server says), which one is
loaded right now, and which Jarvis job uses it.

The chain, on the shipped stack::

    LLM_URL (LiteLLM) ──/v1/models──▶ house, house-fast          (aliases)
                     ──/model/info─▶ house → openai/qwen3.8-27b @ api_base
                                                     │
    api_base (llama-swap) ──/v1/models──▶ ids + status.value (loaded/unloaded)
                          ──/running────▶ state, cmd, proxy, name, description
                          ──/upstream/<id>/v1/models ─▶ the backend's own record
                                          (vLLM: root, max_model_len;
                                           llama.cpp: meta.n_params, size)
    EMBEDDINGS_URL (TEI) ──/info──▶ model_id, model_type.embedding
    RERANK_URL (TEI)     ──/info──▶ model_id, model_type.reranker

Every hop is optional and every failure is recorded per server rather than
raised: a gateway with no `/model/info` is a plain OpenAI server, a server with
no `/running` simply cannot say what is loaded, and an embedder that is down is
still listed from the config with `loaded: null`. The page must render on a
half-up stack, because a half-up stack is when somebody opens it.

Two rules the tests pin:

* **Nothing here loads a model.** llama-swap's `/upstream/<id>/…` starts the
  model it names, so it is asked only about models `/running` already reports
  ready. A settings page that swapped the 35-B model in every time it was
  opened would evict the voice path's KV cache to draw a row.
* **Nothing is invented.** A parameter count, a quantisation or a family is
  taken from the server when the server says (llama.cpp's `meta`, Ollama's
  `details`, vLLM's `root`), and otherwise *derived from the id* and marked
  `described_by: "id"` so the row can say "as named by the server". A server
  that answers with bare ids gets bare ids, not a guess dressed as a fact.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "JOBS",
    "ROLES",
    "ModelInfo",
    "ServerReport",
    "async_describe",
    "describe_model_id",
    "family_of",
]

#: What a model is for. `unknown` is a real answer: a server that lists a bare
#: id has not said, and the row says so rather than guessing.
ROLE_CHAT = "chat"
ROLE_FAST = "fast"
ROLE_VISION = "vision"
ROLE_EMBEDDINGS = "embeddings"
ROLE_RERANK = "rerank"
ROLE_UNKNOWN = "unknown"
ROLES = (ROLE_CHAT, ROLE_FAST, ROLE_VISION, ROLE_EMBEDDINGS, ROLE_RERANK, ROLE_UNKNOWN)

#: The Jarvis jobs a model can be in use for. Plain words: they are printed.
JOB_CONVERSATION = "conversation"
JOB_FAST_PATH = "fast path"
JOB_RESEARCH = "research"
JOB_CODING = "coding"
JOB_EMBEDDINGS = "embeddings"
JOB_RERANK = "rerank"
JOB_VISION = "vision"
JOBS = (
    JOB_CONVERSATION,
    JOB_FAST_PATH,
    JOB_RESEARCH,
    JOB_CODING,
    JOB_EMBEDDINGS,
    JOB_RERANK,
    JOB_VISION,
)

#: The settings each role's choice writes. The console reads this rather than
#: carrying its own copy, so a renamed key breaks one place.
ROLE_SETTINGS = {
    ROLE_CHAT: "llm.model",
    ROLE_FAST: "llm.fast_model",
    ROLE_VISION: "vision.model",
}

#: The gateway's alias for the fast slot (`gateway/config.yaml`). Read by
#: name because that file is the gateway's, not Jarvis's; a deployment that
#: renames it loses the "configured as fast" annotation and nothing else.
GATEWAY_FAST_ALIAS = "house-fast"

#: How long one probe may take. Short: this runs when somebody opens Settings,
#: and five servers at thirty seconds each is a page that never loads. A
#: server that cannot list its models in four seconds is reported as down.
PROBE_TIMEOUT = 4.0

#: Providers LiteLLM routes to a server the operator runs. Anything else is a
#: cloud provider and is listed as an alias only — there is no local server to
#: ask what is behind it, and asking a cloud provider is not this project.
_LOCAL_PROVIDERS = ("openai", "hosted_vllm", "ollama", "ollama_chat", "text-completion-openai")


# ---------------------------------------------------------------------------
# reading an id
# ---------------------------------------------------------------------------
#: Proper casing for families whose names are not simply capitalised.
_FAMILY_NAMES = {
    "qwen": "Qwen",
    "llama": "Llama",
    "gemma": "Gemma",
    "mistral": "Mistral",
    "mixtral": "Mixtral",
    "phi": "Phi",
    "deepseek": "DeepSeek",
    "glm": "GLM",
    "bge": "BGE",
    "minilm": "MiniLM",
    "nomic": "Nomic",
    "e5": "E5",
    "gte": "GTE",
    "llava": "LLaVA",
    "moondream": "Moondream",
    "minicpm": "MiniCPM",
    "granite": "Granite",
    "smollm": "SmolLM",
    "olmo": "OLMo",
    "yi": "Yi",
    "internvl": "InternVL",
    "pixtral": "Pixtral",
    "mxbai": "MxBAI",
    "arctic": "Arctic",
    "jina": "Jina",
    "gpt": "GPT",
    "codestral": "Codestral",
    "starcoder": "StarCoder",
}

_VISION_TOKENS = frozenset({"vl", "vision", "llava", "moondream", "minicpm-v", "minicpmv", "pixtral", "internvl", "paligemma"})
_RERANK_TOKENS = frozenset({"rerank", "reranker", "cross-encoder", "ms-marco", "marco"})
_EMBED_TOKENS = frozenset({"embed", "embedding", "embeddings", "bge", "e5", "gte", "minilm", "nomic", "arctic-embed", "mxbai-embed", "sentence-transformers"})

_PARAMS_RE = re.compile(r"^(\d+(?:\.\d+)?)b$")
_QUANT_RE = re.compile(
    r"^(q\d(?:_[a-z0-9]+)*|iq\d[a-z0-9_]*|f16|f32|bf16|fp16|fp8|int4|int8|awq|gptq|exl2|nf4|mxfp4|q\d_k)$"
)
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)?$")
_HEAD_RE = re.compile(r"^([a-z]+)(\d+(?:\.\d+)?)?([a-z]*)$")


def _tokens(model_id: str) -> list[str]:
    """`Qwen3.8-27B-AWQ-INT4` -> ['qwen3.8', '27b', 'awq', 'int4'], from the last path segment."""
    tail = str(model_id or "").strip().rsplit("/", 1)[-1]
    tail = re.sub(r"\.(gguf|safetensors|bin)$", "", tail, flags=re.I)
    # Dots stay inside a token (`qwen3.8`, `0.6b`), and so do underscores
    # (`q4_k_m`, `iq4_xs`): both are how a version or a quant is spelled.
    return [t for t in re.split(r"[-:@\s]+", tail.lower()) if t]


def family_of(model_id: str) -> str:
    """The family a name implies: `qwen3.8-27b` -> `Qwen 3.8`, `gemma-3-27b-it` -> `Gemma 3`.

    A reading of the *name*, and nothing more: it cannot tell a fine-tune from
    its base, and it does not try. What it is for is a row a person can scan
    without knowing that `qwen3.8` is a version of Qwen.
    """
    tokens = _tokens(model_id)
    if not tokens:
        return ""
    head = _HEAD_RE.match(tokens[0])
    if not head:
        return ""
    name, version, tail = head.group(1), head.group(2) or "", head.group(3) or ""
    if name not in _FAMILY_NAMES:
        # `ms-marco-MiniLM-L-6-v2`, `all-MiniLM-L6-v2`: the family is not the
        # first word. A known name anywhere in the id beats a capitalised
        # prefix that means nothing.
        for token in tokens[1:]:
            inner = _HEAD_RE.match(token)
            if inner and inner.group(1) in _FAMILY_NAMES:
                name, version, tail = inner.group(1), inner.group(2) or "", inner.group(3) or ""
                break
    pretty = _FAMILY_NAMES.get(name, name.capitalize())
    # `gemma-3-27b`: the version is the next token when the head had none.
    if not version and len(tokens) > 1 and _VERSION_RE.match(tokens[1]):
        version = tokens[1]
    # `Qwen2.5-VL-7B`: the vision suffix as its own token.
    if not tail and len(tokens) > 1 and tokens[1] == "vl":
        tail = "vl"
    parts = [pretty]
    if version:
        parts.append(version)
    if tail:
        parts.append(tail.upper())
    return " ".join(parts)


def describe_model_id(model_id: str) -> dict[str, str]:
    """Family, parameters, quant and a role hint, read off an id.

    Returns empty strings for anything the id does not carry — a `bge-small`
    has no parameter count in its name and gets none. The role hint is
    `unknown` unless the name says vision, embeddings or rerank; a plain LLM
    is not "chat" by name, it is chat by being served by a chat server, and
    the caller decides that.
    """
    tokens = _tokens(model_id)
    lowered = str(model_id or "").lower()
    parameters = ""
    quant_parts: list[str] = []
    for token in tokens:
        match = _PARAMS_RE.match(token)
        if match and not parameters:
            parameters = f"{match.group(1)}B"
            continue
        if _QUANT_RE.match(token):
            quant_parts.append(token.upper())
    role = ROLE_UNKNOWN
    if any(t in _RERANK_TOKENS for t in tokens) or "cross-encoder" in lowered or "ms-marco" in lowered:
        role = ROLE_RERANK
    elif any(t in _EMBED_TOKENS for t in tokens) or "embed" in lowered:
        role = ROLE_EMBEDDINGS
    elif any(t in _VISION_TOKENS for t in tokens) or any(t.endswith("vl") for t in tokens[:1]) or "minicpm-v" in lowered:
        role = ROLE_VISION
    return {
        "family": family_of(model_id),
        "parameters": parameters,
        "quant": "-".join(quant_parts),
        "role": role,
    }


def _human_params(count: Any) -> str:
    """`27070000000` -> `27B`; `600000000` -> `0.6B`."""
    try:
        number = float(count)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    billions = number / 1e9
    if billions >= 10:
        return f"{billions:.0f}B"
    if billions >= 1:
        text = f"{billions:.1f}".rstrip("0").rstrip(".")
        return f"{text}B"
    return f"{billions:.1f}B"


# ---------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    """One model as a server serves it, plus what Jarvis does with it."""

    #: As served — the string a request names. Never an alias.
    id: str
    #: For a person: the server's own `name` if it gave one, else family and size.
    name: str = ""
    family: str = ""
    parameters: str = ""
    quant: str = ""
    role: str = ROLE_UNKNOWN
    #: True/False when the server says; None when it cannot (a bare OpenAI list).
    loaded: bool | None = None
    #: Names the configured `LLM_URL` server knows this model by, when it is
    #: behind a gateway. Empty when Jarvis talks to the server directly.
    aliases: list[str] = field(default_factory=list)
    in_use_for: list[str] = field(default_factory=list)
    #: The base URL that listed it, and what kind of server that was.
    server: str = ""
    kind: str = ""
    #: What to write into `llm.model` to use it for chat: an alias behind a
    #: gateway, the id otherwise, None when `LLM_URL` cannot reach it at all.
    choice: str | None = None
    #: Where family/parameters/quant came from: "server" or "id".
    described_by: str = "id"
    context: int | None = None
    size_bytes: int | None = None
    description: str = ""
    #: A configured name the server did not list. The M40 failure, visible.
    missing: bool = False
    #: One sentence the row shows, when there is something to say.
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ServerReport:
    """One server that was asked, and how it went."""

    url: str
    kind: str
    role: str = ""
    ok: bool = False
    error: str = ""
    models: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _base(url: str) -> str:
    """`http://h:4000/v1/` -> `http://h:4000`; a bare host is left as it is."""
    text = str(url or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1/models", "/v1/embeddings", "/chat/completions", "/rerank", "/v1", "/openai"):
        if text.lower().endswith(suffix):
            return text[: -len(suffix)].rstrip("/")
    return text


def _scalar(value: Any) -> str:
    """A config scalar with `!env_var NAME ""`'s literal quotes stripped (see the llm integration)."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _section(config: dict[str, Any] | None, name: str) -> dict[str, Any]:
    raw = (config or {}).get(name)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return raw if isinstance(raw, dict) else {}


class _Probe:
    """One HTTP client, one timeout, one place errors are turned into words."""

    def __init__(self, client: httpx.AsyncClient, timeout: float = PROBE_TIMEOUT) -> None:
        self.client = client
        self.timeout = timeout
        #: Every URL asked, in order. The tests read it to prove the resolver
        #: never touched an unloaded model's upstream.
        self.asked: list[str] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        """The JSON at `url`, or an `Exception` instance describing why not.

        Returned rather than raised so a caller can try three endpoints in a
        row and keep the reasons, and so a page never dies on one of them.
        """
        self.asked.append(url)
        try:
            response = await self.client.get(url, headers=headers or None, timeout=self.timeout)
        except httpx.HTTPError as exc:
            return exc
        if response.status_code >= 400:
            return httpx.HTTPStatusError(
                f"{response.status_code} for {url}: {response.text[:200]}",
                request=response.request,
                response=response,
            )
        try:
            return response.json()
        except ValueError as exc:
            return exc


def _why(err: Any) -> str:
    """One line for a report: the class and the message, no stack."""
    if isinstance(err, httpx.HTTPStatusError):
        return str(err).split("\n", 1)[0][:200]
    return f"{type(err).__name__}: {err}"[:200]


# ---------------------------------------------------------------------------
# the servers, one reader each
# ---------------------------------------------------------------------------
async def _read_gateway_aliases(
    probe: _Probe, base: str, headers: dict[str, str] | None
) -> dict[str, dict[str, str]] | None:
    """LiteLLM's `/model/info`: alias -> {model, api_base, provider, locality}.

    None when the server is not LiteLLM (a 404, or no `data`) — which is what
    makes the difference between "a gateway with these aliases" and "a server
    with these models" a fact rather than a config flag.
    """
    payload = await probe.get(f"{base}/model/info", headers)
    if isinstance(payload, Exception) or not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        alias = str(row.get("model_name") or "").strip()
        params = row.get("litellm_params") if isinstance(row.get("litellm_params"), dict) else {}
        info = row.get("model_info") if isinstance(row.get("model_info"), dict) else {}
        target = str(params.get("model") or "").strip()
        if not alias or not target:
            continue
        provider, _, upstream = target.partition("/")
        if not upstream:
            provider, upstream = "", target
        out[alias] = {
            "model": upstream,
            "provider": provider,
            "api_base": str(params.get("api_base") or "").strip(),
            "locality": str(info.get("locality") or "").strip(),
        }
    return out


def _openai_entry(entry: Any) -> ModelInfo | None:
    """One `/v1/models` row, on any of the dialects that share the shape."""
    if isinstance(entry, str):
        return ModelInfo(id=entry)
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    info = ModelInfo(id=str(entry["id"]))
    owner = str(entry.get("owned_by") or "").lower()
    # llama-swap: `status.value` is loaded/unloaded, `meta.llamaswap`.
    status = entry.get("status")
    if isinstance(status, dict) and status.get("value"):
        info.loaded = str(status["value"]).lower() in ("loaded", "ready", "running")
        info.kind = "llama-swap"
    elif "llama-swap" in owner or (isinstance(entry.get("meta"), dict) and "llamaswap" in entry["meta"]):
        info.kind = "llama-swap"
    # llama.cpp: `meta` carries the GGUF's own numbers.
    meta = entry.get("meta")
    if isinstance(meta, dict) and ("n_params" in meta or "n_ctx_train" in meta):
        info.kind = "llama.cpp"
        info.loaded = True
        info.parameters = _human_params(meta.get("n_params")) or info.parameters
        if meta.get("size"):
            info.size_bytes = int(meta["size"])
        if meta.get("n_ctx_train"):
            info.context = int(meta["n_ctx_train"])
        info.described_by = "server"
    # vLLM: `root` is the weights it loaded, `max_model_len` the context.
    if "vllm" in owner or entry.get("max_model_len"):
        info.kind = info.kind or "vllm"
        info.loaded = True if info.loaded is None else info.loaded
        if entry.get("max_model_len"):
            info.context = int(entry["max_model_len"])
        root = str(entry.get("root") or "")
        if root and root != info.id:
            info.description = root
    if isinstance(entry.get("name"), str) and entry["name"].strip():
        info.name = entry["name"].strip()
    if isinstance(entry.get("description"), str) and entry["description"].strip():
        info.description = entry["description"].strip()
    return info


def _from_command(cmd: str, info: ModelInfo) -> None:
    """What llama-swap's `cmd` line says about a model, when the backend will not.

    `--max-model-len 256000` (vLLM), `-c 32768` / `--ctx-size` (llama.cpp) and
    the weights' path or repo are all in there. Read only when the backend's
    own record did not already say — the command line is what was *asked*,
    the backend answers with what it *did*.
    """
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    for index, part in enumerate(parts):
        nxt = parts[index + 1] if index + 1 < len(parts) else ""
        if part in ("--max-model-len", "-c", "--ctx-size", "--ctx_size") and nxt.isdigit() and not info.context:
            info.context = int(nxt)
        if part in ("-m", "--model", "serve") and nxt and not nxt.startswith("-") and not info.description:
            # `vllm serve org/Repo-AWQ-INT4`, `llama-server -m /models/x.gguf`.
            if "/" in nxt or nxt.endswith(".gguf"):
                info.description = nxt


async def _read_llama_swap_running(probe: _Probe, base: str, models: dict[str, ModelInfo]) -> None:
    """`/running`: which models are up, and the command each was started with."""
    payload = await probe.get(f"{base}/running")
    if isinstance(payload, Exception) or not isinstance(payload, dict):
        return
    rows = payload.get("running")
    if not isinstance(rows, list):
        return
    ready: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("model"):
            continue
        model_id = str(row["model"])
        state = str(row.get("state") or "").lower()
        info = models.get(model_id)
        if info is None:
            continue
        info.kind = info.kind or "llama-swap"
        info.loaded = state in ("ready", "loaded", "running")
        if info.loaded:
            ready.add(model_id)
        if isinstance(row.get("name"), str) and row["name"].strip():
            info.name = row["name"].strip()
        if isinstance(row.get("description"), str) and row["description"].strip():
            info.description = row["description"].strip()
        if isinstance(row.get("cmd"), str):
            _from_command(row["cmd"], info)
    # The backend's own record, for the models that are UP. Never for the
    # others: `/upstream/<id>/…` is how llama-swap starts a model.
    for model_id in sorted(ready):
        payload = await probe.get(f"{base}/upstream/{model_id}/v1/models")
        if isinstance(payload, Exception) or not isinstance(payload, dict):
            continue
        for entry in payload.get("data") or []:
            own = _openai_entry(entry)
            if own is None:
                continue
            info = models[model_id]
            if own.context and not info.context:
                info.context = own.context
            if own.parameters and info.described_by != "server":
                info.parameters, info.described_by = own.parameters, "server"
            if own.size_bytes:
                info.size_bytes = own.size_bytes
            if own.description and not info.description:
                info.description = own.description
            if own.kind in ("vllm", "llama.cpp"):
                info.kind = f"llama-swap → {own.kind}"


async def _read_openai_server(
    probe: _Probe, base: str, headers: dict[str, str] | None, report: ServerReport
) -> dict[str, ModelInfo]:
    """`/v1/models` on any OpenAI-compatible server, then whatever else it has."""
    payload = await probe.get(f"{base}/v1/models", headers)
    if isinstance(payload, Exception):
        report.error = _why(payload)
        return {}
    entries = payload.get("data") if isinstance(payload, dict) else payload
    models: dict[str, ModelInfo] = {}
    for entry in entries or []:
        info = _openai_entry(entry)
        if info is not None:
            info.server = base
            models[info.id] = info
    report.ok = True
    if any(info.kind == "llama-swap" for info in models.values()):
        report.kind = "llama-swap"
        await _read_llama_swap_running(probe, base, models)
    elif any(info.kind == "vllm" for info in models.values()):
        report.kind = "vllm"
    elif any(info.kind == "llama.cpp" for info in models.values()):
        report.kind = "llama.cpp"
    for info in models.values():
        info.kind = info.kind or report.kind or "openai"
    report.models = len(models)
    return models


async def _read_ollama(probe: _Probe, base: str, report: ServerReport) -> dict[str, ModelInfo]:
    """Ollama's own wire: `/api/tags` describes every pull, `/api/ps` says what is loaded."""
    payload = await probe.get(f"{base}/api/tags")
    if isinstance(payload, Exception):
        report.error = _why(payload)
        return {}
    models: dict[str, ModelInfo] = {}
    for entry in (payload.get("models") if isinstance(payload, dict) else None) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "").strip()
        if not name:
            continue
        info = ModelInfo(id=name, server=base, kind="ollama", loaded=False)
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        if details.get("family"):
            info.family = str(details["family"]).strip()
            info.described_by = "server"
        if details.get("parameter_size"):
            info.parameters = str(details["parameter_size"]).strip().upper()
            info.described_by = "server"
        if details.get("quantization_level"):
            info.quant = str(details["quantization_level"]).strip().upper()
            info.described_by = "server"
        if entry.get("size"):
            info.size_bytes = int(entry["size"])
        models[name] = info
    report.ok = True
    report.kind = "ollama"
    running = await probe.get(f"{base}/api/ps")
    if isinstance(running, dict):
        for entry in running.get("models") or []:
            if isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("model") or "")
                if name in models:
                    models[name].loaded = True
    report.models = len(models)
    return models


async def _read_tei(
    probe: _Probe, url: str, configured_model: str, role: str, report: ServerReport
) -> ModelInfo:
    """Text Embeddings Inference's `/info`, or the configured name with nothing known.

    One model per TEI instance, so the answer is one row either way; what the
    server adds is that it is *there*, what it is actually serving (which may
    not be what the config says, after an `.env` edit), and its input length.
    """
    base = _base(url)
    info = ModelInfo(id=configured_model, server=base, kind="tei", role=role)
    payload = await probe.get(f"{base}/info")
    if isinstance(payload, dict) and payload.get("model_id"):
        served = str(payload.get("served_model_name") or payload["model_id"])
        info.id = served
        info.loaded = True
        report.ok = True
        report.models = 1
        kinds = payload.get("model_type") if isinstance(payload.get("model_type"), dict) else {}
        if "reranker" in kinds:
            info.role = ROLE_RERANK
        elif "embedding" in kinds:
            info.role = ROLE_EMBEDDINGS
        if payload.get("max_input_length"):
            info.context = int(payload["max_input_length"])
        if payload.get("model_dtype"):
            info.quant = str(payload["model_dtype"]).upper()
            info.described_by = "server"
        if served != configured_model and configured_model:
            info.note = f"the config names {configured_model}; the server is serving this"
        return info
    # Not TEI, or not up. An OpenAI-style list is the other shape an embedder
    # serves (Infinity, vLLM, Ollama on /v1).
    listing = await probe.get(f"{base}/v1/models")
    if isinstance(listing, dict) and isinstance(listing.get("data"), list):
        report.ok = True
        ids = [str(e.get("id")) for e in listing["data"] if isinstance(e, dict) and e.get("id")]
        report.models = len(ids)
        if configured_model in ids or not ids:
            info.loaded = None
        else:
            info.missing = True
            info.note = f"the server lists {', '.join(ids[:4])}, not this"
        return info
    report.error = _why(payload if isinstance(payload, Exception) else listing)
    info.loaded = None
    info.note = "the service did not answer; this is what the config names"
    return info


# ---------------------------------------------------------------------------
# putting it together
# ---------------------------------------------------------------------------
def _finish(info: ModelInfo) -> None:
    """Fill family/size/quant from the id where the server said nothing, and a name."""
    derived = describe_model_id(info.description or info.id) if info.description else describe_model_id(info.id)
    own = describe_model_id(info.id)
    if not info.family:
        info.family = own["family"] or derived["family"]
    if not info.parameters:
        info.parameters = own["parameters"] or derived["parameters"]
    if not info.quant:
        info.quant = own["quant"] or derived["quant"]
    if info.role == ROLE_UNKNOWN:
        info.role = own["role"] if own["role"] != ROLE_UNKNOWN else derived["role"]
    if not info.name:
        info.name = " ".join(p for p in (info.family, info.parameters) if p) or info.id


def _resolve(name: str, aliases: dict[str, dict[str, str]]) -> str:
    """A setting's value -> the served id: through the gateway's alias, or as it is."""
    target = aliases.get(name)
    return target["model"] if target else name


async def async_describe(
    jarvis: "Jarvis",
    client: httpx.AsyncClient | None = None,
    timeout: float = PROBE_TIMEOUT,
) -> dict[str, Any]:
    """The whole answer: every model, every server, and what each role is set to.

    `client` is the shared model-server client (`jarvis.data["llm_client"]`)
    when the llm integration built one — same pool, same injected transport in
    tests — and a fresh one otherwise. Never raises: a stack with nothing up
    answers with empty lists and a reason per server.
    """
    config = jarvis.config if isinstance(getattr(jarvis, "config", None), dict) else {}
    llm = _section(config, "llm")
    memory = _section(config, "memory")
    research = _section(config, "research")
    code = _section(config, "code")
    vision = _section(config, "vision")

    own_client = client is None
    http = client or httpx.AsyncClient(follow_redirects=True)
    probe = _Probe(http, timeout=timeout)
    servers: list[ServerReport] = []
    models: dict[str, ModelInfo] = {}
    aliases: dict[str, dict[str, str]] = {}
    gateway: dict[str, Any] | None = None
    try:
        # --- the chat server, which may be a gateway in front of the real one
        url = _scalar(llm.get("url") or llm.get("host")) or "http://127.0.0.1:11434"
        api_key = _scalar(llm.get("api_key"))
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        backend = _scalar(llm.get("backend")).lower()
        base = _base(url)
        wants_openai = backend == "openai" or (not backend and ("/v1" in url.lower() or url.rstrip("/").lower().endswith("/openai")))
        front = ServerReport(url=base, kind="openai" if wants_openai else "ollama", role="chat")
        servers.append(front)
        if wants_openai:
            found = await _read_gateway_aliases(probe, base, headers)
            if found is not None:
                front.kind = "litellm"
                aliases = found
                gateway = {
                    "url": base,
                    "aliases": {alias: row["model"] for alias, row in aliases.items()},
                }
                # The gateway's own list is what `llm.model` may name.
                front.ok = True
                front.models = len(aliases)
                # Behind it: every distinct local upstream, asked once.
                seen: set[str] = set()
                for alias, row in aliases.items():
                    upstream_base = _base(row["api_base"])
                    local = row["provider"] in _LOCAL_PROVIDERS or not row["provider"]
                    if not upstream_base or not local or upstream_base in seen:
                        continue
                    seen.add(upstream_base)
                    report = ServerReport(url=upstream_base, kind="openai", role="chat")
                    servers.append(report)
                    # The upstream's own key, if the gateway forwards one, is
                    # in litellm's config and not here; llama-swap has none.
                    models.update(await _read_openai_server(probe, upstream_base, None, report))
                # A cloud alias has no local server behind it and is listed as
                # exactly that — so the row can say "not local" rather than
                # vanishing from a page whose whole point is what is served.
                for alias, row in aliases.items():
                    if row["provider"] in _LOCAL_PROVIDERS or not row["provider"]:
                        continue
                    info = ModelInfo(
                        id=row["model"], server=base, kind=f"litellm → {row['provider']}",
                        loaded=None, note="a cloud provider behind the gateway; not on this network",
                    )
                    models.setdefault(info.id, info)
            else:
                models.update(await _read_openai_server(probe, base, headers, front))
        else:
            models.update(await _read_ollama(probe, base, front))

        # --- aliases onto the models they name, and the chat choice ---------
        for alias, row in aliases.items():
            info = models.get(row["model"])
            if info is not None:
                info.aliases.append(alias)
        for info in models.values():
            if info.aliases:
                # The first alias is the plain one (`house` before `house-fast`).
                info.choice = sorted(info.aliases, key=len)[0]
            elif not gateway and info.server == base:
                info.choice = info.id

        # --- the retrieval services, one row each ---------------------------
        if memory.get("embeddings", True) is not False and _scalar(memory.get("embedding_url")):
            report = ServerReport(url=_base(_scalar(memory.get("embedding_url"))), kind="tei", role="embeddings")
            servers.append(report)
            info = await _read_tei(probe, report.url, _scalar(memory.get("embedding_model")), ROLE_EMBEDDINGS, report)
            info.in_use_for.append(JOB_EMBEDDINGS)
            models.setdefault(info.id, info)
        rerank_urls: list[tuple[str, str]] = []
        if _scalar(research.get("rerank_url")):
            rerank_urls.append((_scalar(research["rerank_url"]), _scalar(research.get("rerank_model"))))
        if _scalar(memory.get("rerank_url")):
            rerank_urls.append((_scalar(memory["rerank_url"]), _scalar(memory.get("rerank_model"))))
        for rerank_url, rerank_model in rerank_urls:
            base_url = _base(rerank_url)
            if any(s.url == base_url and s.role == "rerank" for s in servers):
                continue
            report = ServerReport(url=base_url, kind="tei", role="rerank")
            servers.append(report)
            info = await _read_tei(probe, rerank_url, rerank_model, ROLE_RERANK, report)
            info.in_use_for.append(JOB_RERANK)
            models.setdefault(info.id, info)

        # --- what Jarvis is set to, resolved through the gateway ------------
        chat_value = _scalar(llm.get("model"))
        chat_id = _resolve(chat_value, aliases) if chat_value else ""
        fast_value = _scalar(llm.get("fast_model"))
        fast_source = ""
        if fast_value:
            fast_id, fast_source = _resolve(fast_value, aliases), "setting"
        elif GATEWAY_FAST_ALIAS in aliases and aliases[GATEWAY_FAST_ALIAS]["model"] != chat_id:
            fast_id, fast_source = aliases[GATEWAY_FAST_ALIAS]["model"], "gateway"
        else:
            fast_id = ""
        research_value = _scalar(research.get("model")) if research else ""
        code_value = _scalar(code.get("model")) if code else ""
        vision_value = _scalar(vision.get("model")) if vision else ""

        def _use(model_id: str, job: str, missing_note: str) -> None:
            if not model_id:
                return
            info = models.get(model_id)
            if info is None:
                # Configured, and not served. Listed so the page can say so:
                # after the gateway renamed everything (M40) this was a 400 per
                # turn and an empty dropdown.
                info = ModelInfo(id=model_id, missing=True, note=missing_note, loaded=None)
                models[model_id] = info
            if job not in info.in_use_for:
                info.in_use_for.append(job)

        _use(chat_id, JOB_CONVERSATION, f"`llm.model` names {chat_value!r}, which no server lists")
        if research:
            _use(_resolve(research_value, aliases) if research_value else chat_id, JOB_RESEARCH, "named by `research: model:`, which no server lists")
        if code:
            _use(_resolve(code_value, aliases) if code_value else chat_id, JOB_CODING, "named by `code: model:`, which no server lists")
        if vision:
            _use(vision_value, JOB_VISION, "named by `vision: model:`, which no server lists")
        if fast_source == "setting":
            # M60 routes a spoken turn to `llm.fast_model` when it is set; the
            # gateway's own fast alias, unchosen, is still idle and says so.
            _use(fast_id, JOB_FAST_PATH, "named by `llm.fast_model`, which no server lists")

        # --- roles --------------------------------------------------------------
        for info in models.values():
            if info.role != ROLE_UNKNOWN:
                continue
            hinted = describe_model_id(info.description or info.id)["role"]
            if hinted == ROLE_UNKNOWN:
                hinted = describe_model_id(info.id)["role"]
            if hinted != ROLE_UNKNOWN:
                info.role = hinted
            elif info.id == fast_id and info.id != chat_id:
                info.role = ROLE_FAST
            elif info.kind and info.kind != "tei" and not info.missing:
                info.role = ROLE_CHAT
        if fast_id and fast_id in models and JOB_FAST_PATH not in models[fast_id].in_use_for:
            # A fast alias the gateway offers but nobody chose: idle, and said
            # so. (A chosen one is on the voice path — M60 — and lands above.)
            models[fast_id].note = models[fast_id].note or (
                f"configured as fast ({GATEWAY_FAST_ALIAS}); idle — choose it as the fast model to route spoken turns to it"
                if fast_source == "gateway"
                else "chosen as the fast model; idle — nothing is routed to it yet"
            )
        for info in models.values():
            _finish(info)
            if info.described_by == "id" and (info.family or info.parameters or info.quant):
                info.note = info.note or "as named by the server"

        ordered = sorted(
            models.values(),
            key=lambda m: (
                m.missing,
                ROLES.index(m.role) if m.role in ROLES else len(ROLES),
                not m.loaded,
                m.id.lower(),
            ),
        )
        return {
            "models": [m.as_dict() for m in ordered],
            "roles": {
                ROLE_CHAT: {"setting": ROLE_SETTINGS[ROLE_CHAT], "value": chat_value, "model": chat_id or None},
                ROLE_FAST: {
                    "setting": ROLE_SETTINGS[ROLE_FAST],
                    "value": fast_value,
                    "model": fast_id or None,
                    "source": fast_source or None,
                },
                ROLE_VISION: {
                    "setting": ROLE_SETTINGS[ROLE_VISION],
                    "value": vision_value,
                    "model": vision_value or None,
                    "configured": bool(vision),
                    # The three things a panel has to tell apart, because they
                    # have three different fixes: no vision block at all; a
                    # block whose model no server lists (load one under that
                    # alias, or choose a served one); a served model and no
                    # camera to point it at. The operator read "cameras are
                    # not configured" when the truth was the second (26 Aug).
                    "served": any(m.id == vision_value and not m.missing for m in ordered) if vision_value else False,
                    "served_vision": [m.id for m in ordered if m.role == ROLE_VISION and not m.missing],
                    "cameras": len(vision.get("cameras") or []) if isinstance(vision, dict) else 0,
                },
            },
            "servers": [s.as_dict() for s in servers],
            "gateway": gateway,
            "fast_available": bool(fast_id) or GATEWAY_FAST_ALIAS in aliases,
            "asked": list(probe.asked),
        }
    finally:
        if own_client:
            await http.aclose()
