# Deviations & constraints

Where this build knowingly differs from what was planned, where a thing is
genuinely impossible and what is done instead, and what was never run here.

For the per-capability claims register — what is proven, by which command —
see [`docs/verification.md`](docs/verification.md). This file is for the
judgement calls behind it.

## 1. Hardware-gated tests were not executed in this environment

This repository was built in a cloud container with **no GPU, no Ollama, no
GrapheneOS Pixel, no Android Auto head unit or DHU**, and no Docker daemon.

Everything verifiable without those is verified and green. Everything that
needs the missing hardware is written, wired and documented with an exact run
procedure, and is marked **Manual** or **Unproven** in
`docs/verification.md`. It has not been run here, and no row claims otherwise.

To close the remaining gaps on your own kit:

```bash
make smoke              # boots a throwaway jarvis-core against your services
make pipeline-smoke     # full stt->tts audio round trip through Wyoming
make test-web           # HUD build + unit + Playwright
make eval-persona BACKEND=jarvis
make eval-decomp        # ship/no-ship for tier-3 delegation
make egress-audit       # sandbox isolation, against the running stack
```

Then the device procedures in `docs/android.md` and `docs/android-auto.md`.

## 2. Android Auto in-car voice — a genuine OS impossibility

A third-party app **cannot** be the Android Auto voice button. Only Google's
assistant can, and Google removed Assistant from AA in March 2026 leaving
Gemini only. There is no "voice assistant" Car App category for third
parties, and the head-unit mic is routed to the AA stack while connected.

**The fallback, implemented:** phone-side "Hey Jarvis" runs in parallel while
AA is connected — the mic is the phone's, TTS routes out over the car's
Bluetooth link, nothing renders on the head unit. The car-BT wake policy
(`WakeWordGate`) turns detection on for the drive and off afterwards. Full
write-up in [`docs/android-auto.md`](docs/android-auto.md).

## 3. Tier-3 multi-agent quality at 8B is aspirational

`delegate_to_agents` and `code_task` are the weakest links on an 8B planner.
Rather than assert quality that cannot be verified here, the ship decision is
a deterministic gate: `evals/decomposition_eval.py` (5 three-part requests,
keyword-coverage scoring, ≥60% to ship tier 3). **Run it on your model.**

If it fails:

* leave the orchestrator running — `code_task` and the command broker are
  independent of decomposition quality;
* drop `delegate_to_agents` from what the model can see, by excluding it in
  the `llm: expose:` block;
* tiers 1 and 2 ship regardless and are reliable.

Coder quality scales with the model: `CODER_MODEL` defaults to
`qwen2.5-coder:7b` and can be raised to `:14b`/`:32b` on a GPU.

## 4. Persona wit is aspirational; tone is not

8B gives reliable Sir/ma'am tone but not screenwriter-sharp wit. The persona
eval separates these: tone and routing cases gate (≥80%, and all 10
adversarial cases must pass), the 5 wit cases are scored and reported but
never gate. A LoRA or a larger model improves wit later; never train on
copyrighted scripts.

## 5. Credentials cannot be spliced into a `*.tool.yaml` manifest

Manifests in `config/tools/` are read with a plain YAML parser, so `!secret`
and `!env_var` do not work there — a half-resolved credential in a URL is a
worse failure than an honest one. A tool that needs a credential belongs in
the inline `llm: tools:` block in `configuration.yaml`, which is loaded with
the full config loader. This is documented at the point of use, in
`jarvis-core/config/tools/example.tool.yaml`.

## 6. OpenCode may be absent from the orchestrator image

If `opencode` fails to install (network policy, version pin), `code_task`
returns a clear "opencode binary not installed" error rather than pretending
to work. Alternatives (Aider, Continue) drop in by replacing `build_command`
in `jarvis-orchestrator/app/opencode.py`.

Likewise, if `apt-get` was unreachable during `docker compose build`, the
Dockerfiles treat the package step as best-effort and log a `WARN`. The
orchestrator API and the sandbox work regardless; `code_task` needs `git` and
will not.

## 7. The orchestrator and sandbox are opt-in, and start disabled

They are commented out in `jarvis-core/docker-compose.yml` and their
credentials default to empty. jarvis-core registers `delegate_to_agents`,
`code_task` and `execute_command` regardless, so the model is told the truth
about its toolbox, and they return "not configured" until you set
`ORCHESTRATOR_TOKEN` and `APPROVAL_SECRET` and start the services.

Those two values must **differ**. If they match, holding the API token is
enough to execute a command, which defeats the entire two-secret design;
jarvis-core logs an error at startup when it sees them equal, but it cannot
refuse on your behalf.

## 8. What the on-device Kotlin policy engine proves, and where

The Android policy engine, geofence, schedule maths and screen pruning are
mirrored by pure-Python executable specs in `android-app/tools/`, which CI
runs. The Kotlin itself is **not compiled** in this environment — there is no
Android SDK here. So "the tier system is enforced on the device" is proven in
the mirror and unproven in the shipped binary. `docs/verification.md` says so
in the row where it matters.

## Licensing notes

* Piper was archived Oct 2025 → OHF-Voice/piper1-gpl (GPL-3.0; MIT→GPL).
* openWakeWord code is Apache-2.0; the official models are CC BY-NC-SA 4.0
  and English-only (personal use fine). Train your own custom wake words and
  do not redistribute the NC models. See
  [`docs/wake-word-training.md`](docs/wake-word-training.md).
* microWakeWord on Android is experimental and battery-heavy — third-party
  apps get no low-power DSP path, which is why the wake gate exists.
