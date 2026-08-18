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
| 3 approval | unlock, notify, `execute_command`, `apply_code_task`, `write_file`, `start_coding_job`, tier-3 `*.tool.yaml` | **human approval, enforced in code** |

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

## Pairing needs a second secret, for the same reason

`POST /api/pair/claim` is the **only unauthenticated write in the API**, and it
has to be: the phone has no credential yet, which is the entire problem being
solved. It exchanges a short-lived code for a real token.

The obvious design — put the token in the QR — is worse than typing it by hand.
A QR on a screen is photographable from across a room, ends up in whatever
screenshot or shared window captured it, and a token in one stays valid
indefinitely. So the QR carries a **code**: 192 bits from
`secrets.token_urlsafe(24)`, five minutes, single use, removed before the token
is minted so two devices racing the same photograph produce exactly one token.
Failed claims are counted **per caller** — a global counter would let anybody
who can reach the endpoint lock the household out of pairing — and a claim
carrying an `Origin` header is refused outright, because browsers always send
one on a cross-origin POST and phones never do.

Minting a code needs `JARVIS_PAIRING_SECRET` **as well as** the API token, and
that is not belt and braces. jarvis-web's relay attaches the server-held admin
token to whatever connects, and its origin guard deliberately admits a client
that sends no `Origin`, because that is what a non-browser client looks like.
So a script with nothing but transient reach to the console's port is already
an authenticated API client — and with minting gated on the API token alone it
could mint a code, claim it, and walk away with a permanent token. Reach for as
long as the script runs, converted into access forever.

The same split as the orchestrator's, then: the relay never holds this secret.
It is typed into the pairing panel and forwarded. Unset means pairing is off
and every surface says so.

**Un-pairing is the half that makes pairing safe to offer.** Revoking a token
closes every socket authenticated with it, and tells them why, rather than
waiting for a reconnect that may be days away. The console's list is built from
the auth manager rather than from any pairing record: a token store that failed
to load would otherwise render as "no devices" over a live full-privilege
credential, with no way to see it and nothing to revoke.

## A question is shown to a person, so it says where its words came from

The tier system answers "may this run without a human". It cannot answer
"should the human believe what is on the screen", and those are different
questions for exactly one tool.

A held **action** displays pinned entity ids — resolved server-side when the
request was raised — which injected text cannot forge. A held **question**
(`ask_user`) displays the model's own sentence, so a turn that has read a
hostile page can put *"confirm your bank password"* in front of somebody in
Jarvis's voice, on the same consent surface they trust for everything else.

Every approval request therefore carries `tainted`, read from the same
`UntrustedTurns` store that already raises the tier for device commands. The
console draws a warning; the phone has no field for provenance, so it goes in
the words. Nothing is refused — a turn that read a page and needs to ask which
of three results was meant is the legitimate case, and an attacker who is
refused simply rephrases. Marking is the control that survives that.

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

## The relay is a door, and it now asks who you are

`jarvis-web` holds the admin token so the browser never does, and relays the
console's WebSocket to jarvis-core at `/ws`. Authorisation for that socket was
a same-origin check — correct as far as it goes, because only a browser sends
`Origin` and only a browser can be tricked into a cross-origin request.

The gap was what happened when there was no `Origin` at all. `isOriginAllowed`
returns true for a missing one, so **anything that was not a browser — the
phone, curl, any script that could reach port 8199 — was handed the admin
token's full control of the house without presenting anything**, while
jarvis-core next door required a bearer token for exactly the same power. The
firewall was the only thing in the way, which makes "LAN" a trust boundary the
rest of this document does not otherwise grant.

Now the socket asks:

| client | brings | relay does |
| --- | --- | --- |
| browser console | same-origin `Origin`, no token (it cannot set headers) | injects the admin token, swallows the handshake — unchanged |
| Jarvis app, scripts | `Authorization: Bearer <jarvis-core token>` | passes the token through; **jarvis-core** decides |
| anything else | neither | `401`, before a socket exists |
| a page on another origin | a foreign `Origin` | `403`, as before |

Pass-through matters as much as the refusal. The relay does not vouch for a
client it cannot authenticate; it forwards the credential and lets jarvis-core
remain the single authority on tokens. A revoked token stops working
everywhere at once, which would not be true if the relay kept minting access
of its own.

It is also what lets one URL do everything: with the handshake identical on
both, the app can be pointed at the console and reach voice, management and
TTS through it, instead of needing to know which of two servers is which.

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
