# Security

Jarvis Core puts a language model next to the things that unlock your front
door. The design assumes the model can be tricked — by a web result, by text in
a document, by a camera reading a sign, by the user repeating something they
were sent — and keeps it structurally unable to do the dangerous things on its
own. Persona wording is not a control. Code is.

Three separable questions, in order of how much they matter:

1. Who can reach the API at all (network + token).
2. What the model can see and touch (exposure).
3. What nobody can do without a human saying yes (the gate).

## 1. Network

**LAN or WireGuard only. Never port-forward Jarvis.**

There is no TLS, no rate limiting, no account lockout and no audit of failed
attempts, because there is meant to be a network boundary in front of all of
it. If you want access from outside the house, that is what WireGuard is for —
one UDP port, key-based, no login surface.

Every service in `docker-compose.yml` uses `network_mode: host`, which is
deliberate on two counts. Jarvis needs loopback to the Wyoming containers and
Ollama, and MQTT/mDNS discovery needs the LAN broadcast domain. It also keeps
`ufw` as the single authority on who can reach a port: Docker's own port
publishing writes DNAT rules that bypass ufw, which is a trap people fall into
exactly once.

Roughly what belongs in the firewall:

```bash
ufw default deny incoming
ufw allow in on wg0 to any port 8080 proto tcp    # WireGuard clients
ufw allow from 192.168.1.0/24 to any port 8080 proto tcp
ufw deny 11434                                     # Ollama has NO auth
ufw deny 10200,10300,10400/tcp                     # Wyoming has no auth either
```

`scripts/apply-firewall.sh` in the parent repo does this properly. Note the
last two lines: **Ollama and the Wyoming services have no authentication at
all**. Anything that can reach 11434 can run arbitrary prompts on your GPU and
read whatever the model has been told. Keep them on loopback.

`OLLAMA_HOST=127.0.0.1:11434`, never `0.0.0.0`.

## 2. Tokens

Long-lived bearer tokens are the whole authentication story. No user accounts,
no login form, no sessions, no cookies.

- 256 bits from `secrets.token_urlsafe`, shown exactly once when created.
- Stored as a SHA-256 digest in `<config>/.storage/auth.json`. A stolen backup
  gives up no working token.
- Compared with `hmac.compare_digest`, and verification walks every stored
  token instead of stopping at the first match, so timing reveals nothing
  about which token matched — or whether one did.
- `JARVIS_TOKEN` in the environment is always accepted, on top of the store.

```bash
# first start prints one in the log; after that:
docker compose exec jarvis-core python -m jarvis --config /config --create-token phone
curl -s localhost:8080/api/states -H "Authorization: Bearer $TOKEN"
```

One token per device, so revoking a lost phone does not lock out the car.
Revoke by deleting the entry from `.storage/auth.json` and restarting;
`GET /api/auth/tokens` lists them (metadata only — the secrets are gone).

Three routes are deliberately open, and each one's URL *is* its credential:

| Route | Why |
|---|---|
| `GET /healthz` | Liveness. Reports nothing about the house. |
| `GET /api/tts_proxy/{token}.wav` | The token is single-use and random, so a satellite can play an answer without holding a long-lived secret. |
| `ANY /api/webhook/{id}` | The id is long and random. This is how a doorbell or a phone shortcut posts without carrying a token. Set `jarvis: webhook_require_auth: true` to demand a bearer token as well. |

Everything else is 401 without a valid token, including reads. There is no
anonymous mode.

CORS defaults to `*`, which is fine when the API is unreachable from the
internet and every request needs a bearer token anyway — but narrow
`cors_allowed_origins` to your HUD's origin once you know it.

## 3. Exposure — what the model can see

```yaml
llm:
  expose:
    domains: [light, switch, cover, climate, media_player]
    entities: [sensor.outside_temperature]
    exclude_entities: [switch.coffee_machine]
```

Anything not exposed is invisible to **every** tool, including read-only ones.
The model cannot list it, cannot read its state, and cannot name it in a
service call. This is the blast radius; set it deliberately rather than
inheriting the default.

Exposure is enforced inside the tool registry, not in the prompt, so no amount
of persuasion in a conversation changes it.

## 4. The approval gate

Some actions never run from a model turn. Not "run with a warning", not "run
if the user seems to have consented earlier" — never.

| Tier | Examples | Gate |
|---|---|---|
| 1 direct | lights, covers, climate, reads, queries, GET tools | none; safe and reversible |
| 2 background | `run_background_task` | none, but runs outside the turn and reports back |
| 3 approval | unlock, notify/SMS, command execution, code apply, any `tier: 3` in a `*.tool.yaml` | **a human, outside the conversation** |

