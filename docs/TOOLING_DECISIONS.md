# TOOLING_DECISIONS.md — what goes in the toolbelt, and what does not

Every row in `MILESTONES.md` from M31 to M37 proposes adding a service. This
file is the argument for each one, written **before** it is built, so that the
answer "we did not add it" is a decision with a reason rather than a thing
nobody got round to.

The rule this file exists to enforce: *a component earns its place by moving a
number*. `evals/intelligence/run.py` and the research, memory and coding evals
produce those numbers; `scripts/verify/toolbelt_baseline.py` snapshots them
before a change and compares after. A component that improves nothing
measurable is removed, however fashionable it is.

## The two budgets everything is spent against

**RAM, on this host.** 16 GB total, 4 vCPU — doubled from 8 GB on 2026-08-25,
mid-run, by the operator. Every decision below that was taken against the 8 GB
figure is marked, because a rejection whose reason has since changed is a
rejection that should be re-opened rather than quietly kept.

    $ free -g | head -2
                   total        used        free      shared  buff/cache   available
    Mem:              16           1          13           0           1          15

The CPU count did not change, and on this box that is now the tighter of the
two: four cores, of which `wyoming-whisper` may take three during a spoken turn.

**VRAM, on the model host.** There is no GPU on this box; `qwen3.8-27b` is
served by llama-swap over the tailnet (`BLOCKERS.md` §2). The 3090s hold that
model's weights and its KV cache, and the KV cache is what a longer context
costs — 12288 tokens of window, per concurrent request (`config/configuration.yaml`).

**The VRAM justification rule.** Nothing new gets GPU residency without a
written paragraph naming: what it evicts, how much cache it costs at the
current `num_ctx` and `max_concurrent`, and what number it improves in
exchange. No paragraph, no residency — the default answer is CPU or nothing.
The one that already applies: **embeddings must not go through llama-swap.**
An embedding request there swaps or shares the same VRAM as the voice path's
KV cache, so a note being indexed makes the next spoken turn slower. That is
M33's whole reason to exist.

## The slots

### 1. Headless browser — *keep one, share it* (M31)

**Chosen:** `jarvis-browser`, the service already in `jarvis-core/docker-compose.yml`.

**Rejected:** a second Chromium inside the research engine; a per-task
Playwright install. Two Chromiums on an 8 GB box is the single most expensive
mistake available in this list — each one is ~300 MB resident before it opens a
page, and they would idle in parallel.

`testing/live/fixture_browser.py` stays, demoted to what it always was: a
stand-in for a host with no stack, which proves nothing about the browser and
says so.

### 2. Crawling and extraction — *measured, and both rejected* (M32)

**Crawl4AI 0.9.2 — rejected.** Pulled and run on this host, not read about:

| | |
|---|---|
| image | **4.23 GB** |
| resident, idle, one browser pool | **411 MB** |
| free RAM on this host with the stack up | ~350 MB |
| its browser | its own Chromium — the second one in the system |
| loopback | refused, like ours: `URL blocked (SSRF protection)` |

The last row is worth the space it takes: its SSRF guard blocks the fixture web
exactly as `jarvis-browser`'s does, so adopting it would not even have removed
the problem M31 solved — it would have moved it. What it would genuinely have
bought is better markdown, and the gap that mattered there was tables.

So the gap was closed instead, in `jarvis-browser/jarvis_browser/extract.py`,
in about forty lines. Measured on the fixture handbook's rate table:

    before   Rate / Hours / Unit price / Day / 07:30–00:30 / 28.4 p/kWh / …
             every figure preserved and not one row — a model cannot tell
             which price belongs to which rate

    after    | Rate  | Hours       | Unit price | Standing charge |
             | ---   | ---         | ---        | ---             |
             | Day   | 07:30–00:30 | 28.4 p/kWh | 53.1 p/day      |

A new eval question asks for the night rate and its hours; it passes on the
second form and cannot be answered from the first.

**Docling — rejected, and not close.** `pip install docling` resolves to **101
packages**, among them torch, torchvision, transformers, opencv **and the whole
CUDA stack** (cublas, cudnn, nccl, cusolver, cusparse, nvshmem, nvjitlink). On
a box with no GPU and ~350 MB free, that is gigabytes of GPU libraries to read
a text-layer PDF.

What was built instead, in `jarvis-browser/jarvis_browser/documents.py`:

