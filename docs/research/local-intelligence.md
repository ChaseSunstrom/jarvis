# Local intelligence — what would make Jarvis smarter, offline

Researched 2026-08-26 against the tree on `claude/jarvis-overhaul`. Everything
below is self-hosted; nothing calls a cloud API at runtime. Each item says what
the repo already has, what is new, what it gives Jarvis, what it costs, and how
it plugs in. Versions and licences were read from the projects' own pages on
the day; the Sources list at the end is the evidence.

## The two boxes, and the rules that already apply

Jarvis runs on **two machines**, and every recommendation is written against
that split:

| | Jarvis box (this host) | Model host |
|---|---|---|
| What | Debian 12 LXC, **4 vCPU, 16 GB RAM, no GPU**; `free -g` today: 5 used, 10 available with the stack up | llama-swap over the tailnet, RTX 3090s, serving `qwen3.8-27b` (`BLOCKERS.md` §2) |
| Runs | jarvis-core, jarvis-browser, three Wyoming containers, LiteLLM gateway, two TEI instances, optional SearXNG/Kokoro/Photon/Radicale/MQTT | the chat model, and whatever else the operator puts in llama-swap's config |
| Budget | CPU is the tighter of the two (`wyoming-whisper` may take three cores during a turn); ~8 GB of RAM is realistically spendable | VRAM: 27B Q4 weights (~16.5 GB) + KV cache for `num_ctx: 12288` × `max_concurrent: 2` |

`docs/TOOLING_DECISIONS.md` sets the rules this file respects: a component
earns its place by moving a number (`evals/intelligence`, research, memory and
coding evals; `scripts/verify/toolbelt_baseline.py`); **nothing gets GPU
residency without a written paragraph** naming what it evicts; embeddings never
go through llama-swap; no second inference runtime (no Ollama); no telemetry
(Qdrant was rejected for phoning `telemetry.qdrant.io`; Chroma's anonymised
telemetry is on by default and would fail the same test).

Two of the six areas have already been measured and partly rejected in that
file — Crawl4AI (4.23 GB image, 411 MB resident, its own Chromium) and Docling
(101 packages including the CUDA stack). Those numbers stand; this document
does not re-argue them, it says what is cheaper.

---

## 1. Better local models

### What exists

* `llm:` speaks OpenAI wire to the LiteLLM gateway (`jarvis-core/gateway/config.yaml`),
  which routes `house` and `house-fast` to llama-swap with mutual fallbacks.
  `GATEWAY_FAST_UPSTREAM_MODEL` → `house-fast` is wired in compose, **but
  nothing in `jarvis-core/jarvis/` asks for `house-fast`** — only the privacy
  guard and a test know the name. The fast lane exists on the router and is
  unused by the application.
* `think: false` + `allow_think_escalation: true`; `num_ctx: 12288`;
  `max_concurrent: 2`; `max_tool_rounds: 5`.
* `llm/openai_compat.py` already sends `response_format: {type: json_schema}`
  **and** vLLM's `guided_json` for schema'd calls; `llm/toolcalls.py` has the
  Hermes text fallback; `llm/plan.py` is a plan → verify → replan loop.
* Vision (`integrations/vision/analyze.py`) speaks **Ollama's** `/api/chat`
  with `images: [...]` — it cannot use the llama-swap path at all today.

### The model landscape (Apache-2.0 unless stated)

| Model | Params | GGUF Q4_K_M | Q8_0 | Notes |
|---|---|---|---|---|
| **Qwen3.8-27B** (already the house model) | 27B dense | **16.5 GB** (UD-Q4_K_XL 17.6) | 29 GB | 262k ctx, thinking switchable per request, **MTP head shipped** (unsloth lists a separate 1.37 GB MTP Q4_0 file), native vision (mmproj) |
| Qwen3.6-27B / 35B-A3B | 27B / 35B (3B active) | — | — | April 2026; MTP in llama.cpp via `--spec-type draft-mtp` |
| **Qwen3.5-9B** | 9B | **5.68 GB** (UD 5.97) | 9.53 GB | March 2026; 262k ctx; vision with mmproj; thinking default on, off with `enable_thinking: false` (no soft switch) |
| **Qwen3.5-4B** | 4B | **2.74 GB** (UD 2.91) | 4.48 GB | same family; the realistic CPU-box ceiling |
| Qwen3.5-2B / 0.8B | 2B / 0.8B | 1.28 GB / — | 2.01 GB | 0.8B is the vocab-matched draft for the 9B/27B |
| **Gemma 4 E4B** / E2B | ~4B eff. / ~2B | 4.98 GB (UD 5.13) | 8.19 GB | text+image+**audio** in, 128k ctx, "native structured tool use"; Ollama's parser choked on it — use llama.cpp |
| Phi-4-mini-instruct (MIT) | 3.8B | 2.49 GB | — | 128k ctx; ties Qwen3-4B on one 2026 tool benchmark |
| Llama 3.2 3B / 3.1 8B (Llama community licence) | 3B / 8B | ~2 / ~4.9 GB | — | native llama.cpp tool handler; weaker than the Qwen line on agentic sets |

**Which does tool calling best in llama.cpp.** The 2026 comparisons agree on
the same three sub-7B bases: Qwen3-4B-Instruct-2507 (now superseded by
Qwen3.5-4B), Gemma 4 E4B and Phi-4-Mini, all handled by llama.cpp's tool-call
parser since the March 2026 release; one benchmark ties `qwen3:4b`,
`phi4-mini` and even `qwen3:0.6b` at 0.88. The Qwen line has "unusually strong
tool-calling priors" and is the family the house already runs, so **stay in
the Qwen family** for both lanes: one template, one parser, one set of
sampling defaults, and the 0.8B is a valid draft for both.

