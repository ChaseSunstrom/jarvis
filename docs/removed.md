# What was removed, and why

Jarvis was built twice. The first generation was a persona, HUD and
orchestration layer riding **on top of** Home Assistant, with HA as the sole
tool-execution hub. The second is `jarvis-core`, which does that job itself.

Both generations lived in this repository at once for a while. That was
useful during the changeover and actively misleading afterwards: two compose
files, two tool mechanisms, two persona prompts that had already drifted
apart, and an Android fork that had been replaced months earlier. This page
records what went, so the deletions are legible rather than mysterious.

Nothing here was working-and-wired. Everything that was orphaned but working
was kept — see [What was kept](#what-was-kept).

## Deleted

### `android/` — the Home Assistant app fork overlay

A set of Kotlin sources, resources and a patch script (`apply-to-fork.sh`,
`overlay/patches/apply.py`) that were copied into a clone of
`home-assistant/android` to produce a Jarvis-branded build of HA's companion
app in its degoogled `minimal` flavour.

**Why:** superseded by `android-app/`, a standalone app (`ai.jarvis.app`)
that speaks the same WebSocket protocol and depends on none of HA's
internals. `android-app/README.md` already described itself as the
replacement. The fork's applicationId
(`io.homeassistant.companion.android.minimal`) and Kotlin package
(`io.homeassistant.companion.android.jarvis.*`) no longer exist anywhere,
which is what made `scripts/adb-jarvis-role.sh` point at nothing.

**Kept from it:** the platform facts that outlived the code — the GrapheneOS
assistant-role reinstall caveat, the ≤300 ms activation target, and the
wake-word battery gate — now in [`android.md`](android.md), which is
otherwise a pointer to `android-app/docs/`.

### `ha-config/` — Home Assistant packages and the old persona prompt

`packages/jarvis/*.yaml` (context/routing script, background tasks, the
orchestrator approval gate, retention) plus
`prompts/jarvis_system_prompt.txt` and a `generated/` output directory.

**Why:** all of it is Home Assistant YAML, and there is no Home Assistant.
Each piece has a live equivalent in `jarvis-core`:

| was | is now |
|---|---|
| `prompts/jarvis_system_prompt.txt` | `jarvis-core/config/prompts/jarvis.txt` |
| `packages/jarvis/jarvis_context.yaml` (routing) | the `guidance` string from `get_user_context` in `jarvis-core/jarvis/llm/tools.py` |
| `packages/jarvis/jarvis_background.yaml` | the `run_background_task` tool |
| `packages/jarvis/jarvis_orchestrator.yaml` (approval gate) | `ToolRegistry`'s tier-3 gate, plus the new `orchestrator` integration |
| `packages/jarvis/jarvis_retention.yaml` | `recorder: purge_keep_days` in `configuration.yaml` |

The persona prompt is worth calling out because it looked like a live file
and was not. `jarvis-core` reads `config/prompts/jarvis.txt` via
`llm: persona_file:`, and that copy had already been revised — it grew rules
about exposure, plain speech and admitting ignorance that the `ha-config`
copy never had. The old file was a stale fork of a live document. It was
deleted rather than merged, and the two places that still read it
(`evals/persona_eval.py`, `evals/test_routing.py`) were repointed at the real
one.

### `jarvis_tools/` — the `.tool.yaml` → Home Assistant config generator

`generate_config.py` turned small `*.tool.yaml` manifests into HA
`rest_command` + `script` definitions and an expose list, with 7 tests.

**Why:** `jarvis-core` reads `*.tool.yaml` manifests **directly**
(`jarvis/llm/tools.py::load_tool_manifests`, wired up by `llm: tools_dir:`),
so the translation step it existed to perform no longer has a target. The
manifest format survived — it is the same one documented in
`jarvis-core/config/tools/example.tool.yaml`.

Its two sample manifests were not ported:

* `searxng_search.tool.yaml` is superseded by the built-in `web_search`
  tool in the `web` integration, which also fences its results.
* `paperless_search.tool.yaml` used `!secret` inside a manifest, which the
  manifest loader deliberately does not support (a tool needing a credential
  belongs in the inline `llm: tools:` block). It was a user-specific example
  with a hardcoded LAN address, not shared functionality.

### The root `docker-compose.yml` as it was

Not deleted, **rewritten**. It used to build the HA-era stack and pointed
`jarvis-web` at `HA_URL`/`HA_TOKEN` on port 8123. It is now the *companion*
stack — the HUD, the orchestrator and the sandbox — pointed at `jarvis-core`
on 8080, complementing `jarvis-core/docker-compose.yml` rather than competing
with it.

## What was kept

### `jarvis-orchestrator/` and `jarvis-sandbox/` — kept, and wired in

These were the interesting case: tested, working, network-isolated, and
reachable by nothing. Multi-agent fan-out, OpenCode coding jobs, and an
approval-gated command broker whose sandbox has no network at all.

Deleting working code because the thing that used to call it went away is how
capability quietly disappears. Moving it to `legacy/` would have been
honest labelling of the same loss. So instead they were **wired in**: a new
`orchestrator` integration in
`jarvis-core/jarvis/integrations/orchestrator/` registers
`delegate_to_agents`, `code_task`, `code_task_status`, `apply_code_task` and
`execute_command`, backed by 37 tests.

This also fixed a real bug rather than merely tidying. The shipped persona
prompt has always told the model that `delegate_to_agents` and `code_task`
exist. Until this integration landed, they did not — the prompt was promising
tools that were never registered.

The security properties were preserved, not re-implemented:

* `execute_command` and `apply_code_task` are tier 3 in `jarvis-core`, so
  their handlers are unreachable from a model turn.
* The orchestrator's own `ExecGate` still enforces its state machine with a
  separate credential in a separate process, so forging a jarvis-core tool
  call is still not enough to execute anything.
* The command is stored verbatim; if the orchestrator echoes back a different
  one, jarvis-core refuses to approve it rather than running something nobody
  saw.
* Agent prose, generated diffs and command stdout are fenced as untrusted
  before the model sees them.

### `evals/` — kept, repointed

The routing table, the persona eval and the decomposition gate all still
describe live behaviour. Three changes:

* `persona_eval.py` reads `jarvis-core/config/prompts/jarvis.txt`, and its
  `--backend ha` became `--backend jarvis` (`JARVIS_URL`/`JARVIS_TOKEN`
  against `/api/conversation/process`).
* `test_routing.py` used to assert that an HA script and the old prompt
  mirrored the routing table. It now asserts against the two things that
  actually reach the model: the `get_user_context` guidance string and rule 4
  of the shipped persona.
* `decomposition_eval.py` was already aimed at the orchestrator's `/delegate`
  and needed nothing.

### `tests/web/` — kept where it is

The mock backend, protocol smoke test and Playwright spec are live:
`jarvis-web/playwright.config.ts` sets `testDir: '../tests/web'`. It looks
like a stray root directory and is not one.

### `scripts/` — kept, corrected

Every script still refers to something that exists.

| script | change |
|---|---|
| `adb-jarvis-role.sh` | rewritten for `ai.jarvis.app`; the two component names live in different sub-packages, which the old single-prefix version got wrong |
| `collect-crash-logs.sh` | dropped the four dead `io.homeassistant.*` candidates |
| `apply-firewall.sh` | port 8123 → 8080; added photon/jarvis-browser/SearXNG on loopback rather than the LAN |
| `pipeline-smoke.py` | `HA_URL`/`HA_TOKEN` → `JARVIS_URL`/`JARVIS_TOKEN`. Kept rather than folded into `e2e-smoke.sh`, which deliberately skips transcription: this is the only check that pushes real audio through `assist_pipeline/run` |
| `egress-audit.sh`, `e2e-smoke.sh` | comment fixes only |

## Known follow-up: CI

`.github/workflows/` is outside the scope this change was allowed to touch,
and it still references two deleted paths. The `legacy-suites` job will fail
until someone with access makes these edits:

```yaml
# .github/workflows/ci.yml

# 1. legacy-suites job — drop jarvis_tools/tests:
- run: python -m pytest jarvis-orchestrator/tests jarvis-sandbox/tests -q

# 2. static job, "Python files compile" — drop jarvis_tools:
- run: |
    python -m compileall -q jarvis-core/jarvis jarvis-desktop jarvis-browser \
      jarvis-orchestrator/app jarvis-sandbox evals scripts \
      android-app/tools
```

Nothing else in CI is affected. The compose-validation step already tolerates
a missing file, and the root `docker-compose.yml` still exists and still
validates.
