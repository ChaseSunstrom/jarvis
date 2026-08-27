# Pointing Jarvis at an OpenAI-compatible server

**llama-swap** is the endpoint this house runs: it presents one
OpenAI-compatible `/v1` and swaps the underlying llama.cpp process per model, so
`LLM_MODEL`, `PLANNER_MODEL` and `CODER_MODEL` can name three different models
without three servers. Nothing here is specific to it — it is one more
OpenAI-compatible URL — but it is the one the defaults are written for, and
`python3 scripts/check-model-server.py <url>/v1` reports which models it offers.


jarvis-core speaks two wires. Ollama's own `/api/chat` is the default and needs
no configuration. The other is `/v1/chat/completions`, which means the inference
server becomes a deployment decision rather than an architectural one:

| Server | Works | Notes |
|---|---|---|
| **LiteLLM** | yes | A router in front of anything else. See below. |
| **vLLM** | yes | The one with guided decoding; `--api-key` supported. |
| **llama.cpp** (`llama-server`) | yes | |
| **LM Studio** | yes | Its server tab serves `/v1`. |
| **TGI** | yes | |
| **SGLang** | yes | |
| **Ollama** | yes | Also serves `/v1`, though its native wire has more. |

## The whole change

```yaml
# config/configuration.yaml
llm:
  backend: openai
  url: http://127.0.0.1:8000/v1
  model: Qwen/Qwen3-8B
  # api_key: !env_var VLLM_API_KEY     # only if you started it with one
```

Everything else — the persona, the tool registry, the approval tiers, the voice
pipeline — is unchanged and does not know which wire it is on.

## Two things that bite

**The `/v1` is load-bearing.** A bare `http://host:4000` is read as *Ollama*.
That is deliberate: every existing install writes its Ollama url exactly that
way, and inferring `openai` from a bare host:port would break all of them on
upgrade. So either put `/v1` on the url or write `backend: openai` explicitly.
Getting it wrong produces a 404 from `/api/chat`, reported as *"Ollama returned
404"* — which is the error you will see if you skipped this paragraph.

Pasting a full endpoint is fine, though: `…/v1/chat/completions` is trimmed back
to its base, as is `…/v1/models`.

**`model:` is the router's name for the model, not the provider's.** With
LiteLLM in front, `model: gpt-4o` does not reach OpenAI unless your LiteLLM
config has a `model_name: gpt-4o` entry. Use the names from *your* router.

## A worked LiteLLM pair

```yaml
# litellm-config.yaml
model_list:
  - model_name: house-model
    litellm_params:
      model: openai/Qwen3-8B
      api_base: http://vllm:8000/v1
      api_key: os.environ/VLLM_API_KEY
  - model_name: house-model-big
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: os.environ/ANTHROPIC_API_KEY

general_settings:
  master_key: sk-house-master
```

```yaml
# jarvis-core/config/configuration.yaml
llm:
  backend: openai
  url: !env_var LLM_URL http://litellm:4000/v1
  model: !env_var LLM_MODEL house-model
  api_key: !env_var LLM_API_KEY
  backend_name: LiteLLM          # appears in error messages
  # headers:                     # anything else the router wants
  #   x-litellm-tags: house
```

```sh
# .env
LLM_URL=http://litellm:4000/v1
LLM_MODEL=house-model
LLM_API_KEY=sk-house-master
```

Swapping the model behind Jarvis is then a change to `litellm-config.yaml` and
nothing else — which is the point of running a router at all.

## Authentication

`api_key` becomes `Authorization: Bearer <key>` on every request to the model
server, and **only** to the model server. It is sent per request rather than
installed on the HTTP client, because jarvis-core shares one connection pool
between the model and every YAML- or console-authored HTTP tool — and a key on
the shared client would be sent to every third-party endpoint the model can
reach. Which endpoints those are is a decision the *model* makes.

`headers:` works the same way, for a router that needs more than a bearer token.

If you write `api_key: !env_var LLM_API_KEY ''` and the variable is unset, the
empty-string default is stripped rather than sent as a literal `Bearer ""`.

## What differs between the two wires

| | `ollama` | `openai` |
|---|---|---|
| Streaming | NDJSON | server-sent events |
| Tool calls | one finished object | fragments, reassembled by index |
| Tool results | matched by tool **name** | matched by `tool_call_id` |
| Reasoning | `message.thinking` | `delta.reasoning_content` |
| Guided decoding | no | yes (`response_format` + `guided_json`) |
| Embeddings | no | yes (`/v1/embeddings`) — used by `memory:` |
| `keep_alive` | honoured | accepted and ignored (Ollama's own) |
| `think` | honoured | accepted and ignored (Ollama's own) |
| `options: num_ctx` | honoured | dropped; on this wire the context length is a property of how the server was started, and sending it as `max_tokens` would cap the *reply* at the size of the window |

Other `options:` keys map across where they mean the same thing
(`temperature`, `top_p`, `seed`, `stop`, `num_predict` → `max_tokens`,
`presence_penalty`, `frequency_penalty`); the rest go through as `extra_body`,
which vLLM reads and a stricter server ignores.

Both wires present the same class surface, so `ConversationAgent` cannot tell
which one it has — including the two methods that differ underneath.
`tool_call_id` is one of them and is not cosmetic: LiteLLM translating to
Anthropic or Bedrock rejects a tool result that does not name the call it
answers, so without it the *second* round of every multi-tool turn fails.

## Failure handling

The client distinguishes the failures a proxy actually produces:

* **4xx** is not retried, except 408 and 429. A wrong key or an exhausted budget
  will be exactly as wrong half a second later, and retrying only doubles the
  wait before the same apology.
* **429 with `Retry-After`** waits the interval the server asked for, capped at
  30 seconds — a rate limiter under load will happily name several minutes, and
  somebody standing in front of the orb needs an answer or an apology.
* **5xx and transport errors** get one retry with backoff, but only *before the
  first token has reached the user*. After that a retry would replay the
  sentence from the beginning, which on a voice path is a stutter the user
  hears.

At startup jarvis-core probes the server once, in the background, and logs
either how many models it is serving or a plain warning that it could not be
reached. That probe also fills the model dropdown in the console's settings
page.

## What this is not

This is **outbound** only: Jarvis talking to a model server. It does not make
jarvis-core serve `/v1/chat/completions` to other applications. Pointing
OpenWebUI at Jarvis is a different feature and does not exist yet.

## See also

* [`configuration.md`](configuration.md) — every key in the `llm:` block
* [`clients.md`](clients.md) — the websocket and REST contract
* [`../../docs/architecture.md`](../../docs/architecture.md) — how the pieces fit