Practicalities that bite: serve with `--jinja` (tool calling is off
without it); Qwen3.5+ emits the XML-style `<function=…><parameter=…>` form
that llama.cpp constrains with a *lazy grammar* triggered on the tool-call
opener — there is an open 2026 issue where a malformed `</parameter>` slips
past it on Qwen3.6 and the stream aborts, so keep the Hermes text fallback in
`toolcalls.py`; leave `parallel_tool_calls` off for small models; llama.cpp's
own doc warns that **`-ctk q4_0` degrades tool calling** — q8_0 is the floor.

### Speed expectations (bandwidth arithmetic, to be measured)

Decode on CPU and GPU is memory-bound: tokens/s ≈ bandwidth ÷ bytes of weights
read per token, and the correlation with measured RAM speed is linear.

* **Jarvis box (4 vCPU, DDR4-class LXC).** Qwen3.5-4B Q4_K_M (2.7 GB): roughly
  6–12 tok/s; 2B: 15–25; 0.8B: 30+. A datapoint: Llama-3.1-8B Q4_K_M did
  14 tok/s on 12 EPYC threads. Prompt processing is compute-bound and is the
  real cost here: Jarvis's system prompt + 28 tool schemas is several thousand
  tokens, and at ~50–100 tok/s prefill on four cores that is **30–60 s before
  the first token** unless the prefix is cached (below). A CPU-side chat model
  on this box is therefore only viable for *short-prompt* jobs (a router step,
  note-taking, memory extraction) — not the voice turn.
* **Model host (3090, 936 GB/s).** 27B Q4_K_M ≈ 40–50 tok/s baseline. The
  measured 5090 run went **73.6 → 133.6 tok/s** with MTP at draft depth 2–3;
  scale by bandwidth and expect roughly 40 → 70 on a 3090. Qwen3.5-9B on the
  same card: ~120+ tok/s, i.e. a spoken answer in well under a second.

### Two lanes in llama-swap

llama-swap's `groups` do exactly what the brief asks. The config lives on the
model host, not in this repo; this is the shape:

```yaml
healthCheckTimeout: 300
macros:
  "srv": "llama-server --port ${PORT} --jinja -fa on -ngl 999 --cache-reuse 256"

models:
  "qwen3.8-27b":                       # research, planning, authoring
    cmd: |
      ${srv} -m /models/Qwen3.8-27B-UD-Q4_K_XL.gguf
      -c 24576 -np 2 --cache-type-k q8_0 --cache-type-v q8_0
      --spec-type draft-mtp --spec-draft-n-max 2
      --temp 0.7 --top-p 0.8 --top-k 20
    env: ["CUDA_VISIBLE_DEVICES=0"]
    ttl: 0
  "qwen3.5-9b":                        # the voice turn
    cmd: |
      ${srv} -m /models/Qwen3.5-9B-UD-Q4_K_XL.gguf
      -c 24576 -np 2 --cache-type-k q8_0 --cache-type-v q8_0
      --reasoning-budget 0
    env: ["CUDA_VISIBLE_DEVICES=1"]
    aliases: ["fast"]

groups:
  "resident":
    persistent: true      # other groups cannot unload these
    swap: false           # both members run at once (one per GPU)
    members: ["qwen3.8-27b", "qwen3.5-9b"]
```

Then in `.env`: `GATEWAY_UPSTREAM_MODEL=qwen3.8-27b`,
`GATEWAY_FAST_UPSTREAM_MODEL=qwen3.5-9b`. **The Jarvis-side change is the
missing piece:** the conversation agent should ask the gateway for
`house-fast` on a voice turn and `house` for `deep_research`, `plan.py`
and automation authoring. That is a `model:` per call site, and the routing
accuracy row in the intelligence scorecard is the number it must not lower.

If only one 3090 is spendable, use `swap: true` with `ttl` on the 27B: the
9B stays resident for voice and the 27B loads on demand (a cold load of
17 GB is ~10 s from NVMe) — write that VRAM paragraph before choosing.

### Speculative decoding — where it pays and where it does not

* **MTP (`--spec-type draft-mtp`)** is the one to use: no second model, the
  head ships with Qwen3.6/3.8, and it measured **1.8×** on a dense 27B.
  `--spec-draft-n-max 2` is the documented sweet spot; 3 with an f16 cache.
* **A classic draft model (`--model-draft` with Qwen3.5-0.8B)** and the free
  n-gram modes (`ngram-simple`, `ngram-mod`, `ngram-map-k`) were benchmarked
  on a 3090 against the **35B-A3B MoE: no variant produced a net speedup**
  (−3 % to −52 %), because a 3B-active MoE is already cheap per token and every
  draft position loads more experts. Do not put a draft in front of a MoE.
* Reasoning roughly halves throughput; Jarvis's `think: false` is already the
  right default. `--reasoning-budget 0` disables thinking server-wide; the
  per-turn escalation Jarvis relies on needs the per-request
  `chat_template_kwargs: {"enable_thinking": true}` form instead — verify the
  build honours it before switching the flag on.

### KV-cache tricks that matter to Jarvis specifically

* **`--cache-reuse 256`**: KV shifting so an identical prefix is not
  re-prefilled. Jarvis's prefix (persona + tools + house summary) is long and
  constant within a session; this is the largest single latency win available
  and costs nothing. The server's prompt cache is on by default; `--cache-ram`
  (default 8 GiB host RAM) keeps idle slots' KV for reuse across slots.
* **`-np 2`** to match `max_concurrent: 2`; slots partition the context, so
  `-c` must be 2 × the window Jarvis asks for.
* **`--cache-type-k/v q8_0`** halves KV memory at negligible loss; q4_0 is
  documented to hurt tool calling.
* **`--slot-save-path`** + `/slots/{id}?action=save` if a fixed prefix should
  survive a restart (optional; measure before adding an ops step).
* `-fa on` is required for the quantised cache types.

**Cost:** none on the Jarvis box. On the model host: a second resident model
(≈6 GB + KV for the 9B) and the VRAM justification it requires. **Gives:**
a voice turn that no longer waits behind research, ~2× decode on the 27B,
and tool calls that arrive as JSON from a parser rather than a regex.

