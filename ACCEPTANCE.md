# Acceptance — every requirement mapped to a test

Statuses:

* **AUTOMATED-PASS** — a test in this repo passes in a plain dev container
  (no Ollama, no HA, no devices). Run `make test`.
* **NEEDS-MODEL** — test is written and runnable; needs a live Ollama/HA.
* **NEEDS-HARDWARE** — gate procedure documented; must be run on real
  hardware (server GPU, GrapheneOS Pixel, AA head unit/DHU) before the
  Completion Contract is satisfied. This container has none of those, so
  these are **not yet executed** — see `DEVIATIONS.md`.

## New tools → tests (Completion Contract requirement)

| tool | defined in | test | status |
|---|---|---|---|
| `get_user_context` | `ha-config/packages/jarvis/jarvis_context.yaml` | routing mirror test `evals/test_routing.py::test_ha_script_mirrors_the_table`; live via `make smoke`/HA | AUTOMATED-PASS (shape) / NEEDS-MODEL (live) |
| `run_background_task` | `jarvis_background.yaml` | dispatcher round-trip on HA (docs/acceptance P3) | NEEDS-MODEL |
| `delegate_to_agents` | `jarvis_orchestrator.yaml` + `app/fanout.py` | `evals/decomposition_eval.py` | NEEDS-MODEL |
| `code_task` / `apply_code` | `jarvis_orchestrator.yaml` + `app/opencode.py` | `tests/test_api.py::test_code_task_repo_traversal_rejected`, `::test_code_apply_needs_approval_secret` | AUTOMATED-PASS |
| `execute_command` | `jarvis_orchestrator.yaml` + `app/exec_gate.py` | `tests/test_exec_gate.py` (9), `tests/test_api.py` injection gate (7) | AUTOMATED-PASS |
| `*.tool.yaml` (paperless, web_search) | `jarvis_tools/generate_config.py` | `jarvis_tools/tests/test_generate_config.py` (7) | AUTOMATED-PASS |

## Phase gates

| phase | gate | test | status |
|---|---|---|---|
| P0 env audit | scripted stt→tts round trip | `scripts/pipeline-smoke.py` | NEEDS-MODEL (skips w/o HA_TOKEN) |
| P1 web MVP | node smoke + fake-mic → transcript+TTS; latency logged | `tests/web/smoke.test.mjs`, Playwright `e2e.spec.ts` | AUTOMATED-PASS |
| P2 HUD anim + streaming | streaming transcript + chat_log_delta; barge-in; orb states | `pipeline.test.ts` event dispatch; visual states in `Orb.svelte` | AUTOMATED-PASS (logic) / NEEDS-HARDWARE (60fps, visual regression) |
| P3 persona + routing + background | 30-prompt persona eval incl. adversarial; routing table; background round trip | `evals/persona_eval.py`, `evals/test_routing.py`, HA dispatcher | AUTOMATED-PASS (routing, 17) / NEEDS-MODEL (persona, background) |
| P4 wake word | detection test; false-accept <1/hr | server OWW `hey_jarvis`; browser PTT+VAD (`wake.ts`) | NEEDS-HARDWARE |
| P5 android | adb role → ACTION_ASSIST → ≤300ms; round trip; lock-screen | `docs/android.md` gate, `scripts/adb-jarvis-role.sh` | NEEDS-HARDWARE |
| P6 android auto | DHU IoT list; phone "Hey Jarvis" on BT → round trip, TTS on car | `docs/android-auto.md` gate | NEEDS-HARDWARE + OS-CONSTRAINT (see DEVIATIONS) |
| P7 custom tool UX + MCP | add manifest → regenerate → restart → tool exposed; MCP tool appears | `test_generate_config.py`; HA MCP integration | AUTOMATED-PASS (generator) / NEEDS-MODEL (MCP live) |
| P8 orchestration + sandbox | decomposition eval = ship/no-ship; code not run until approved; adversarial command gate + network isolation | `test_exec_gate.py`, `test_api.py`, `decomposition_eval.py`, `scripts/egress-audit.sh` | AUTOMATED-PASS (gate + isolation logic) / NEEDS-MODEL (decomposition) / NEEDS-HARDWARE (live egress audit) |
| P9 security + E2E | ufw; egress audit; confirm gates; recorder purge; CSP; `make test-e2e` | `apply-firewall.sh`, `egress-audit.sh`, retention package, CSP in `svelte.config.js` | AUTOMATED-PASS (offline) / NEEDS-HARDWARE (live) |

## Adversarial gates (hard requirements)

| requirement | test | status |
|---|---|---|
| injected destructive command → no exec without approval | `test_api.py::test_injected_destructive_command_never_runs_without_approval` | PASS |
| prompt shows the TRUE command, not the model's paraphrase | `test_api.py` (verbatim echo) + `test_exec_gate.py::test_command_stored_verbatim` | PASS |
| decline runs nothing | `test_api.py::test_deny_executes_nothing_and_blocks_later_approval` | PASS |
| API token alone cannot approve (needs approval secret) | `test_api.py::test_bearer_token_alone_cannot_approve` | PASS |
| sandbox cannot reach host/LAN (network none) | `scripts/egress-audit.sh` | NEEDS-HARDWARE (verified in compose config; live run needs the stack) |
| persona never softens a Tier-3 gate | `evals/persona_prompts.yaml` adv-01..10 via `persona_eval.py` | NEEDS-MODEL |
| OpenCode diff never run/committed without approval | `app/opencode.py` (apply is separate, gated) + `test_api.py::test_code_apply_needs_approval_secret` | PASS |

## How to run

```
make test           # offline suite: 64 tests (47 python + 17 routing) + web 16 — all green
make test-web       # HUD build + vitest + smoke + playwright
make smoke          # P0, needs HA_TOKEN
make eval-persona   # P3 persona, needs Ollama (BACKEND=ha for the real path)
make eval-decomp    # P8 ship/no-ship, needs Ollama
make egress-audit   # P9, needs the running stack
```
