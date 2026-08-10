# Security & privacy model

Jarvis puts a tool-rich LLM with dangerous actuators (unlock, messaging,
shell, code-exec) next to untrusted input (web results, camera frames, MQTT
payloads, screen text, documents). The whole design assumes the model can be
tricked, and keeps the model out of the loop for anything that can hurt you.

This is the repo-level model. `jarvis-core/docs/security.md` is the detailed
one for the assistant itself — tokens, the API surface, what lands on disk.

## Threat model

* **Prompt injection** via web results, fetched pages, camera frames, MQTT
  payloads, notification text, or the user parroting an injected string.
  Mitigation: everything from outside is *fenced* as data before the model
  sees it (`jarvis-core/jarvis/integrations/web/fence.py`), the persona says
  untrusted text is never instructions, and — the part that actually holds —
  no dispatcher can be reached from fenced content without a fresh human
  approval. Wording is not the control; the tier is.
* **A compromised or hallucinating model** calling `execute_command` or
  applying a diff. Mitigation: the approval decision is made in code, outside
  the model, in two independent places (below). Persona wording cannot reach
  it. See `jarvis-core/jarvis/llm/tools.py::ToolRegistry.requires_approval`
  and `jarvis-orchestrator/app/exec_gate.py`, both with adversarial tests.
* **Blast radius if a gate ever failed.** The sandbox has no network, no host
  mounts, no Docker socket, no capabilities, no secrets, a read-only rootfs
  and tight resource limits. The only writable crossover is
  `./jarvis-workspace`, which holds nothing sensitive.

## Tiers

Policy is enforced on the device, outside the model. A server may only ever
**raise** a tier, never lower one.

| tier | examples | gate |
|---|---|---|
| 1 direct | lights, covers, climate, `get_user_context`, `web_search`, `web_fetch`, tier-1 `*.tool.yaml` | none (safe / idempotent) |
| 2 background | `run_background_task`, `delegate_to_agents`, `code_task` | none, but runs outside the turn and reports back |
| 3 approval | unlock, notify, `execute_command`, `apply_code_task`, tier-3 `*.tool.yaml` | **human approval, enforced in code** |

Two things escalate automatically, and neither consults the model:

* A tool whose resolved targets land in `GATED_DOMAINS` (`lock`, `notify`)
  becomes tier 3 no matter what tier it was declared at.
* A `web_browse` batch that would click or type — as opposed to read — is
  held, and `jarvis-browser` gates the sensitive subset again with its own
  separate secret.

