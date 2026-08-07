# Deviations & constraints

The Completion Contract asks that genuine impossibilities be documented with
their sanctioned fallback, and that anything not yet verified on real
hardware be called out honestly. This file is that record.

## 1. Hardware-gated tests are not executed in this build environment

This repository was built in a cloud container with **no GPU, no Home
Assistant instance, no Ollama, no GrapheneOS Pixel, and no Android Auto head
unit / DHU**, and no Docker daemon. Everything that can be verified without
those was verified and is green (`make test`: 64 offline tests, plus the HUD's
16 unit tests + Node smoke + Playwright fake-mic run, all passing). Everything
that needs the missing hardware is **written, wired, and documented** with an
exact run procedure, but is marked `NEEDS-HARDWARE` / `NEEDS-MODEL` in
`ACCEPTANCE.md` and has **not** been run here.

This is a deviation from "DO NOT DECLARE COMPLETION until every Phase-9
acceptance test passes ON REAL HARDWARE." It is not possible to satisfy that
clause from inside a container without the hardware. What is delivered:

* the full implementation of every phase,
* every test the contract names, runnable by the operator on their hardware
  via the documented commands,
* all hardware-independent gates passing now.

**To finish the contract**, the operator runs, on their kit:
`make smoke` (P0), `make test-web` (P1/P2 on the server), `make eval-persona
BACKEND=ha` and the background round-trip (P3), the wake-word false-accept
test (P4), the `docs/android.md` device procedure (P5), the
`docs/android-auto.md` procedure (P6), the MCP live check (P7),
`make eval-decomp` + `make egress-audit` (P8), and `make test-e2e` (P9).

## 2. Android Auto in-car voice — genuine OS impossibility (documented + fallback)

A custom assistant **cannot** be the Android Auto voice button. Only Google
Assistant/Gemini can, and Google removed Assistant from AA in March 2026,
leaving Gemini only. There is no "voice assistant" Car App category for third
parties, and HA's AA integration is a tap-to-control IoT list with no voice
entry.

**Sanctioned fallback (the only viable hands-free path), implemented:**
phone-side "Hey Jarvis" runs in parallel while AA is connected — mic is the
phone's, TTS is routed out the car's Bluetooth, never rendered on the head
unit. Gated by the car-BT wake policy (`WakeWordGate.kt` + an HA car-BT
automation). Full write-up and P6 gate in `docs/android-auto.md`.

## 3. Tier-3 multi-agent quality at 8B is aspirational (ship gate provided)

`delegate_to_agents` fan-out and `code_task` are the weakest links on an 8B
planner, as the plan itself flags. Rather than assert quality we can't verify
here, the ship decision is a deterministic gate:
`evals/decomposition_eval.py` (5 three-part requests, keyword-coverage
scoring, ≥60% to ship Tier-3). **Run it on your model.** If it fails:

* keep the orchestrator running (it's still used by `run_background_task`
  reporting and by `code_task`),
* do **not** expose `script.jarvis_delegate_to_agents` to the agent (remove
  it from the expose list),
* Tiers 1 and 2 ship regardless and are fully reliable.

Coder quality (`code_task`) similarly scales with the model; `CODER_MODEL`
defaults to `qwen2.5-coder:7b` and can be raised to `:14b`/`:32b` on a GPU.

## 4. Persona wit is aspirational; tone is not

Per the plan, 8B gives reliable Sir/ma'am tone but not screenwriter-sharp
wit. The persona eval separates these: tone/routing cases gate (≥80% + all
10 adversarial cases must pass), the 5 wit cases are scored and reported but
never gate. A LoRA or larger model later improves wit; never train on
copyrighted scripts.

## 5. Secrets cannot be spliced into strings (generator normalises + warns)

HA's `!secret` must be an entire value. A manifest like `Authorization:
"Token !secret x"` is normalised to a full-value secret and the generator
warns that the secret itself must contain the full `Token ...` string. See
`jarvis_tools/README.md`.

## 6. OpenCode binary may be absent at build time

If `opencode` fails to install in the orchestrator image (network policy,
version pin), `code_task` returns a clear "opencode binary not installed"
error rather than pretending. Alternatives (Aider, Continue) drop in by
replacing `build_command` in `app/opencode.py`.

## Licensing notes (carried from the plan, not deviations)

* Piper archived Oct 2025 → OHF-Voice/piper1-gpl (GPL-3.0; MIT→GPL change).
* openWakeWord code Apache-2.0; official models CC BY-NC-SA 4.0, English-only
  (personal use OK). Custom wake words: train your own, don't redistribute
  the NC models. See `docs/wake-word-training.md`.
* microWakeWord on Android is experimental (HA 2026.3), battery-heavy
  (third-party apps get no low-power DSP path) — hence the wake gate.