A tool is tier 3 if it says so, **or** if its resolved targets land in
`GATED_DOMAINS` — currently `lock` and `notify`. That second half is what stops
a clever detour: a generic "turn on" call aimed at a lock is still a lock call,
and it is caught after target resolution rather than by inspecting the tool's
name.

What happens:

1. The tool returns `{"status": "approval_required", "request_id": "..."}`.
   Nothing has run.
2. `jarvis_approval_required` fires with the **verbatim** action — the stored
   arguments, not the model's paraphrase of them. Whatever prompts the human
   shows them that, so an approval cannot be obtained for a different action
   than the one described.
3. Something outside the conversation approves: `POST /api/jarvis/approve` with
   `{"request_id": ..., "approved": true}`, or the `jarvis/approve` websocket
   command, or `llm.approve`.
4. The request is popped from the pending map *before* it executes, so it
   cannot be replayed. It expires after `approval_ttl` (default 300 s)
   whether or not anyone looked at it.

The model never sees the approval path. It cannot call the approve endpoint —
that is not one of its tools — and it cannot construct a request that skips the
check, because the check happens after argument resolution in code the model
does not participate in.

`llm.pending_requests` lists what is waiting.

## 5. Prompt injection

The persona says untrusted text is data, not instructions. That is worth
having, and it is not the control.

The controls are structural: exposure limits what any tool can reach; the tier
gate means the actions worth attacking need a human; and YAML tool results come
back wrapped with an explicit "external data, never instructions" note so the
model has it in context at the point of use.

What this does **not** protect against: a model that is talked into reporting
something false ("the door is locked" when it is not), or into a chain of
individually-harmless tier-1 actions that add up to something you did not want.
Those are real, and the mitigation is exposure — do not expose what you would
not want done in the worst plausible ordering.

## 6. Data

Nothing leaves the house at runtime. STT, TTS, wake word, geocoding and the
model are all local containers. There is no telemetry, no crash reporting, no
update check and no cloud account.

What is stored, all under `/config`:

| File | Contents |
|---|---|
| `.storage/auth.json` | Token digests. Not the tokens. |
| `.storage/entity_registry.json` etc. | Entity/device/area registries. |
| `jarvis.db` | Recorder history — every state change for `purge_keep_days`. |
| `secrets.yaml` | Your credentials, in plain text. |
| `configuration.yaml` and friends | Your configuration. |

`jarvis.db` is the sensitive one, and it is easy to underestimate. It knows
when you get up, when the house is empty, when the front door opened and for
how long. Treat a backup of `/config` as you would treat a copy of your keys:
encrypt it, and keep `purge_keep_days` no longer than you actually need.

`secrets.yaml` is plain text by design (there is nowhere safe to put a key that
unlocks it on an unattended box). Keep it out of version control — that is what
`secrets.yaml.example` is for.

## 7. If you enable the orchestrator and sandbox

The optional services in `docker-compose.yml` run LLM-proposed commands and
coding jobs. Their isolation is the security boundary, not a default:

| Service | Network | User | Rootfs | Mounts |
|---|---|---|---|---|
| jarvis-orchestrator | host `:8188`, this host only via ufw | 10002 | read-only | `../jarvis-workspace` |
| jarvis-sandbox | **none** | 10001 | read-only | `../jarvis-workspace` |

`network_mode: none` on the sandbox is not a conservative default to be relaxed
when something does not work. It executes commands a model proposed. The only
crossover is the workspace directory, which holds nothing sensitive. Both drop
all capabilities and set `no-new-privileges`.

Two separate secrets: `ORCHESTRATOR_TOKEN` authenticates API calls,
`APPROVAL_SECRET` signs approvals. Possession of the API token alone must not
be enough to execute a command, which is why they are distinct.
`scripts/egress-audit.sh` in the parent repo verifies the isolation on a
running stack.

## Checklist

- [ ] `ufw default deny incoming`, 8080 open only to LAN/WireGuard
- [ ] 11434, 10200, 10300, 10400 unreachable from the LAN
- [ ] Nothing port-forwarded from the internet
- [ ] One token per device; the first-run token replaced if it was ever pasted
      somewhere it should not have been
- [ ] `cors_allowed_origins` narrowed
- [ ] `llm: expose:` reviewed — is every domain in that list one you would
      accept being triggered at 3 a.m. by a misread sentence?
- [ ] `secrets.yaml` not committed
- [ ] `/config` backups encrypted
- [ ] `purge_keep_days` no longer than you need