A held action returns `approval_required` and a request id, and that is the
end of the model's turn. It cannot retry, rephrase or self-approve. The
arguments are **pinned** when the request is created: a fuzzy target ("the
front door") is resolved to concrete entity ids *before* a human sees it, so
what runs later is what was shown, not a re-resolution against a house that
has moved on. Requests expire, and each can be used at most once.

## The command path has two gates, with different credentials

`execute_command` and `apply_code_task` are the sharpest tools in the box, so
they pass two independent checks in two processes:

1. **In jarvis-core.** The tool is `TIER_APPROVAL`. The registry holds it and
   fires `jarvis_approval_required`; the handler is unreachable from a model
   turn.
2. **In jarvis-orchestrator.** `ExecGate` is a state machine keyed on
   `APPROVAL_SECRET`. Nothing reaches the sandbox before it says approved, the
   stored command is verbatim what was requested, and a request can be
   approved at most once.

Two secrets, deliberately distinct: `ORCHESTRATOR_TOKEN` authenticates every
call, `APPROVAL_SECRET` is sent on exactly two request paths and only after a
human approved that exact action. **Possession of the API token alone cannot
execute anything** — that is the point of the split, it is tested in
`jarvis-orchestrator/tests/test_api.py` and
`jarvis-core/tests/test_orchestrator.py`, and jarvis-core logs an error at
startup if you set both to the same value.

jarvis-core also refuses to approve a command the orchestrator echoes back
differently from the one that was approved: if the stored copy does not match
byte for byte, nobody saw what would actually run, so it stops.

## Everything that comes back is untrusted too

Search results, fetched pages, crawled pages, specialist-agent prose,
generated diffs and command stdout are all wrapped in
`<untrusted_web_content>` markers with a notice before the model sees them.
Content cannot close its own fence — the markers are escaped inside the body.
"Another model wrote it" is not a trust boundary: a delegated agent that read
a poisoned page must not be able to smuggle an instruction back.

The markers are the half the model sees. The half that holds is
`mark_untrusted_result`, which taints the *turn*: once a turn has read
somebody else's words, no dispatcher can be reached from it without a human.
Any tool that returns bytes from a machine that is not this one must call it —
including YAML- and console-defined tools, which fetch an arbitrary HTTP
endpoint. A `note` on the result asking the model to treat the body as data is
worth saying and is **not** a control, because a hostile endpoint's reply is
exactly the text that talks a model out of following a note. Error paths count:
a 500 whose body is the remote server's words is as much an injection vector
as a 200, and is cheaper to return.

## Isolation matrix

| service | network | user | rootfs | mounts | caps |
|---|---|---|---|---|---|
| jarvis-core | host (:8080), LAN/WG via ufw | non-root | rw (image) | `./config` | default |
| jarvis-web | host (:8199), LAN/WG via ufw | node | rw (image) | none | default |
| jarvis-browser | host (:8210), loopback via ufw | non-root | rw (image) | none | default |
| jarvis-orchestrator | host (:8188), core host only via ufw | 10002 | read-only | `./jarvis-workspace` | drop ALL |
| jarvis-sandbox | **none** | 10001 | read-only | `./jarvis-workspace` | drop ALL |

`network_mode: host` is used so the `127.0.0.1` defaults reach jarvis-core
(8080) and Ollama (11434) on the same host, and so ufw — not Docker's DNAT,
which can bypass ufw — is the single authority on who may reach a port.

**`jarvis-sandbox` must never get host or LAN networking.** It executes
LLM-proposed commands; `network_mode: none` and the workspace file-queue are
its containment. This is asserted by `scripts/egress-audit.sh`, by the compose
config, and by `jarvis-core/tests/test_packaging.py`, which fails if the
commented service in `jarvis-core/docker-compose.yml` ever loses it.
`no-new-privileges` on both isolated services; sandbox `mem_limit 1g`,
`pids_limit 128`, `cpus 2`, tmpfs `/tmp`.

## Outbound audit → zero cloud at runtime

Intended egress at runtime: **only** SearXNG's upstream fetch (SearXNG makes
it itself) and whatever pages you ask `jarvis-browser` to fetch. Everything
else is local:

* HF/model downloads — setup only, then block egress.
* Container registries — pin image digests, pull at deploy only.
* Photon geocoder index — built manually, offline after.
* Ollama / Piper / Whisper / openWakeWord / orchestrator / sandbox — never
  talk to the internet; firewalled.

There is no cloud fallback anywhere. If SearXNG is down, `web_search` fails
and says so; it does not quietly ask somebody's search API instead.

Run `scripts/egress-audit.sh` against the live stack: it proves the sandbox
has only `lo` and cannot reach the LAN gateway or the internet, and flags any
orchestrator egress you did not intend.

## Firewall

`scripts/apply-firewall.sh` (ufw) — deny incoming, allow `wg0`, allow the LAN
to the jarvis-core/Ollama/Wyoming/HUD ports, keep photon, jarvis-browser and
SearXNG on loopback, allow only the jarvis-core host to the orchestrator, and
(post-setup) deny outgoing except the SearXNG path and `wg0`. `DRY_RUN=1`
previews. Transport is LAN/WireGuard only, no port forward.

## Retention

`jarvis-core/config/configuration.yaml` sets `recorder: purge_keep_days` (10
by default) with `auto_purge`, and excludes high-frequency sensor noise and
`call_service` events from the database entirely. No raw audio is stored by
the voice pipeline — transcripts and in-memory traces only.

`jarvis.db` is the sensitive artefact and it is easy to underestimate: it
knows when you get up, when the house is empty, and when the front door
opened. Treat a backup of `/config` as a copy of your keys — encrypt it, and
keep `purge_keep_days` no longer than you actually need.

## Updates

Pin image digests in a deploy overlay; stage changes; keep the previous digest
for a documented one-command rollback (`docker compose up -d` with the old
digest). OpenCode is pinned by `OPENCODE_VERSION` in its Dockerfile.