* **PDF** — `pypdf`, one pure-Python wheel, reading the text layer. It does not
  OCR, and a scanned PDF is *named as such* rather than returned as an empty
  string, because an empty string in a model's context is an invitation to
  invent the contents.
* **DOCX** — the standard library. A .docx is a zip of XML; paragraphs are
  `w:p`, headings are a style name, tables are `w:tbl`. No dependency at all.

Both come back through `/fetch`, fenced as untrusted exactly like a page,
because a PDF somebody sent you is a stranger's text arriving in a model's
context.

**What this does NOT buy**, and the honest place to say so: no OCR, no
JavaScript-heavy *crawling* at depth, and no page-to-page link following —
`lead_depth` follows new SEARCH QUERIES, not URLs, so Jarvis reaches a document
when a search surfaces it. The fixture search now indexes PDFs and .docx files
for that reason, which is what a real search engine does.

### 3. Embeddings and reranking — *TEI, two instances, and the reranker only where it wins* (M33)

**Chosen: Text Embeddings Inference `cpu-1.9`, twice.** The prediction here was
Infinity, on the reasoning that one process serving both models is the cheaper
shape on a small box. Measured, that is backwards:

| | Infinity `latest-cpu` | TEI `cpu-1.9` |
|---|---|---|
| image | 2.34 GB | **686 MB**, shared by both instances |
| resident, embedder + reranker | **4 GB** (OOM-killed at 3) | **329 MB + 218 MB** |
| models per process | two | one |
| containers | 1 | 2 |
| embed latency | 77 ms | **9 ms** |

Two containers of one image is seven times cheaper in memory than one container
of another, so the "fewer processes" instinct cost nothing to check and would
have cost 3.5 GB to keep. TEI is Rust; Infinity carries torch, onnxruntime and
OpenVINO.

One more thing the measurement found: Infinity refused to load
`mxbai-rerank-xsmall-v1` at all — OpenVINO cannot compile DeBERTa's dynamic
rank — so the model choice was constrained by the server, which is its own
argument.

**Why any of this is worth a container.** `memory/vectors.py` embedded through
the LLM client, and this deployment's llama-swap answers `/embeddings` with
`no router for requested model`. So semantic recall was configured, degraded
silently to keyword search exactly as designed, and **had never once run
here**. Worse, had it worked it would have been an eviction: an embedding
request through llama-swap costs the voice path's KV cache. On CPU it costs
9 ms and nobody's cache.

The number it moved, on the memory eval's six paraphrase queries — queries that
share no content word with the note that answers them:

    keyword only          recall@1   0%    recall@3   0%     (nothing at all)
    + embeddings         recall@1 100%    recall@3 100%

**And the reranker earns its place in exactly one of the two jobs.** Same
model, same host, measured both ways:

| | embedder alone | + cross-encoder |
|---|---|---|
| personal notes (6 paraphrases, 4 candidates) | **6/6** | 5/6 |
| documents (5 questions, 8 fixture pages) | 3/5 | **4/5** |

A note is one line — *"I take my coffee black"* — and a model trained to rank
web passages has almost nothing to read. A page is a page. So `research:` uses
the cross-encoder to choose which pages get FETCHED (the expensive step, and
the decision is made before any of them is), and `memory:` ships with it off,
one line from being switched on, with those numbers in the config beside it.

`bge-reranker-base` was measured too and matched the small model exactly (5/6
and 4/5) for **2.16 GB and 91 ms** against **177 MB and 12 ms**. Size bought
nothing here.

**The floor is part of the model.** `SIMILARITY_FLOOR = 0.62` was tuned for
`nomic-embed-text`. `bge-small-en-v1.5` ranked all six paraphrases correctly at
0.450–0.652, and that constant threw five of them away — a working model
turned into an empty search by a number from a different one. Floors and task
prefixes now live in one table per family (`vectors.py`).

### 4. The vector store — *the sidecar stays, and here is the number* (M34)

**Kept:** `<config>/.storage/memory-vectors.json` — id → unit vector, base64
packed, with a cosine scan in pure Python. **Rejected:** Qdrant, pgvector,
sqlite-vec, and every other index, for now.

Measured with `scripts/verify/vector_store_bench.py` against the real embedder,
on this host:

