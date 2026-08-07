# Jarvis

A fully self-hosted, private, cinematic AI assistant that rides **on top of**
an existing Home Assistant + Ollama + Wyoming + SearXNG stack — it doesn't
replace it. Nothing goes to the cloud at runtime. A browser HUD, a
GrapheneOS phone app, and an in-car wake path all speak to HA's Assist
pipeline; HA stays the single tool-execution hub with an 8B local model
wearing a dry-witted British-butler persona.

End-user experience, by design: **one `docker compose up -d`** for the
server, **one Obtainium APK** for the phone.

```
Browser HUD ─┐
Phone app    ─┼─► HA assist_pipeline/run ─► Ollama qwen3:8b (persona + tools)
Car wake     ─┘         ├─ existing scripts/automations
                        ├─ get_user_context · run_background_task
                        ├─ delegate_to_agents ─► orchestrator ─► OpenCode
                        └─ execute_command    ─► sandbox (network: none, approval-gated)
```

See `docs/architecture.md` for the full picture and why HA-as-hub is
load-bearing.

## Layout

| dir | what |
|---|---|
| `jarvis-web/` | SvelteKit HUD — WebGL orb, mic capture, streaming, barge-in; HA token stays server-side |
| `android/` | overlay for a home-assistant/android fork: `jarvis` flavor, ASSIST activity, wake gate |
| `jarvis_tools/` | `generate_config.py` — custom tools from <10-line `*.tool.yaml` |
| `ha-config/` | HA packages: persona prompt, context/routing, Tier-3 tools + approval gate, retention |
| `jarvis-orchestrator/` | FastAPI: agent fan-out, OpenCode coding, approval-gated command broker |
| `jarvis-sandbox/` | network-less execution jail |
| `evals/` | routing table, 30-prompt persona eval (10 adversarial), decomposition ship/no-ship gate |
| `scripts/` | firewall, egress audit, P0 pipeline smoke, adb role |
| `docs/` | architecture, security, acceptance, android, android-auto, wake-word training |

## Quick start (server)

```bash
cp .env.example .env            # fill HA_TOKEN, ORCHESTRATOR_TOKEN, APPROVAL_SECRET
python3 jarvis_tools/generate_config.py --secrets /config/secrets.yaml
# copy ha-config/packages/jarvis + generated/jarvis_tools.yaml into /config,
# paste the persona prompt into your Ollama agent, create the "Jarvis"
# pipeline — see ha-config/README.md
docker compose up -d --build
```

Then the HUD is at `http://<server>:8199` (over WireGuard/LAN). Phone build:
`docs/android.md`.

## Tests

```bash
make test          # offline suite — 64 python tests + HUD 16 unit + smoke + Playwright
make help          # all targets
```

What's green now, what needs your hardware, and every tool mapped to a test:
**`ACCEPTANCE.md`**. Honest constraints (Android Auto voice, 8B Tier-3
limits) and what wasn't run in this build environment: **`DEVIATIONS.md`**.

## Security

Untrusted web/camera text sits next to unlock/SMS/shell/code-exec, so the
model is kept out of the loop for anything dangerous: tiered tools, human
approval gates enforced **outside** the model (plain HA YAML + orchestrator
HTTP, verified by adversarial tests), a network-less sandbox, LAN/WireGuard
only, nightly purge. Full model: `docs/security.md`.
