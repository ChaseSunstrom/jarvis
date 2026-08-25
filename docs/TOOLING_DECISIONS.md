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

**RAM, on this host.** 8 GB total, 4 vCPU, ~2 GB free with the stack up. This
is the binding constraint and it is small. A service that wants 4 GB is asking
for half of everything.

    $ free -g | head -2
                   total        used        free      shared  buff/cache   available
    Mem:               8           5           2           0           0           2

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

### 3. Embeddings and reranking — *yes, on CPU, and off llama-swap* (M33)

**Chosen (to try, in this order):** Infinity (`michaelf34/infinity:latest-cpu`)
serves an embedder **and** a cross-encoder reranker from one process, which on
this box is the deciding property; TEI (`ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`)
is faster per model but Hugging Face's own guidance is one instance per model,
so the same pair costs two containers.

**Why at all:** `jarvis-core/jarvis/integrations/memory/vectors.py` currently
embeds through the LLM client — the exact thing the VRAM rule forbids. Moving
it to CPU is the point; a rerank-after-retrieve pass on top is the cheapest
quality win available to memory, notes and research.

**What it must not break:** `jarvis-core`'s dependency list stays seven
pure-Python wheels (`DEVIATIONS.md` §9). The embedder is a service it talks to
over HTTP, never a package it imports, and every failure mode still ends in
"fall back to keyword search" rather than an error the user sees.

### 4. The vector store — *decide, do not drift* (M34)

**Current:** a JSON sidecar next to the notes, cosine in pure Python, ~500
entries. `vectors.py` already argues the case and measured the alternative:
Qdrant's stock container POSTs to `telemetry.qdrant.io` hourly, which breaks
this project's first sentence, and 500 × 768 floats is single-digit
milliseconds beside a multi-second generation.

**Decision, provisional:** keep the sidecar; M34 is discharged by the paragraph
and the numbers (entries, query latency, recall on the memory eval), not by a
container. It is re-opened by one number: if the store passes a few thousand
entries and query latency becomes visible next to a turn, a real index earns
its place — `sqlite-vec` first, because it is a file and not a service.

### 5. Speech — *services yes, replacement no, A/B the voice* (M35)

**STT:** the Wyoming faster-whisper container works. An OpenAI-compatible
front (speaches, which serves faster-whisper and Kokoro) buys one thing this
system actually needs: `condition_on_previous_text`, which is the suspected
cause of the doubled-transcript issue in `ISSUES.md` and is not exposed by the
current container. That is the measurement that decides it, not the API shape.

**TTS:** Piper stays. Kokoro-FastAPI (`ghcr.io/remsky/kokoro-fastapi-cpu`) is
A/B'd beside it — its own README reports ~3.5 s to first audio on an older i7
CPU, against Piper's near-instant, so on this box the likely verdict is "better
voice, too slow to talk to". The live suite's judge scores and the WER and
latency numbers are the tie-break, and the operator's ears are the appeal.

### 6. Observability — *wanted, and it does not fit* (M36)

**Candidate:** Langfuse v4. Self-hosting it is Postgres **and** ClickHouse
**and** Redis **and** MinIO, and its own documentation asks for 4 cores and
16 GiB. This box has 4 cores and 8 GB, two of them free.

**Decision, provisional:** not Langfuse on this host. What M36 must still
deliver is the *capability* — every agent step, subagent, tool call, token
count, latency and judge verdict, and a "view trace" link from the task UI —
so the fallback is the trace integration this repository already has
(`jarvis/integrations/trace/`) extended to cover the agent loops, written to
disk, with the UI reading it. If the operator later runs Langfuse elsewhere on
the tailnet, the same events can be shipped to it: the decision is about where
it runs, not about whether traces exist.

### 7. n8n — *a bridge, off by default* (M37)

**Chosen:** a bridge to the operator's existing n8n over the tailnet. Nothing
is installed here. The flag defaults off, the allow-list names the workflows
that may be called, and an un-listed workflow is refused. This is a reach
surface, so M43's rules apply to it before it is switched on.

### 8. LLM gateway — *LiteLLM, self-hosted* (M40, listed here for the budget)

`ghcr.io/berriai/litellm` is the routing layer for M40, and its production
shape wants Postgres and Redis. What M40 needs from it — policy routing,
fallbacks, per-provider caps, and a guard that refuses to send a local-only
request to a cloud provider — is available from the config file and callbacks
without the full control plane. The database is not adopted; if the guard
cannot be expressed without it, the guard is written in Jarvis instead, because
the guard is the requirement and LiteLLM is the convenience.

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

* Text Embeddings Inference — <https://github.com/huggingface/text-embeddings-inference> (v1.9, CPU images, cross-encoder rerankers, one model per instance)
* Infinity — <https://github.com/michaelfeil/infinity> (`latest-cpu`, multiple models in one process, ONNX/Optimum on CPU)
* Crawl4AI — <https://github.com/unclecode/crawl4ai> (0.9.2, `unclecode/crawl4ai:latest`, REST on 11235, `--shm-size=1g`, its own Playwright Chromium). Also **pulled and run here**: 4.23 GB image, 411 MB resident idle, and its SSRF guard refuses loopback exactly as ours does
* Docling — <https://github.com/docling-project/docling> (`docling-serve`, PDF/DOCX/PPTX/XLSX/HTML → markdown). Also **resolved here**: `pip install --dry-run docling` → 101 packages including torch, transformers, opencv and the CUDA stack
* Langfuse self-hosting — <https://langfuse.com/self-hosting/docker-compose> (v4; 4 cores / 16 GiB recommended, ClickHouse + Postgres + Redis + MinIO)
* speaches — <https://github.com/speaches-ai/speaches> (OpenAI-compatible STT/TTS, faster-whisper, Piper and Kokoro, `compose.cpu.yaml`)
* Kokoro-FastAPI — <https://github.com/remsky/Kokoro-FastAPI> (`ghcr.io/remsky/kokoro-fastapi-cpu`, `/v1/audio/speech`, ~3.5 s first token on an older i7)
* LiteLLM — <https://github.com/BerriAI/litellm> (`ghcr.io/berriai/litellm`, router with fallbacks, virtual keys and budgets; Postgres + Redis for the full control plane)
* Qdrant, pgvector and sqlite-vec for the store decision — and `jarvis-core/jarvis/integrations/memory/vectors.py`, which already records the telemetry objection that ruled Qdrant out here