| entries | one-off embed | index | **query scan** | vectors in RAM | sidecar on disk |
|---|---|---|---|---|---|
| 500 (the configured cap) | 2.2 s | 0.02 s | **6.3 ms** | 750 KB | 1.0 MB |
| 2 000 | 9.1 s | 0.10 s | 25.3 ms | 3.0 MB | 4.0 MB |
| 10 000 | 47.7 s | 0.48 s | 127 ms | 15 MB | 20 MB |

A spoken turn on this host takes 7–10 seconds. At the configured cap the search
is **6 ms** — 0.08% of it. At twenty times the cap it is 127 ms, still under
2%. The scan is linear and predictable at about **1 ms per 80 notes**, so the
crossover where a real index would be felt is somewhere north of 50 000
entries: half a second of scan is the first number a person would notice next
to a two-second answer.

Nothing about that argues for a container today. What it does do is name the
condition for changing the answer, which is the part that was missing:

* **more than ~25 000 entries**, where the scan passes 300 ms and starts
  competing with the model for the user's patience; or
* **more than one process** needing the same vectors, since a JSON file that
  two writers own is a corruption waiting to happen; or
* **filtered search** (by tag, by date, by pin) becoming a common operation
  rather than a rare one — that is where an index earns its keep and a scan
  does not.

The first index to try when one of those becomes true is `sqlite-vec`: it is a
file and not a service, so it costs a dependency rather than a container, a
port, a volume and a backup story.

Qdrant specifically stays rejected for the reason `vectors.py` already
recorded and which no benchmark changes: its stock container POSTs to
`telemetry.qdrant.io` hourly, and "nothing goes to the cloud at runtime" is the
first sentence of this project's README.

### 5. Speech — *both stay where they are, and one open defect closed* (M35)

**STT: Wyoming faster-whisper stays. speaches is not adopted.** The case for
swapping was one specific defect — `ISSUES.md`, "a transcript is occasionally
doubled on the wake-word path" — on the theory that it was a
`condition_on_previous_text` setting the current container does not expose.

Re-tested, the doubling was not occasional at all: **three runs out of three**,
every utterance, `"Turn on the ceiling lights.  Turn on the ceiling lights."`
The two spaces were the clue — faster-whisper returning one sentence as two
segments, which is the repeat hallucination long silences provoke. The
container does not expose `condition_on_previous_text`, but it does expose
`--vad-filter`, which trims the silence that provokes it:

    before   WER 1.00, three runs of three doubled
    after    WER 0.00, three runs of three clean

So the defect that justified a new service was closed by a flag the service we
already run has had all along. That also made two negative scenarios stronger:
silence and room tone now produce **no text at all** rather than Whisper's
famous "You" hallucination, so `voice-silence` and `voice-room-tone` assert a
coded `stt-no-text-recognized` instead of the weaker "whatever it heard moved
nothing".

What would reverse this: a need for per-request decoding parameters (an
OpenAI-compatible server takes them per call; Wyoming takes them at startup),
or a second recogniser for a second language.

**TTS: Piper stays the default, and Kokoro is one flag away.** Both engines
were measured on five real replies, twice (`scripts/verify/tts_ab.py`):

| | median synth | real-time factor | round-trip WER | cost |
|---|---|---|---|---|
| Piper `en_GB-alan-medium` | 1.7 s | 0.40–0.52x | 0.000–0.040 | 33 MB of model, already running |
| Kokoro `bm_george` | 1.4 s | 0.39–0.47x | 0.000 | 3.2 GB image, 1 GB resident |

The gap is inside the run-to-run variance. Neither is slower than real time,
neither is hard to understand, and the numbers refuse to pick. **So the numbers
do not get to pick** — `docs/tts-review/` holds the same five sentences in both
voices, and Piper stays the default only because a tie is not a reason to spend
3.2 GB of somebody's disk.

`jarvis/voice/openai_tts.py` makes the switch a config key rather than a code
change, and `jarvis-tts` is in the stack behind `--profile kokoro`. One trap
worth recording: Kokoro streams its WAV, so the header's frame count is a
placeholder — it claims 89 478 seconds — and reading by it returns nothing.
The length comes from the bytes.

### 6. Observability — *the data stops being thrown away; Langfuse still does not come in* (M36)

**Rejected: Langfuse v4.** Not on the grounds this file first gave. When that
paragraph was written the box had 8 GB and the answer was "it does not fit";
the operator doubled it to 16 GB mid-run, so the honest thing is to re-argue it
rather than keep a rejection whose reason expired.

