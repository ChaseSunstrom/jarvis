# Architecture

Jarvis is a self-hosted assistant that runs the house itself. `jarvis-core` is
the hub: automation engine, entity registry, voice pipeline, LLM agent and
tool layer in one process. Every client is deliberately **dumb** — capture
mic, stream to core, render events, play TTS. All intelligence stays
server-side, which is why there is one exposure model, one place tools live,
and one audit surface.

```
Browser HUD (SvelteKit) ─┐
Android app (Kotlin)     ─┼─► jarvis-core ─► Ollama qwen3:8b (persona + tools)
Desktop agent (Python)   ─┘        │
                                   ├─► entities, automations, scenes, scripts
                                   ├─► get_user_context / run_background_task
                                   ├─► web_search / web_fetch ─► SearXNG, jarvis-browser
                                   ├─► deep_research ─► a task, several searches, cited
                                   ├─► delegate_to_agents ─► jarvis-orchestrator
                                   │                          └─► OpenCode ─► Ollama
                                   └─► execute_command ─► jarvis-sandbox (network: none)
                            Wyoming: whisper STT 10300 · Piper TTS 10200 · OWW 10400
```

Jarvis used to ride on top of Home Assistant, with HA as the tool-execution
hub. `jarvis-core` replaced it. The HA-era pieces are gone; see
[`removed.md`](removed.md) for what went and why. The client contract did not
change, because `jarvis-core` implements the same REST/WebSocket/pipeline API
HA does — see [`standalone.md`](standalone.md).

**Rejected alternatives:** LocalAI/Neon/OVOS as the brain; a generic MCP host
driving Ollama directly (bypasses the exposure/permission model); a
Tasker/WebView phone app (no real ASSIST role, no lock-screen activation).

## Components

| dir | what |
|---|---|
| `jarvis-core/` | the assistant: entities, automations, voice, the LLM agent, the tool registry and the approval gate |
| `jarvis-web/` | SvelteKit HUD; server-side proxy keeps the token off the client; WebGL orb; AudioWorklet capture; barge-in |
| `android-app/` | standalone Android app (`ai.jarvis.app`): ASSIST role, lock-screen activation, wake word, companion channel |
| `jarvis-desktop/` | desktop agent — screen/context awareness and the desktop half of the companion channel |
| `jarvis-browser/` | fetching, crawling and gated browser automation, behind its own token and its own approval secret |
| `jarvis-orchestrator/` | FastAPI: `/delegate` fan-out, `/code_task` OpenCode, gated `/execute` |
| `jarvis-sandbox/` | network-less jail; file-queue executor |
| `evals/` | routing table and its mirrors, persona eval, decomposition ship/no-ship gate |
| `scripts/` | firewall, egress audit, e2e smoke, audio pipeline smoke, adb assistant role |
| `tests/web/` | the mock backend and Playwright e2e the HUD runs against |

## The pipeline contract (what the clients speak)

`assist_pipeline/run` over the WebSocket API. Key points every client
implements identically (see `jarvis-web/src/lib/pipeline.ts` and
`scripts/pipeline-smoke.py`):

* auth handshake (`auth_required` → `auth` → `auth_ok`);
* `assist_pipeline/pipeline/list` to resolve the **Jarvis** pipeline id;
* `assist_pipeline/run` with `start_stage: stt, end_stage: tts`;
* on `run-start`, grab `runner_data.stt_binary_handler_id`;
* audio framing: each binary frame = 1 byte (handler id) + Int16LE PCM;
  a lone handler-id byte signals end-of-audio;
* render `stt-end` (final transcript), stream `intent-progress`
  `chat_log_delta.content`, on `tts-end` fetch `tts_output.url` and play;
* keep `conversation_id` from `intent-end` for multi-turn continuity.

## Tiers of parallelism

1. **Tier 1** — multiple tool calls per turn (native to the 8B agent, free).
2. **Tier 2** — `run_background_task`: fires an event, the job runs outside
   the turn and reports back through the routing policy. The turn returns
   immediately.
3. **Tier 3** — `delegate_to_agents` (orchestrator fan-out) and `code_task`
   (OpenCode). Aspirational at 8B; gated behind
   `evals/decomposition_eval.py`. If that eval fails on your model, ship Tier
   2 and document it in `DEVIATIONS.md`.

Note the naming collision, because it has bitten before: these
*parallelism* tiers are not the *safety* tiers in
[`security.md`](security.md). `delegate_to_agents` is parallelism Tier 3 and
safety tier 2 — it spawns agents, but it cannot touch anything. Only
`execute_command` and `apply_code_task` are safety tier 3.

## Tools

Three ways to add a tool, easiest first:

1. **`*.tool.yaml` manifest** in `jarvis-core/config/tools/` — an HTTP call
   defined entirely in YAML, no Python. `config/tools/example.tool.yaml`
   documents every field, including how to pick a tier.
2. **An inline `llm: tools:` block** in `configuration.yaml`, which is loaded
   with the full config loader and so can reference `secrets.yaml`.
3. **A Python integration** under `jarvis-core/jarvis/integrations/<name>/`
   that registers services and, optionally, tools. See
   `jarvis-core/docs/integrations.md`.

A script or scene that carries metadata becomes an LLM tool automatically.