---

## 2. Local document intelligence

### What exists

* `jarvis-browser/jarvis_browser/documents.py`: **pypdf** text layer and a
  30-line DOCX reader; a scanned PDF is *named as scanned* rather than
  returned empty. Reached through `web_fetch`, fenced as untrusted.
* `integrations/files`: WebDAV `list_files` / `read_file` / `search_files` /
  `write_file` (Nextcloud, Synology, mod_dav).
* No OCR, no layout/table model, no document archive, and the vision path is
  Ollama-only.

### The engines

| Tool | Licence | Footprint | What it is good at |
|---|---|---|---|
| **Tesseract 5** (via **OCRmyPDF**, MPL-2.0) | Apache-2.0 | ~10 MB binary + language packs | Fastest on CPU (453 ms/page in one comparison), best accuracy/speed on clean scans; OCRmyPDF adds `--deskew`, `--rotate-pages`, `--skip-text`, PDF/A output |
| **RapidOCR** (PP-OCRv5 on ONNX Runtime) | Apache-2.0 | pip, no Paddle/CUDA, all models ≈ 258 MB | Better on photos/receipts/small fonts than Tesseract; 39+ languages; CPU out of the box |
| **PaddleOCR** PP-OCRv5/v6, PP-StructureV3 | Apache-2.0 | Paddle runtime (large) or ONNX/OpenVINO | The only free engine with built-in table + layout recovery; v6 claims 5.2× CPU speed-up; heavier install |
| **docTR** / OnnxTR | Apache-2.0 | torch, or ONNX via OnnxTR | Tunable detection+recognition pairs; FastAPI template |
| **Docling** + docling-serve | MIT | CPU image ≈ 2.7 GB; a 4.4 MB PDF drove RSS to ~12 GB on a 4 vCPU/16 GB box | Best structure fidelity (TableFormer, 88 % F1 vs MarkItDown's 82 %), 20+ formats, VLM pipeline (GraniteDocling) — **already rejected here on RAM** |
| **Unstructured** | Apache-2.0 | needs tesseract, poppler, libmagic, libreoffice; `hi_res` pulls detection models | 30+ types incl. email; `fast` strategy is cheap, `hi_res` is not |
| **MarkItDown** | MIT | pure Python (pdfminer, python-docx, openpyxl, python-pptx) | 100 pages in 12 s, no models; Office formats; **no local OCR** (its OCR plugin wants an LLM vision API) |
| **Paperless-ngx** | GPL-3.0 | app + Redis + DB (SQLite is fine) + optional Tika/Gotenberg; OCRmyPDF inside | The house archive: consumption folder, mail ingestion, tags/correspondents, full-text search, REST API |
| paperless-gpt | MIT | small Go/JS service | LLM titles/tags and *LLM-vision OCR* for Paperless via any OpenAI-compatible endpoint |

### The pipeline that fits this box

1. **Text layer first** (exists). 2. **If empty: OCR** — RapidOCR in the
   `jarvis-browser` image (one pip, 258 MB of ONNX models, CPU) or OCRmyPDF
   for whole scanned PDFs (adds a text layer once, so the next read is step 1).
   Tesseract is the cheaper install and fine for printed letters; RapidOCR is
   the one to A/B on receipts and phone photos. 3. **Tables and layout:**
   PP-StructureV3 via ONNX if the eval shows tables are lost; Docling only if
   this host ever gets a GPU or the model host takes docling-serve.
4. **VLM read as the escalation:** Qwen3.8-27B and Qwen3.5-9B are natively
   multimodal, so "read this scan" can be the house model looking at the page
   — highest accuracy on messy documents, zero new services, but it costs
   image tokens (~1k+ per page) and a VRAM paragraph for the mmproj. This
   needs the vision integration to grow an OpenAI-wire path (`image_url`
   content parts against llama-server with `--mmproj`), which it lacks today.

### "Any document the house has": Paperless-ngx as the archive

Paperless is the right shape because it already does the ingestion nobody
wants to write — a watched folder, IMAP polling, OCRmyPDF, dedup, and a
searchable `content` field — and exposes all of it:

```
Authorization: Token <token>        Accept: application/json; version=10
GET  /api/documents/?query=boiler+service+2025        # full-text with syntax
GET  /api/documents/?text=council+tax                 # simple title+content
GET  /api/documents/?more_like_id=123
POST /api/documents/post_document/  (multipart: document, title, tags, created)
GET  /api/documents/{id}/download/ | /preview/ | /thumb/
```

`content` (the OCR text) comes back in list results, so a `document_search`
tool can hand the model the matching passage without a second call. A
`paperless` integration is four tools (`document_search`, `document_read`,
`document_upload`, `document_file`) plus `.env` keys, all results fenced as
untrusted like `web_fetch`. paperless-gpt (MIT) can be pointed at the gateway
for titles/tags — but the privacy guard must tag those requests `local-only`,
because a document body is exactly the kind of text the guard exists for.

**Cost:** Paperless ≈ 1 GB RAM idle (more during OCR), Redis ≈ 20 MB, disk
for originals + archived PDFs; RapidOCR ≈ 300 MB on disk and a CPU second
per page. **Gives:** every letter, bill, manual and receipt in the house
becomes answerable ("when is the boiler service due?", "what did the council
say about the bins?"), and scans stop coming back as "this PDF has no text".

---

## 3. Local web capability beyond search + fetch

### What exists

* `web_search` (SearXNG, `--profile search`), `web_fetch`, **`web_crawl`**
  (BFS with page/depth/byte/time/robots limits in `jarvis-browser/crawl.py`),
  `web_browse` (Playwright sessions), the in-house extractor (`extract.py`,
  tables to markdown), `deep_research` (plan → search → read → note → write,
  reranked).
* `schedule_task` / `list_scheduled` / `cancel_scheduled`, `note_*`,
  `write_file`, and notifications — the parts of "watch this page" that are
  not the watching.
* Crawl4AI 0.9.2: measured, rejected. Firecrawl: AGPL-3.0, needs API +
  Playwright + Redis + RabbitMQ + Postgres and **8–12 GB RAM** — out on the
  same arithmetic, before its licence is even discussed.

### Watching: changedetection.io (Apache-2.0)

`dgtlmoon/changedetection.io`, port 5000, API key in `x-api-key`. It has the
one thing a home-grown scheduler + fetch + diff does not: a **price/restock
processor** that parses the product price and understands "out of stock":

```
POST /api/v1/watch
{ "url": "...", "title": "Espresso machine", "tag": "jarvis",
  "processor": "restock_diff",
  "time_between_check": {"hours": 6},
  "price_change_threshold_percent": 5,   # or price_change_min / _max
  "in_stock_processing": "in_stock_only", # | all_changes | off
  "follow_price_changes": true,
  "notification_urls": ["json://jarvis-core:8123/api/webhook/<id>"] }
GET  /api/v1/watch/{uuid}/history/latest      # last text snapshot (?html for HTML)
```

Notifications go through Apprise, so a JSON webhook into Jarvis's existing
webhook/automation path is the whole integration; the tool is
`watch_page(url, what: price|stock|text, threshold)` and the result arrives
as an event Jarvis can speak or text. JS-heavy pages need its
`sockpuppetbrowser` container — a **second Chromium** (~300 MB+), the cost
`TOOLING_DECISIONS.md` §1 refused once; start with plain fetch mode and add
the browser only for a page that needs it.

The alternative that costs nothing: `schedule_task` → `web_fetch` → compare
against the last snapshot in a note → notify. It cannot parse prices or stock
and re-fetches with Jarvis's own browser; it is the right first version if the
eval question is only "tell me when this page changes".

### RSS: Miniflux (Apache-2.0)

Postgres-only, "a couple of MB of memory" with hundreds of feeds, built-in
readability for full-content fetching, scraper rules per feed.

```
X-Auth-Token: <key>
POST /v1/discover        {"url": "https://example.org"}
POST /v1/feeds           {"feed_url": "...", "category_id": 1}
GET  /v1/entries?status=unread&order=published_at&direction=desc&limit=20&after=<unix>
GET  /v1/entries/{id}/fetch-content
PUT  /v1/entries         {"entry_ids": [...], "status": "read"}
```

Gives a real "what's new on the sites I follow" for the briefing, and a
`subscribe(url)` verb. Cost: Postgres (the one dependency; ~100 MB idle) —
unless Paperless is adopted on Postgres too, in which case they share it.

### Archiving

ArchiveBox (MIT) is the full answer — wget/WARC, SingleFile, readability,
PDF, screenshot, yt-dlp — but its REST API is still alpha in the 0.9 line and
every good extractor needs its own Chromium. For Jarvis the 90 % case is
"keep a copy of what I just read": `web_fetch` already yields extracted
markdown, and `write_file`/`note_create` can keep it with the URL and date.
Adopt ArchiveBox only when the question is preservation of the rendered page.

### Reader quality

The extractor is home-grown and tuned on the fixtures; if the research eval
ever shows boilerplate leaking or articles truncated, **trafilatura**
(Apache-2.0 since 1.8; lxml + jusText + readability-lxml fallbacks, date and
author metadata) is the pure-Python step up, not a crawler service.

---

## 4. Local speech upgrades

### What exists

* STT: `rhasspy/wyoming-whisper:3.5.0` (faster-whisper) with `base.en`,
  `--vad-filter` (which closed the doubled-transcript defect), capped at 3 CPUs
  / 2 GB. The image accepts `tiny…large-v3` and `turbo`; its README now also
  lists **`rhasspy/qwen3-asr-0.6b-onnx-int4`** as a loadable model.
* TTS: `wyoming-piper:2.3.1` `en_GB-alan-medium` (33 MB, real-time factor
  0.4–0.5); Kokoro-FastAPI behind `--profile kokoro` via `voice/openai_tts.py`
  — A/B'd, a tie, Piper kept as default.
* Wake: `wyoming-openwakeword:2.1.0`, `hey_jarvis`; `docs/wake-word-training.md`
  covers training (code Apache-2.0; the shipped models CC BY-NC-SA 4.0).
* **Speaker verification** (`voice/speaker.py`, `docs/voice-identity.md`):
  owner-or-not on the same audio, before the intent stage. That is not
  diarisation.

### STT

* **faster-whisper large-v3-turbo on CPU.** int8: ~1.5 GB peak, and about
  **7× faster than large-v3** (19.6 s vs 143 s on the same clip); RTF ≈ 2.5 on
  a fast desktop core — i.e. a 3-second utterance takes 5–8 s on this box's
  three capped cores. `base.en` does it in ~1 s. Turbo is the accuracy
  upgrade, **not** a latency one, on four vCPUs; `small.en` /
  `distil-small.en` is the honest ceiling here, and the large models belong on
  the GPU host (1.5–2 GB VRAM — the paragraph writes itself, but it still has
  to be written).
* **NVIDIA Parakeet-TDT-0.6B-v3** (CC-BY-4.0, 25 European languages,
  punctuation + timestamps, 6.3 % average WER on the Open ASR leaderboard —
  ahead of large-v3): **RTF 0.325 on CPU via ONNX** — three times faster than
  real time where turbo is slower than real time. `achetronic/parakeet` serves
  it OpenAI-compatible; `wyoming_openai` (Apache-2.0) bridges any OpenAI STT/TTS
  to the Wyoming socket jarvis-core already dials, so **the swap is a container
  and a port, no Jarvis change**. This is the STT upgrade that fits the box; the
  number it must move is WER in `docs/LIVE_TEST_REPORT.md` without raising
  per-stage latency. `speaches` (MIT; faster-whisper + Kokoro + Piper behind
  `/v1/audio/*`, SSE streaming, dynamic model load/unload) is the same idea
  with Whisper models, and the door `TOOLING_DECISIONS.md` §5 left open for
  per-request decoding parameters.
* **Streaming.** Jarvis's pipeline is utterance-shaped (wake → whole
  utterance → STT), so streaming buys partials for the console and earlier
  barge-in, not a different answer. `whisper.cpp stream` (MIT) is a sliding
  window with a naive VAD; **Vosk** (Apache-2.0; 50 MB small models, true
  partial results, 20+ languages) does stream, and `wyoming-vosk` (MIT) adds
  *limited-vocabulary / corrected* modes — the right tool for a noisy-room
  "lights off" grammar on a Pi satellite, not for open dictation.
* **Diarisation.** `pyannote/speaker-diarization-community-1`: **CC-BY-4.0**,
  gated (accept terms + HF token to download, then fully offline), pyannote.audio
  4.x, torch, CPU by default and slow; 8.9 % DER on REPERE. It answers "who
  said what" in a recording — meeting notes, a voicemail — which Jarvis does
  not do today; voice commands do not need it. NVIDIA's Sortformer is the
  NeMo alternative. Nothing here is MIT; the CC-BY attribution is easy, the
  gating is the operational nuisance.

### TTS

| Engine | Licence | Size | Cloning | CPU |
|---|---|---|---|---|
| Piper (now `OHF-Voice/piper1-gpl` 1.6.0) | **GPL-3.0** since Oct 2025 (was MIT) | 33 MB voices | train your own VITS voice (`python -m piper.train fit`, `|`-separated CSV, export ONNX) | fast |
| Kokoro-82M (in stack) | Apache-2.0 | 82M | **none** — 54 fixed voices, 8 languages | 5× real time on a big CPU |
| **Chatterbox** (Resemble) | **MIT** | Turbo 350M, Nano 110M (EN); Multilingual v3 500M (23 langs) | ~10 s reference clip; emotion dial | **Nano: 3× real time on 8 cores**; Turbo/Multilingual run on CPU, slower |
| Orpheus 3B | Apache-2.0 | 3B | yes | GPU only in practice |
| XTTS-v2 | Coqui CPML (non-commercial) | — | best 6-s clone | GPU |
| F5-TTS | code MIT, **weights CC-BY-NC-4.0** | — | yes | GPU |

Two honest routes to "Jarvis in a voice you chose": **Chatterbox** (MIT,
CPU-capable, imperceptible Perth watermark in every file — note that) served
by `wyoming_openai`, which lists it as a backend; or **train a Piper voice**
from an hour of clean recordings and keep the 33 MB runtime. The GPL flip only
matters if Jarvis is ever redistributed with Piper inside; running it is
unaffected. Kokoro cannot clone; that is a fact about the model, not a gap to
fix. The A/B script (`scripts/verify/tts_ab.py`) and `docs/tts-review/` are
where a third voice gets judged.

### Wake word

Nothing new to adopt: `docs/wake-word-training.md` is current (openWakeWord
notebook, Piper-synthesised positives, custom models are the operator's own;
microWakeWord for ESP32 satellites). The only research note: the pretrained
`hey_jarvis` is CC BY-NC-SA, which is fine for this house and wrong for a
product.

---

## 5. Local knowledge and reasoning

### What exists

* Durable memory (`.storage/memory.json`, remember/recall/forget, refuses
  web-derived text) with the **JSON vector sidecar** — 500-entry cap, 6 ms
  cosine scan, bench in `scripts/verify/vector_store_bench.py`; the written
  condition for an index is >25k entries, a second writer, or filtered search,
  and **sqlite-vec** (MIT/Apache-2.0 dual, a file not a service) is named as
  the first thing to try.
* TEI `cpu-1.9` × 2: `BAAI/bge-small-en-v1.5` (33M, 9 ms) and
  `cross-encoder/ms-marco-MiniLM-L-6-v2`; the reranker is used where it won
  (research).
* Notes integration; conversation history on disk (`.storage/conversations.json`);
  `evals/memory_eval.py`. `docs/AUDIT.md` §13 names the gaps: no
  auto-extraction, no UI, no export.
* `llm/plan.py` (plan/verify/replan), think-escalation, schema'd JSON via
  `response_format`.

### Knowledge-graph memory

| | LightRAG | Graphiti (Zep) | mem0 |
|---|---|---|---|
| Licence | MIT | Apache-2.0 | Apache-2.0 |
| Storage | 4 stores; default NetworkX/JSON files (dev-grade), or Postgres/Neo4j/Milvus/Qdrant | **needs a graph DB**: Neo4j 5.26+ or FalkorDB (Kuzu deprecated) | vector store (defaults to Qdrant/Chroma — both phone home unless told not to) |
| Local models | role-split LLM config (EXTRACT / QUERY / KEYWORD / VLM) over OpenAI-compatible or Ollama; **recommends ≥ Qwen3-30B-A3B for extraction** | `OpenAIGenericClient` with `base_url`; docs warn "very small models frequently emit JSON that doesn't match the schema" | Ollama/OpenAI-compatible |
| What it adds | local/global/hybrid/mix retrieval over entities + relations; server + WebUI + REST + docker | **bi-temporal facts** (valid-from/-to, invalidated not deleted) — "what did I believe in June" | extraction of memories from chats, dedup/update |
| Cost | one container (or in-process), and **many LLM calls per document at index time** on the 27B | a JVM/Rust graph DB (~1–2 GB) + the same extraction load | small, but its defaults fail the telemetry rule |

The honest read: the house model (27B) is above every "minimum size" these
projects state, so quality is not the blocker — **indexing cost and a new
database are**. Every note, page or document would be run through several
extraction prompts on the GPU host, competing with the voice path
(`max_concurrent: 2`). The repo's own gap is narrower than "a graph": it is
*episodic* memory — Jarvis does not learn from a day of conversations unless
told `remember`.

**Cheapest pattern that closes that gap, with no new store:** a nightly
scheduled job on `house` that reads the day's `conversations.json`, asks for
`{facts: [{text, kind: preference|fact|event, when, confidence}]}` under a
schema, writes them to memory with `source: conversation/<id>` and the date,
and dedups against the existing vectors (cosine > 0.9 → update, not insert).
That is one prompt, one call per day, measurable by `memory_eval.py`. Graphiti
becomes worth its database only when the eval starts asking temporal questions
the flat store gets wrong.

Embedding upgrades on the same TEI: `Qwen3-Embedding-0.6B` (Apache-2.0,
multilingual, 32k context, best sub-1 GB model on MTEB) costs ~20× the CPU of
bge-small per call — right for a multilingual house, wrong to adopt without a
retrieval number. `sqlite-vec` 0.1.10 pre-releases are current (May 2026).

### Making a small model reliable at tools — patterns, not frameworks

* **Constrain, don't hope.** llama-server turns `response_format:
  {type: json_schema}` into GBNF at sampling time (Jarvis already sends it);
  the raw `grammar` / `json_schema` fields are the same machinery. Keep
  schemas flat: `additionalProperties` defaults to false, nested `$ref`s are
  broken in the C++ converter, `uniqueItems`/`not`/conditionals are
  unsupported, patterns need `^…$`. Prefer `x{0,N}` to `x? x? x?` — grammars
  slow sampling and that pattern is the pathological case.
* **Tool choice.** `tool_choice: "required"` forces a grammar-constrained tool
  call for a "you must pick a tool" step (the planner); `"auto"` uses the lazy
  grammar that only engages when the model emits the tool opener. Enum every
  argument that can be enumerated (`room`, `domain`, `mode`) — an enum is a
  grammar and removes a whole class of "no such entity" retries.
* **A router on the fast lane.** One short `house-fast` call with a 4-way enum
  schema (`chat | device | lookup | research`) chooses the model and the
  toolset for the turn; small models are far more reliable choosing from four
  than from twenty-eight tools with arguments. `test_prompt_budget.py` already
  measures the toolbox against `num_ctx`; a router lets the voice turn carry a
  subset.
* **One tool per round, verify on failure only.** The plan→verify→replan loop
  exists; the cost is a verify call per step. Verify only when a tool errored
  or returned empty, and let `allow_think_escalation` carry the hard turns.
* **Reasoning budget, not reasoning toggle.** `--reasoning-budget N` caps the
  think block server-side; Jarvis's per-turn escalation wants the per-request
  form. Both exist; pick one and pin it in `test_llm_integration.py`.
* **Evidence:** the 2026 tool-call benchmarks that put Qwen3-4B level with
  Phi-4-mini were run *without* grammars; with them, malformed-JSON failures
  go to zero and what remains is argument choice — which is what the router
  and enums attack.

---

## 6. Local media and home

### What exists

* HA-compatible `media_player` domain is exposed (play/pause/volume on
  whatever entities exist); no library, no "play X by Y".
* **Photon** (`--profile geocode`, country extract; the planet download loop
  is documented); no routing.
* Weather is read from HA `weather.*` entities (`briefing` `_weather`); there
  is no Open-Meteo client in core.
* **Radicale** (CalDAV/CardDAV, `--profile fixtures`) and a calendar
  integration; contacts resolution exists on the phone
  (`android-app/tools/contact_resolve_test.py`), not on the server.
* Vision: camera frames → an Ollama-served VLM; no photo library.
* Nothing for music libraries, photo search, translation or maps routing.

### Music: Music Assistant (Apache-2.0) — the one to add

`ghcr.io/music-assistant/server` fronts **local files, Subsonic/Navidrome,
Jellyfin, Plex, radio** (and the streaming services, if wanted) and plays to
**Sonos, Chromecast, AirPlay, DLNA, Squeezelite, Snapcast, HA media players,
ESPHome**. One API on `:8095/api` (bearer token from Settings › Profile), JSON
`{message_id, command, args}` over HTTP or websocket:

```
music/search            {"search_query": "nick cave", "media_types": ["artist","album","track"]}
player_queues/all
player_queues/play_media {"queue_id": "<player>", "media": "<uri or item>", "option": "replace"|"add"|"next"|"play"}
player_queues/pause | resume | next | previous | seek
```

That is a `play_music(query, room, mode)` tool plus `what_is_playing`. The
Music Assistant docs themselves note Home Assistant has no intent for
*starting* music by voice and offer "expose playback as a tool your LLM
conversation agent can call" — which is exactly Jarvis's shape. Navidrome
(GPL-3.0, tiny, Subsonic 1.16.1 `search3`/`stream`) and Jellyfin (GPL-2.0,
swagger at `/api-docs`) are libraries to put *behind* MA, not integrations to
write twice. Cost: MA ≈ 300–500 MB RAM, ffmpeg; the players are the house's.

### Photos: Immich (AGPL-3.0)

"Photos of the garden last summer" is one call:

```
x-api-key: <key>
POST /api/search/smart
{ "query": "garden in summer", "takenAfter": "2025-06-01", "takenBefore": "2025-09-01",
  "personIds": [...], "type": "IMAGE", "size": 20, "withExif": true }
```

CLIP search runs in Immich's own ML container (1–7 GB RAM by model; the
SigLIP2 SO400M English model is the fast high-quality choice); the stack is
server + machine-learning + Postgres/VectorChord + Valkey. Immich is a large
tenant — **run it where the photos already are**, and let Jarvis be a client
with an API key; the console can render thumbnails through
`/api/assets/{id}/thumbnail`. If the house does not run Immich, the cheaper
answer to the same question is the house VLM over a WebDAV folder, which is
slow and unindexed — Immich is the index.

### Maps and routing

* Geocoding: **Photon exists** (Apache-2.0; 1–2 GB country index). Nominatim
  (GPL-2.0; `mediagis/nominatim:5.3`, `PBF_URL=` country import, Postgres,
  `REPLICATION_URL` updates, `/search` `/reverse` `/lookup`) only earns its
  Postgres if structured addresses or reverse-geocoding a GPS fix become
  common; Photon answers "the hardware shop".
* Routing: **OSRM** (BSD-2; `ghcr.io/project-osrm/osrm-backend`;
  `osrm-extract` → `osrm-partition` → `osrm-customize` → `osrm-routed`;
  `/route`, `/table`, `/nearest`, `/match`, `/trip`; a country extract needs
  a few GB RAM to build, ~55 GB for the planet) or **Valhalla** (MIT;
  `ghcr.io/valhalla/valhalla`; routes, isochrones, matrix, map-matching,
  elevation, multimodal; more disk, less RAM, tiles built from a PBF). "How
  long to the hardware shop, and is it quicker to cycle?" is Photon +
  `/route/v1/{driving|cycling|walking}` — one tool, two containers, all on
  disk.

### Weather

The only offline-ish option is **self-hosting Open-Meteo** (server AGPL-3.0,
data CC-BY-4.0): `ghcr.io/open-meteo/open-meteo`, `sync dwd_icon_d2
temperature_2m,precipitation,…`, same API on `:8080`. Storage is "a few GB"
for a limited variable set and 150 GB for everything; it must still pull the
model runs from the open-data mirror, so "offline" means *no third-party API
at query time*, not air-gapped — nobody runs a numerical weather model at
home. For "now", a local station over MQTT (the `mqtt`/`sensors` integrations)
is genuinely offline. The honest recommendation: keep the HA `weather` entity
as the source and add Open-Meteo self-hosted only if the house is already
paying the disk for a hobby.

### Translation

**LibreTranslate** (AGPL-3.0; `/translate` `/languages` `/detect`;
`--load-only en,fr,de` to cap RAM at ~1–2 GB per pair) wraps **Argos
Translate** (MIT; the same OpenNMT/CTranslate2 models in-process, no server).
The 27B already translates conversational text well; the case for a
deterministic engine is *batch and cheap* — translating a fetched foreign page
before the model reads it, or a language the model handles poorly. Argos as a
pip in jarvis-core (CPU, a few hundred MB per pair on disk) is the lighter of
the two; LibreTranslate is the same thing with a UI and a port. Whisper is
already multilingual for input; Piper needs a voice per output language.

### Calendar and contacts

Radicale exists; the missing half is **CardDAV contacts on the server** —
"text Mum" resolves on the phone today and cannot on a satellite. Radicale
serves CardDAV already, so it is a `find_contact(name)` tool over the same
`files/dav.py`-style four requests, no new service.

---

## Priority order (what moves a number first, cheapest first)

1. **Use the fast lane**: Qwen3.5-9B resident beside the 27B in llama-swap,
   `house-fast` for voice turns in the agent; `--cache-reuse`, q8_0 KV,
   `-np 2`, MTP on the 27B. Latency per stage is the number.
2. **Parakeet-TDT-0.6B-v3 behind `wyoming_openai`** as the STT A/B — WER down,
   RTF 0.3 on CPU, no Jarvis change.
3. **OCR in `jarvis-browser`** (RapidOCR or OCRmyPDF/Tesseract) so scans stop
   being "no text"; then **Paperless-ngx** as the archive with a four-tool
   integration.
4. **Music Assistant** — `play_music` is the single most-asked voice verb the
   house cannot do.
5. **changedetection.io** for price/stock watches via webhook; Miniflux for
   feeds into the briefing.
6. **Nightly episodic-memory extraction** on `house`, measured by
   `memory_eval.py`, before any graph database.
7. A **router step + enum'd schemas + `tool_choice: required`** for the
   planner; the vision integration's OpenAI-wire path so the multimodal house
   model can read a page.
8. Photon + OSRM/Valhalla for "how long to…"; Immich as a client only.
9. Chatterbox (MIT) or a trained Piper voice if the voice is to change;
   diarisation (pyannote CC-BY-4.0) only when recordings become a feature.
10. Rejected again on the numbers: Firecrawl, Docling on this host, Graphiti's
    database today, Qdrant/Chroma defaults, ArchiveBox as a dependency.

---

## Sources

### Models and llama.cpp
* llama.cpp function calling — https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md
* llama-server README (response_format, grammar, cache flags, slots, spec flags) — https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
* llama.cpp speculative decoding modes — https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
* GBNF grammars and JSON-schema converter — https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md
* `--cache-ram` / prompt caching explained — https://jessequinn.info/blog/llama-cpp-cache-ram-prompt-caching
* KV-cache quantisation discussion — https://github.com/ggml-org/llama.cpp/discussions/23470
* Reasoning budget / per-request toggle discussions — https://github.com/ggml-org/llama.cpp/discussions/23351 , https://github.com/ggml-org/llama.cpp/discussions/21445
* Lazy-grammar tool-call issue on Qwen3.6 — https://github.com/ggml-org/llama.cpp/issues/24807
* llama-swap README and config — https://github.com/mostlygeek/llama-swap , https://github.com/mostlygeek/llama-swap/blob/main/docs/configuration.md , https://github.com/mostlygeek/llama-swap/blob/main/config.example.yaml
* Qwen3.8 — https://github.com/QwenLM/Qwen3.8 , https://huggingface.co/Qwen/Qwen3.8-27B , https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
* Qwen3.8-27B speed with MTP (5090) — https://kgptalkie.com/tutorials/generative-ai/qwen-3-8-27b-llama-cpp-speed-settings
* Qwen3.6 (MTP flags) — https://unsloth.ai/docs/models/qwen3.6
* Qwen3.5 family and GGUFs — https://unsloth.ai/docs/models/qwen3.5 , https://huggingface.co/unsloth/Qwen3.5-9B-GGUF , https://huggingface.co/unsloth/Qwen3.5-4B-GGUF , https://huggingface.co/unsloth/Qwen3.5-2B-GGUF
* Spec-decoding on a 3090 with Qwen3.6-35B-A3B (no net speed-up) — https://github.com/thc1006/qwen3.6-speculative-decoding-rtx3090
* Gemma 4 GGUFs — https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF , https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF , https://ai.google.dev/gemma/docs/integrations/llamacpp
* Phi-4-mini GGUF — https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF
* Small-model tool-calling comparisons — https://www.ertas.ai/blog/on-device-tool-calling-2026-qwen3-gemma4-phi4 , https://github.com/MikeVeerman/tool-calling-benchmark , https://insiderllm.com/guides/function-calling-local-llms/
* CPU decode vs memory bandwidth — https://dev.to/maximsaplin/ddr5-speed-and-llm-inference-3cdn , https://github.com/ggml-org/llama.cpp/discussions/3167

### Documents
* Docling — https://github.com/docling-project/docling , https://github.com/docling-project/docling-serve , https://hub.docker.com/r/knowledgestack/docling-serve-cpu
* Unstructured — https://github.com/Unstructured-IO/unstructured
* MarkItDown — https://github.com/microsoft/markitdown
* PaddleOCR — https://github.com/PaddlePaddle/PaddleOCR
* RapidOCR — https://pypi.org/project/rapidocr/ , https://rapidai.github.io/RapidOCRDocs/main/model_list/
* docTR — https://github.com/mindee/doctr
* OCRmyPDF — https://github.com/ocrmypdf/OCRmyPDF
* OCR engine comparisons — https://www.codesota.com/ocr/paddleocr-vs-tesseract , https://invoicedataextraction.com/blog/python-ocr-library-comparison-invoices
* Conversion comparisons — https://www.danilchenko.dev/posts/markitdown-vs-docling-vs-marker/ , https://systenics.ai/blog/2025-07-28-pdf-to-markdown-conversion-tools/
* Paperless-ngx — https://github.com/paperless-ngx/paperless-ngx , https://github.com/paperless-ngx/paperless-ngx/blob/dev/docs/api.md
* paperless-gpt — https://github.com/icereed/paperless-gpt

### Web
* Crawl4AI — https://github.com/unclecode/crawl4ai
* Firecrawl self-hosting — https://docs.firecrawl.dev/contributing/self-host , https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md
* changedetection.io — https://github.com/dgtlmoon/changedetection.io , https://github.com/dgtlmoon/changedetection.io/blob/dev/docs/api-spec.yaml , https://changedetection.io/docs/api_v1/index.html
* Miniflux — https://github.com/miniflux/v2 , https://miniflux.app/docs/api.html
* ArchiveBox — https://github.com/ArchiveBox/ArchiveBox
* trafilatura — https://github.com/adbar/trafilatura

### Speech
* faster-whisper turbo on CPU — https://tesseraai.cloud/en/blog/whisper-large-v3-turbo-vs-large-v3-cpu-eu/ , https://runaihome.com/blog/whisper-large-v3-self-hosted-transcription-server-2026/
* wyoming-faster-whisper — https://github.com/rhasspy/wyoming-faster-whisper
* speaches — https://github.com/speaches-ai/speaches
* wyoming_openai bridge — https://github.com/roryeckel/wyoming_openai
* Parakeet-TDT-0.6B-v3 — https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 , https://github.com/achetronic/parakeet , https://k2-fsa.github.io/sherpa/onnx/nemo/index.html
* whisper.cpp stream — https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/README.md
* Vosk — https://github.com/alphacep/vosk-api , https://github.com/rhasspy/wyoming-vosk
* pyannote community-1 — https://huggingface.co/pyannote/speaker-diarization-community-1 , https://www.pyannote.ai/blog/community-1
* Piper (GPL) — https://github.com/OHF-Voice/piper1-gpl , https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/TRAINING.md
* Kokoro-82M — https://huggingface.co/hexgrad/Kokoro-82M
* Chatterbox — https://github.com/resemble-ai/chatterbox
* TTS licence round-up — https://ocdevel.com/blog/20250720-tts , https://localaimaster.com/blog/kokoro-vs-xtts-vs-chatterbox
* openWakeWord — https://github.com/dscripka/openWakeWord (and `docs/wake-word-training.md`)

### Knowledge
* LightRAG — https://github.com/HKUDS/LightRAG
* Graphiti — https://github.com/getzep/graphiti
* mem0 — https://github.com/mem0ai/mem0
* sqlite-vec — https://github.com/asg017/sqlite-vec , https://pypi.org/project/sqlite-vec/
* Chroma telemetry default — https://github.com/chroma-core/docs/blob/main/docs/telemetry.md
* Qwen3-Embedding — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B , https://github.com/QwenLM/Qwen3-Embedding

### Media and home
* Music Assistant — https://github.com/music-assistant/server , https://www.music-assistant.io/api/ , https://www.music-assistant.io/integration/voice/ , https://github.com/music-assistant/client/blob/main/music_assistant_client/player_queues.py
* Navidrome — https://github.com/navidrome/navidrome , https://www.navidrome.org/docs/developers/subsonic-api/
* Jellyfin — https://github.com/jellyfin/jellyfin
* Immich — https://github.com/immich-app/immich , https://docs.immich.app/features/searching/ , https://api.immich.app/endpoints/search/searchSmart
* Nominatim docker — https://github.com/mediagis/nominatim-docker
* OSRM — https://github.com/Project-OSRM/osrm-backend
* Valhalla — https://github.com/valhalla/valhalla
* Open-Meteo self-hosting — https://github.com/open-meteo/open-meteo , https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md , https://open-meteo.com/en/licence
* LibreTranslate / Argos — https://github.com/LibreTranslate/LibreTranslate , https://docs.libretranslate.com/guides/installation/