Measured here rather than assumed: ClickHouse — the component this file called
the expensive one — is a **942 MB image and 169 MB resident at idle**. Cheap at
rest. Langfuse's own self-hosting guide still asks for **4 cores and 16 GiB**,
and that ask is about load rather than idle; this host has four cores, of which
`wyoming-whisper` may take three during a spoken turn.

But the reason it stays out is no longer arithmetic:

* **It is six containers** — langfuse-web, langfuse-worker, Postgres,
  ClickHouse, Redis and MinIO — to put a user interface over data **this
  process already produces**. Every tool call, model call, approval and
  subagent already fires an event, and every event already carries a `Context`
  with an id and a parent. That is a trace and a span; nothing was missing
  except somebody keeping them.
* **It would hold a second copy of the user's private data.** Traces contain
  the prompts, which contain the memory block, the notes and the house. A
  second datastore of that is a second thing to secure, back up and delete
  from, for a UI.
* **The capability was the requirement, not the product.** M36 asks for every
  agent step, subagent, tool call, token count, latency and judge verdict, plus
  a "view trace" link in the task UI. That is what
  `jarvis/integrations/observability/` delivers, in ~300 lines, at the cost of a
  dict append per span and one line of JSON per finished trace.

**What was built.** A recorder that subscribes to the lifecycle events that
already existed, groups them by context id, nests them by parent id, and pairs
each `*_started` with its `*_finished`. Bounded on both axes — `max_traces`,
`max_spans`, and a truncation count so a trace never lies about what it dropped.
Finished traces append to `<config>/traces/<date>.jsonl`, so "why did it do
that" survives a restart.

One seam was added anywhere else: `jarvis_model_call`, fired after each
exchange with the model, because token counts and time-to-answer live in the
raw payload and are gone the moment the stream closes. They are the only
measure of what a turn actually cost.

**What would reverse this:** more than one Jarvis to compare (traces from
several hosts want a server), or a need to query traces analytically rather
than read them. The JSONL on disk is deliberately the shape you can ship
somewhere else — if the operator runs Langfuse elsewhere on the tailnet, these
events go to it without changing what produces them.

### 7. n8n — *a bridge, off, with an allow-list* (M37)

**Chosen:** a bridge to the operator's existing n8n. Nothing is installed here
and nothing is rebuilt: their odd jobs stay where they live.

Three refusals, in order, each with a test named after it:

* **`enabled: false` is the shipped value.** It runs code on another machine,
  which `PROCESS.md` §2d calls a reach surface, and a reach surface is opt-in.
  Off means off: with the flag down the bridge does not reach n8n even when
  asked directly.
* **The `workflows:` list is an allow-list, not a discovery.** n8n's API can
  enumerate every workflow on the instance and this deliberately never calls
  it, so adding a workflow to n8n can never silently add a capability to
  Jarvis. The refusal names what IS allowed, so the failure is actionable.
* **Tier 3 unless the operator lowers it themselves.** Running an automation
  has effects this process cannot see — an email, a garage door. A `tier: 1` is
  a sentence in their config file rather than a default nobody chose, and an
  unparseable tier is the safe one rather than a crash at startup.

A workflow is started through its **Webhook trigger node**, because n8n's
public API cannot start an arbitrary workflow. One configured without a
`webhook:` is listed and refuses to run, naming the node it needs — a better
failure than a 404 from a URL nobody meant to call. The API key travels as a
header and never in a URL, where it would land in n8n's access log.

### 8. LLM gateway — *LiteLLM, no database, and the guard is not where you expect* (M40)

**Chosen:** `ghcr.io/berriai/litellm:main-stable` as the single internal
endpoint, with a config file and **no database**. LiteLLM's full control plane
is Postgres and Redis to bill one household; routing, fallbacks and per-model
rate limits are config, which is what M40 actually needs.

**Local-only stays a complete configuration.** Two models ship, both local, and
every cloud provider is commented out — they need keys the operator has not
supplied. An install that never touches this file has a working gateway with
nowhere off-network to send anything.

**The guard took three attempts, and the first two failed silently.** This is
worth recording because both looked correct and neither did anything:

| attempt | why it failed |
|---|---|
| `litellm_settings: callbacks: privacy_guard.guard_instance` | a callback WATCHES a request. The proxy dispatches `async_pre_call_hook` to one only under conditions this did not meet: it loaded cleanly, logged nothing, and let a tagged request through to the cloud mock |
| `guardrails: [{guardrail: privacy_guard.PrivacyGuard, mode: pre_call}]` | the mechanism meant for refusing — and custom guardrails route through `initialize_callbacks_on_proxy(premium_user=…)`, so on the free image the block is accepted and the guardrail never runs |
| `general_settings: custom_auth: privacy_guard.privacy_auth` | **works.** Runs on every request, receives the whole `Request`, may raise, and is not a licensed feature |

What caught both failures was `testing/fixtures/gateway_probe.py` asserting the
mock cloud provider had **heard nothing** — not that a log line appeared. A
guard verified by its own logging is a guard verified by the thing that was
absent.

Taking over authentication means implementing it, so the master key is checked
in the same hook. That is a real cost of this approach and it is written down
rather than discovered later.

**What the guard does:** a request tagged `local-only` — because its prompt
carries the memory block, quarantined content, or the results of a private tool
— is refused with a 403 if it was routed at a cloud model. Not downgraded to a
local one: a silent downgrade is a decision nobody made, and a turn that
quietly got worse is indistinguishable from a turn that quietly leaked.

Both halves exist on purpose. Jarvis tags (it knows what is in the prompt); the
proxy refuses (it binds anything that can reach the endpoint, not just a
well-behaved client). A test reads both files and fails if their idea of
"cloud" diverges.

## How a decision here gets overturned

    python3 scripts/verify/toolbelt_baseline.py --out .verify/toolbelt/before.json
    # ... add the thing ...
    python3 scripts/verify/toolbelt_baseline.py --out .verify/toolbelt/after.json
    python3 scripts/verify/toolbelt_baseline.py --compare .verify/toolbelt/before.json .verify/toolbelt/after.json

The compare exits non-zero when a metric got worse. A component that makes
nothing better and something slower is removed the same day, and this file
records why it was tried.

## Sources

Checked on 2026-08-25, against the projects' own documentation rather than
against what was current when this model was trained:

* Text Embeddings Inference — <https://github.com/huggingface/text-embeddings-inference> (v1.9, CPU images, cross-encoder rerankers, one model per instance). **Run here**: 686 MB image, 329 MB resident for `bge-small-en-v1.5` and 218 MB for `ms-marco-MiniLM-L-6-v2`, 9 ms per embed
* Infinity — <https://github.com/michaelfeil/infinity> (`latest-cpu`, multiple models in one process, ONNX/Optimum on CPU). **Run here**: 2.34 GB image, 4 GB resident for the same two models (OOM-killed at 3 GB), 77 ms per embed, and its OpenVINO path could not load a DeBERTa reranker at all
* Crawl4AI — <https://github.com/unclecode/crawl4ai> (0.9.2, `unclecode/crawl4ai:latest`, REST on 11235, `--shm-size=1g`, its own Playwright Chromium). Also **pulled and run here**: 4.23 GB image, 411 MB resident idle, and its SSRF guard refuses loopback exactly as ours does
* Docling — <https://github.com/docling-project/docling> (`docling-serve`, PDF/DOCX/PPTX/XLSX/HTML → markdown). Also **resolved here**: `pip install --dry-run docling` → 101 packages including torch, transformers, opencv and the CUDA stack
* Langfuse self-hosting — <https://langfuse.com/self-hosting/docker-compose> (v4; 4 cores / 16 GiB recommended, ClickHouse + Postgres + Redis + MinIO)
* speaches — <https://github.com/speaches-ai/speaches> (OpenAI-compatible STT/TTS, faster-whisper, Piper and Kokoro, `compose.cpu.yaml`)
* Kokoro-FastAPI — <https://github.com/remsky/Kokoro-FastAPI> (`ghcr.io/remsky/kokoro-fastapi-cpu`, `/v1/audio/speech`, ~3.5 s first token on an older i7)
* LiteLLM — <https://github.com/BerriAI/litellm> (`ghcr.io/berriai/litellm`, router with fallbacks, virtual keys and budgets; Postgres + Redis for the full control plane)
* Qdrant, pgvector and sqlite-vec for the store decision — and `jarvis-core/jarvis/integrations/memory/vectors.py`, which already records the telemetry objection that ruled Qdrant out here
