# Architecture

Jarvis is a **presentation + persona + orchestration + activation layer** over
Home Assistant's Assist pipeline. HA stays the sole tool-execution hub and the
funnel every client speaks through. This is load-bearing: it means one
exposure model, one place tools live, one audit surface.

```
Browser HUD (SvelteKit) ─┐
GrapheneOS app (Kotlin)  ─┼─► HA assist_pipeline/run (WS) ─► Ollama qwen3:8b (persona+tools)
Car (phone wake word)    ─┘        │
                                   ├─► ~40 existing scripts / ~10 automations
                                   ├─► get_user_context / run_background_task
                                   ├─► delegate_to_agents ─► jarvis-orchestrator
                                   │                          └─► OpenCode ─► Ollama
                                   └─► execute_command ─► jarvis-sandbox (network: none)
                                Wyoming: sherpa STT 10300 · Piper TTS 10200 · OWW 10400
```

Every client is deliberately **dumb**: capture mic → stream to HA → render
events → play TTS. All intelligence stays server-side.

**Rejected alternatives:** LocalAI/Neon/OVOS as the brain (would replace the
existing stack); a generic MCP host driving Ollama directly (bypasses HA's
exposure/permission model); Tasker/WebView phone app (no real ASSIST role,
no lock-screen activation).

## Components

| dir | what | phase |
|---|---|---|
| `jarvis-web/` | SvelteKit HUD; WS proxy keeps HA token server-side; WebGL orb; AudioWorklet capture; TTS proxy; barge-in | P1–P4 |
| `android/` | overlay for a home-assistant/android fork: `jarvis` flavor, `JarvisAssistActivity`, VoiceInteractionService, orb, wake gate | P5–P6 |
| `jarvis_tools/` | `generate_config.py` turns `*.tool.yaml` into `rest_command` + `script` + expose list | P7 |
| `ha-config/` | HA packages: persona prompt, get_user_context, run_background_task, Tier-3 tools + approval gate, retention | P3, P7, P8 |
| `jarvis-orchestrator/` | FastAPI: `/delegate` fan-out, `/code_task` OpenCode, gated `/execute` | P8 |
| `jarvis-sandbox/` | network-less jail; file-queue executor | P8 |
| `evals/` | routing table test, persona eval (30 prompts, 10 adversarial), decomposition ship/no-ship gate | P3, P8 |
| `scripts/` | firewall, egress audit, P0 pipeline smoke, adb role | P0, P9 |

## The pipeline contract (what the clients speak)

`assist_pipeline/run` over the HA WebSocket API. Key points every client
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
2. **Tier 2** — `run_background_task`: fire an HA event, dispatcher runs the
   job outside the turn, reports back via the routing policy. The turn
   returns immediately.
3. **Tier 3** — `delegate_to_agents` (orchestrator fan-out) and `code_task`
   (OpenCode). Aspirational at 8B; gated behind `evals/decomposition_eval.py`.
   If that eval fails on your model, ship Tier 2 and document it in
   `DEVIATIONS.md`.

## Tools

Three ways to add a tool, easiest first:

1. **`*.tool.yaml` manifest** → `generate_config.py` (see `jarvis_tools/`).
2. **MCP** — add HA's Model Context Protocol integration pointing at an MCP
   server; its tools appear to the agent automatically. stdio servers via
   `mcp-proxy`.
3. **Hand-written HA script** exposed to Assist, for anything bespoke.

The HUD's Tools page lists generated tools and lets you toggle exposure and
test-run them.
