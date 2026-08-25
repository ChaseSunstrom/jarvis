# Connecting a client to jarvis-core

Every Jarvis client — the web HUD and management console, the Android app, the
ESP32 satellites, `curl` — speaks the **same websocket protocol** against the
same endpoint. There is one contract, and it is deliberately identical to the
Home Assistant websocket API so a client written against either backend works
against the other.

```
jarvis-core   http(s)://<host>:<port>
              ├── /api/websocket        the socket everything real happens on
              ├── /api/tts_proxy/<t>.wav synthesised speech (token in the path)
              ├── /api/...               a REST mirror of the same operations
              └── /healthz               liveness
```

Default bind is `0.0.0.0:8080`; the repo's compose file and every example use
`8123` because that is the port existing clients were written against:

```sh
python -m jarvis --config ./config --host 0.0.0.0 --port 8123
```

## Authentication

One credential type: a long-lived bearer token. No accounts, no login form.

- REST: `Authorization: Bearer <token>`.
- Websocket: the token goes in the `auth` message (below), or as an
  `authorization` field on it.

On first run — nothing in `<config>/.storage/auth.json` and no `JARVIS_TOKEN` in
the environment — jarvis-core mints a token and prints it in a banner. Set
`JARVIS_TOKEN` to pin one instead. More can be minted with
`python -m jarvis --create-token <name>` or `POST /api/auth/tokens`, and revoked
at `DELETE /api/auth/tokens/{id}`; only a SHA-256 digest is ever stored, so a
token is shown exactly once.

## The handshake

```
server  {"type": "auth_required", "ha_version": "jarvis-0.1.0"}
client  {"type": "auth", "access_token": "..."}
server  {"type": "auth_ok", "ha_version": "jarvis-0.1.0"}      (or auth_invalid, then close)
```

`ha_version` is reported so Home-Assistant-shaped clients recognise the
handshake without a special case. After `auth_ok`, every client message carries
a monotonically increasing integer `id`, unique per connection:

```
client  {"id": 1, "type": "get_states"}
server  {"id": 1, "type": "result", "success": true, "result": [...]}

client  {"id": 2, "type": "subscribe_events", "event_type": "state_changed"}
server  {"id": 2, "type": "result", "success": true, "result": null}
server  {"id": 2, "type": "event", "event": {"event_type": "state_changed", "data": {...}}}
```

A failure comes back as
`{"id": n, "type": "result", "success": false, "error": {"code": ..., "message": ...}}`.
The code a client must handle specially is **`unknown_command`**: it means this
backend does not implement that command, and the client should degrade (show a
hint, hide a page) rather than treat it as a fault. That is exactly how
jarvis-web keeps working against Home Assistant, which knows `get_states` and
`call_service` but not everything else.

## Commands

| Command | Purpose |
| --- | --- |
| `ping` | replies `{"id": n, "type": "pong"}` |
| `get_states` | every state object |
| `get_config` | location, version, components, areas |
| `get_services` | the service catalogue: `{domain: {service: {description, fields, supports_response}}}` |
| `call_service` | `domain`, `service`, `service_data`, optional `target` and `return_response`; result carries `context` and `changed_states` |
| `subscribe_events` / `unsubscribe_events` | bus events, optionally filtered by `event_type`; unsubscribe takes `subscription: <the subscribe id>` |
| `fire_event` | put an event on the bus |
| `conversation/process` | one text turn through the conversation agent. Non-streaming — for a chat surface that wants deltas, tool rows and reasoning as they happen, use `assist_pipeline/run` with `start_stage: "intent"` (below) |
| `jarvis/conversation/list` | past conversations, most recent first: `{conversations: [{id, title, created, last_active, turns, preview}]}`. Summaries only, no message bodies |
| `jarvis/conversation/get` | `conversation_id`; one conversation in full, including each turn's `thinking` and `tool_calls`. A tool's *result* is not stored — only its name, arguments and whether it worked |
| `jarvis/conversation/delete` | `conversation_id`; forgets it in both the model's memory and the archive |
| `jarvis/conversation/rename` | `conversation_id`, `title`; a name of your own instead of the first sentence |
| `jarvis/tasks/list` | every tracked job, newest first: `{tasks: [...]}`. `kind` filters, `active: true` hides finished ones. Each row carries its own steps and a derived `fraction`, so a list of progress bars is one request rather than one per bar |
| `jarvis/tasks/get` | `task_id`; one task in full |
| `jarvis/tasks/retry` | `task_id`; put a finished task back on the queue. Refuses one whose kind this server cannot rebuild, rather than being a button that does nothing |
| `jarvis/tasks/log` | `task_id`, `limit`; the task's replayable history — every tool call, every line of output, oldest first. The activity events are fire-and-forget, so a client that opens a task's page two minutes in has missed them; this is how it catches up. See `tests/contracts/task_events.json`. |
| `jarvis/dashboards/list` | every dashboard this token may see: the ones it saved, plus the shared and shipped ones |
| `jarvis/dashboards/save` | `dashboard`; create or replace one. The server stamps the owner from the token — a client cannot save a board as somebody else |
| `jarvis/dashboards/delete` | `id`; refuses one this token does not own |
| `jarvis/metrics/sources` | what can be graphed, per source, with each source's health |
| `jarvis/metrics/query` | `source`, `series`, and either `range` (`1h`…`7d`) or `start`/`end`/`step`; the points. A source that is down answers with an error per series rather than failing the request |
| `jarvis/tasks/cancel` | `task_id`; **asks** the worker to stop. The registry is a record, not a scheduler — it cannot reach into the coroutine — so the reply carries `cancelled` and a `note` saying a worker that does not check may still be running |
| `jarvis/tasks/delete` | `task_id`; forgets one task. Does not stop it |
| `jarvis/tasks/clear_finished` | forgets every finished task, leaving the live ones |
| `jarvis/schedule/list` | every scheduled job, soonest first, with when each next runs and what it last did |
| `jarvis/schedule/add` | `kind` (`notify`/`research`/`code`/`service`) plus a `when` — `{mode: once, at: <iso>}`, `{mode: daily, at: "HH:MM"}`, `{mode: weekly, at, days}` or `{mode: every, minutes}`. Every firing mints a task, so it shows on the same progress surfaces as everything else |
| `jarvis/schedule/remove` | `job_id`; a job from `configuration.yaml` is refused — edit the file |
| `jarvis/schedule/enabled` | `job_id`, `enabled`; turn one off without forgetting it |
| `jarvis/tools/list` | the model's own toolbox: `{tools: [{name, description, parameters, tier, domain, needs_approval, may_escalate}], count}`. This is exactly the set `agent.py` offers the model, with no filtering, so "listed here" and "offered to the model" are the same set by construction. `needs_approval` and `may_escalate` are computed server-side because tier alone is not the whole rule — a gated domain holds a tool at any tier, and a tool with a gate is held depending on its arguments |
| `jarvis/tools/call` | `name`, `arguments`; runs one tool the way the model would, straight through `ToolRegistry.call` — same argument coercion, same unknown-tool message, **same approval gate**. A Tier-3 tool answers `approval_required` and raises a card rather than running. Distinct from `config/tool/list` below, which is the subset this console may EDIT; the Tools page shows the union |
| `jarvis/code/list` | Jarvis Code: `{repositories, jobs, sandboxed, environments, can_create, workspace}`. `forges` carries `has_token` and each one's `allow` list — never a token. Each repository carries `environment`, `networked`, `managed` and `origin`, so a page can say what a job there may run and whether Jarvis made it. `jobs` is the task list filtered to `kind: code`, so a page can draw both halves from one request |
| `jarvis/code/start` | `repo`, `instruction`; starts a coding job and returns its `task_id`. The job runs on a branch of its own and reports through the task list, so its progress is the same bar as everything else. Not approval-gated — the request carried a bearer token, whereas the model's `code_task` tool is Tier 3 |
| `jarvis/code/create_repo` | `name`, optional `description` and `environment`; creates a git repository inside `code: workspace:` and answers with the refreshed listing. Refused when no workspace is configured — Jarvis may not create repositories anywhere else |
| `jarvis/code/forget_repo` | `name`; drops it from the listing. **Does not delete the files** — Jarvis creates directories and never removes them |
| `jarvis/code/clone_repo` | `forge`, `project` (`owner/name`), optional `name` and `environment`. Clones into the workspace. Refused unless that path is on the forge's allow-list — the console has no more reach here than the model, because the constraint is the operator's configuration rather than who is asking |
| `jarvis/code/push` | `repo`, `branch`; pushes one `jarvis/…` branch to the forge it came from. Never `main`, never forced, and refused if `origin` was rewritten |
| `jarvis/code/result` | `task_id`; the branch, the diff, the checks and the tool trail from one finished job |
| `jarvis/briefing/get` | when the morning and evening briefings fire, which sections are in them, and which sections exist |
| `jarvis/briefing/set` | any of `morning`, `evening` (`"07:00"`, or `"off"`), `include`, `importance`. Takes effect at the next tick of the schedule loop — no restart. **Not written back to `configuration.yaml`**: that file is the operator's, and a service that rewrote it would fight whoever edits it, so a restart returns to the configured values |
| `jarvis/notifications/list` | optional `unread`, `limit`; every proactive message Jarvis has sent, newest first, with `unread`. Each carries the bus event that produced it (`source`) and where to go to see the thing itself (`link`) — "why am I seeing this" answered with a fact rather than a guess |
| `jarvis/notifications/read` | `notification_id`, or `all: true` |
| `jarvis/notifications/dismiss` | `notification_id`, or `all: true` |
| `jarvis/conversation/search` | `query`; threads containing it, newest first, each with the lines that matched. Plain substring over the bounded archive rather than an index — the archive is already in memory, and a second store would be a second thing to keep in step |
| `jarvis/notes/list` | optional `tag`, `query`, `limit`; every note, newest first — titles, tags, links and back-links, no bodies |
| `jarvis/notes/get` | `note_id` (slug or title); one note with its `body`. **Not `id`** — every frame has one of those already, and it is the correlation number |
| `jarvis/notes/create` | `title`, optional `body`, `tags`, `overwrite`. Writes `<config>/notes/<slug>.md`; the file IS the note |
| `jarvis/notes/update` | `note_id` plus any of `body`, `title`, `tags` |
| `jarvis/notes/append` | `note_id`, `text`; adds to the end, which is what "add to my list" means |
| `jarvis/notes/delete` | `note_id`; removes the file |
| `jarvis/notes/search` | `query` and/or `tag`; full text through SQLite FTS5, with a word-by-word fallback so a query containing punctuation returns notes rather than a syntax error |
| `jarvis/memory/list` | optional `query`, `tag`, `limit`; every durable note, newest first, or the matches for a query — `{entries: [...], total, query, tag}`. The whole store rather than a page of it: the point of the route is that a person can read what is held about them |
| `jarvis/memory/add` | `text`, optional `tags`, `pinned`, `allow_untrusted`. The console is a person typing, so it may store what the model may not |
| `jarvis/memory/forget` | `entry_id` or `query`, or `all: true` for everything **including the vector sidecar** — a store that reported itself empty while an index still ranked the old text would be the least visible kind of broken promise |
| `jarvis/memory/pin` | `entry_id`, `pinned`; a pinned note keeps its place in the prompt whatever the turn is about |
| `jarvis/memory/export` | optional `format` (`json` or `markdown`); everything in one document. `GET /api/memory/export?format=markdown` returns it as a file |
| `jarvis/traces/list` | recent agent traces, newest first — `{traces: [{id, origin, label, task_id, started, ms, spans, tools, model_calls, prompt_tokens, completion_tokens, model_ms, tool_ms, errors}], recording}`. Summaries only; `recording: false` means `observability:` is not configured, which is a choice rather than a fault |
| `jarvis/traces/get` | one trace and every span under it — `{trace_id}` OR `{task_id}` for the "view trace" link a task card has, since a task knows its own id and nothing about contexts. `{trace: {…, spans: [{kind, name, started, ms, ok, error, data}]}}` |
| `jarvis/skills/list` | every loaded skill — `{skills: [{name, description, allowed_tools, metadata, version, resources, path, body_chars}], errors: [{path, error}], enabled, path}`. Names and descriptions only: the body is what `use_skill` fetches, and shipping every body here would be the same context bloat on the wire that progressive disclosure exists to avoid. `errors` carries the skills that could NOT be read, with the reason — a mistyped frontmatter otherwise just makes a skill silently absent |
| `jarvis/skills/get` | `name`; one skill with its `body`. What the console shows when you open one |
| `jarvis/skills/reload` | re-reads the skills directory: `{loaded, errors}`. The only write, and it writes nothing — a skill is created by putting a folder on disk |
| `jarvis/extensions/list` | everything extensible — skills, MCP servers and tool plugins — in ONE shape: `{extensions: [{id, kind, key, version, description, author, source_url, permissions, granted, revoked, tools, network: {needs, hosts}, filesystem: {read, write}, origin, enabled, location, health: {ok, detail}, last_used}], errors: [{kind, id, location, error}], permissions: [...], counts: {skill, mcp, plugin}}`. Health is included rather than left to a second round trip: this is a list of things that are either working or not, and one that paints without that changes under the reader a moment later. `permissions` is the whole closed vocabulary, so a client can draw the scope control without hard-coding it. `errors` carries what would not validate, with the reason |
| `jarvis/extensions/set` | `{key, enabled?, permissions?}` — turn one off, or narrow what it holds. Applied to the running system BEFORE it answers: a disabled plugin's tools are off the model's list by the time this returns, and `permissions: null` means "back to whatever the manifest declares". Narrowing only — a permission the manifest never declared cannot be granted, because the manifest is the statement people read. Answers `{extension, removed: [...], restored: [...]}` |
| `jarvis/extensions/scaffold` | `{name, description, tools?, permissions?, body?}` — write a new `SKILL.md` from the template and load it. The permissions a chosen tool requires are written in for you, so the file cannot fail its own validator a second later. Refuses a name that is not `[a-z0-9][a-z0-9-]{1,63}`, and refuses to overwrite: replacing a skill somebody wrote, because they typed a name that already existed, is not something a create button should be able to do |
| `jarvis/extensions/browse` | `{query?, kind?}` — what the configured catalog sources offer: `{entries: [{id, kind, source, url, version, description, author, permissions, ref, sha256}], sources: [...]}`. Every text field has been quarantined (M43): a description is content from a stranger and arrives wrapped, not filtered. `permissions` is what the entry CLAIMS, intersected with the closed vocabulary — a catalog cannot declare one nothing enforces and have it shown as though it meant something. An empty `sources` means no origin has been allowed, which is the default |
| `jarvis/extensions/plan` | `{source, entry, sha256?, refs?}` — what installing would do, without doing it: `{plan: {id, kind, source, ref, sha256, permissions, files, hooks, warning}}`. `ref` is concrete, never `latest`. `hooks` names every file in the payload that looks like a program — Jarvis never runs them, so this is disclosure rather than defence. **The field is `entry`, not `id`**: `id` is this protocol's own message id |
| `jarvis/extensions/install` | `{source, entry, approved}` — writes what was approved. `approved` is the plan from the call above, passed back; without it this refuses and says which call is missing. The hash is re-checked immediately before writing, because the gap between approving and writing is where a source that wanted to swap the payload would do it |
| `jarvis/mcp/list` | every configured MCP server, its tools, and whether it is up: `{servers: [...], allow_stdio, default_tier}`. Never carries a server's token |
| `jarvis/mcp/inspect` | `name`; one server in full — `server_info`, `protocol_version`, every tool's **JSON schema**, `last_error`, and how long until the next automatic reconnect. The listing is thin because it is drawn for every server at once; this is what you open when a tool call keeps failing |
| `jarvis/mcp/add` | `name` plus either `url` (+ optional `token`) or, when the operator has allowed it, `command`/`args`. Adds the server, connects, and registers its tools as `mcp_<server>_<tool>` |
| `jarvis/mcp/remove` | `name`; forgets a console-added server and unregisters its tools. A server defined in `configuration.yaml` is refused — edit the file |
| `jarvis/mcp/reconnect` | `name`, or omit for all; reconnects and re-reads the tool list, which is how a server that gained a tool becomes visible |
| `jarvis/approve` | resolve a Tier-3 approval the safety gate is holding |
| `config/entity_registry/list` · `/update` | rename (label or `entity_id`), re-area, hide, or set `exposed` |
| `config/device_registry/list` · `/update` | device names and area assignment |
| `config/area_registry/list` · `/create` · `/update` · `/delete` | areas |
| `config/companion/list` | the phones, desktops and satellites *running Jarvis*, each with what it will let Jarvis do to it and whether it is connected. Not the house's entities — those are the registries above. `include_actions: false` returns counts instead of manifests |
| `config/token/list` | every long-lived token, with `connected` for whether a live socket is holding it. Built from the auth manager, so a token with no pairing record still appears |
| `config/token/revoke` | `token_id`; removes the credential **and closes every socket authenticated with it**, which is why the result carries `sockets_closed` |
| `config/tool/list` · `/create` · `/update` · `/delete` | console-authored tools. Built-ins are listed and refuse to be edited or shadowed |
| `config/settings/list` · `/set` · `/reset` | the editable settings overlay, grouped as jarvis-core groups them. Each row says where its value came from and whether applying it needs a restart |
| `config/automation/list` · `/create` · `/update` · `/delete` | automations |
| `jarvis/device/register` | says who this socket is, so it can be sent commands and counted as present. It is the door to the whole device channel — `device_command` / `device_result` / `device_event` are *frames*, not commands, and are specified in `docs/cross-device.md` and `android-app/docs/device-channel.md` |
| `assist_pipeline/pipeline/list` | available voice pipelines + the preferred one |
| `assist_pipeline/run` | a voice run (below) |

Every command above is in `_HANDLERS` in `jarvis/api/websocket.py`, and
`test_packaging.py::test_every_websocket_command_is_documented` asserts the two
sets are equal in both directions — so a command added without a row here, or a
row for a command that no longer exists, fails the build.

**There is no command to allow stdio MCP servers, and there will not be.** An
`http` MCP server is a URL jarvis-core fetches; a `stdio` one is a **program
jarvis-core starts**, as its own user. That switch is `mcp: allow_stdio: true`
in `configuration.yaml` — a file, edited by a person with shell access — so no
request, forged or otherwise, can turn a Jarvis that reads URLs into a Jarvis
that runs commands. With it on, `jarvis/mcp/add` will accept a `command`.

**Versioning rule.** A client that gets `unknown_command` back MUST hide the
feature rather than surface an error, and never fail open. That is what lets a
new console talk to an older jarvis-core: the panel simply is not drawn. It is
also why every new command here is additive, and why none of them changes the
meaning of an existing one.

Registry updates skip **null-valued** fields, so a client clears an assignment
by sending `""` — `{"type": "config/entity_registry/update", "entity_id":
"light.a", "area_id": ""}` — not `null`.

**Renaming an `entity_id`** is the same command, with `new_entity_id`. It used
to answer `not_supported`; it now moves the registry entry, carries the state
across, and rewrites the authored automations that named the old id:

```
client  {"id": 9, "type": "config/entity_registry/update",
         "entity_id": "light.kitchen", "new_entity_id": "light.cooking"}
server  {"id": 9, "type": "result", "success": true, "result": {
           "entity_entry": {"entity_id": "light.cooking", …},
           "renamed_from": "light.kitchen",
           "automations_updated": ["Kitchen at dusk"]}}
```

Three refusals, each `invalid_format` with a sentence: an id that is malformed,
one that already exists, and one in a different domain — the domain is what
decides which services an entity accepts, so `light.x` renamed to `switch.x`
would promise `switch.turn_on` from a platform that does not implement it.

The registry event names both ids (`{"action": "update", "entity_id":
"light.cooking", "old_entity_id": "light.kitchen"}`) so a listener can follow
the move rather than seeing one entity vanish and another appear. Automations
in `configuration.yaml` are the operator's file and are NOT rewritten; only
authored ones, which Jarvis stores itself.

The same operations exist over REST (`GET /api/states`,
`POST /api/services/{domain}/{service}`, `POST /api/config/area_registry/create`
and friends) for scripts that do not want a socket.

## Voice runs

```
client  {"id": 3, "type": "assist_pipeline/run", "start_stage": "stt",
         "end_stage": "tts", "input": {"sample_rate": 16000},
         "conversation_id": null, "pipeline": "jarvis"}
server  {"id": 3, "type": "result", "success": true, "result": null}
server  {"id": 3, "type": "event", "event": {"type": "run-start", "data": {
           "runner_data": {"stt_binary_handler_id": 1, "timeout": 300}}}}
client  <binary>  0x01 + Int16LE PCM     one chunk of 16 kHz mono audio
client  <binary>  0x01                   lone handler-id byte = end of audio
server  ... stt-start, stt-vad-start, stt-vad-end, stt-end, intent-start,
            intent-tool-start / intent-tool-end / intent-tool-narrated /
            intent-thinking (as they happen), intent-progress (deltas),
            intent-end,
            tts-start, tts-end, run-end
```

Two details clients get wrong: the binary prefix byte is the
`stt_binary_handler_id` from `run-start` (not a constant), and pipeline events
arrive under the **run's** message id, so a client must route by id.

`tts-end` carries `{"tts_output": {"url": "/api/tts_proxy/<token>.wav",
"mime_type": "audio/wav"}}`. That path is open — the token in it is the secret —
so a client can fetch it without a bearer header, though sending one is fine.

### Text runs

The same command with no audio: pass the words in `input.text` and start at
`intent`. This is what the console's chat mode uses, and it is a *pipeline run*
rather than `conversation/process` because only a run streams.

```
client  {"id": 4, "type": "assist_pipeline/run", "start_stage": "intent",
         "end_stage": "intent", "input": {"text": "is the back door shut?"},
         "conversation_id": "<or null for a new one>"}
server  ... run-start, intent-start, intent-progress …, intent-end, run-end
```

`end_stage: "tts"` instead speaks the reply as well, which is how the chat mode
answers out loud when the user asked out loud. There is no audio to send, so
the binary handler id in `run-start` goes unused.

### Showing the working

Three events narrate what a turn is *doing*, for a surface that wants more than
a spinner. All three are scoped to the run, so a client with two turns in
flight can tell them apart:

| Event | Data |
| --- | --- |
| `intent-tool-start` | `{name, arguments, round, index, total}` — fired **before** the call runs, so a nine-second tool is visible for nine seconds |
| `intent-tool-end` | `{name, round, index, total, ok, status, error, duration_ms}`. `ok` is false for a tool that answered `{"status": "error"}` as well as one that threw |
| `intent-thinking` | `{delta}` — a slice of the model's reasoning. Consecutive slices are coalesced server-side, so this is paragraphs and not tokens |
| `intent-tool-narrated` | `{tool, round}` — the model wrote a tool call out as text instead of making one, and is being asked to make it properly. Show "still working" rather than stalling; the corrective round happens at most once per turn. Common with small local models |

Reasoning never appears in `intent-progress`: that is the text the TTS speaks
and the HUD renders as the reply, and a model's deliberation is neither.

The same tool payloads are also on the bus as `jarvis_tool_started` /
`jarvis_tool_finished` for anything watching the house as a whole. Use the bus
for a global activity indicator and the run events for a transcript — the bus
events carry no conversation, so they cannot tell you which turn a call
belonged to.

## The clients

### jarvis-web (browser)

Runs as its own Node process (`node build`) and **proxies** the socket so the
token never reaches the browser: the page opens `ws(s)://<origin>/ws`, and the
server dials `${url}/api/websocket`, does the auth handshake itself, swallows
the `auth_*` frames and relays everything else — JSON and binary alike. TTS
audio goes through `/api/tts?path=…`, which validates the path and attaches the
`Authorization` header server-side.

The voice HUD (`/`) and the management pages (`/devices`, `/areas`,
`/automations`, `/tools`, `/settings`) share that one relay.

| Var | Default | Purpose |
| --- | --- | --- |
| `JARVIS_BACKEND` | `core` | `core` (jarvis-core) or `ha` (Home Assistant) |
| `JARVIS_URL` | — | jarvis-core base URL |
| `JARVIS_TOKEN` | — | jarvis-core token, server-side only |
| `HA_URL` / `HA_TOKEN` | — | used when `JARVIS_BACKEND=ha`, and as the fallback otherwise |
| `JARVIS_PIPELINE` | `Jarvis` | pipeline name to select |
| `JARVIS_TTS_VOICE` | `en_GB-alan-medium` | reported to the page via `/api/config` |
| `PORT` | `8199` | listen port |

The selected backend's variables win and the other pair is the fallback, so an
existing Home-Assistant deployment keeps running after an upgrade without
touching its env file.

```sh
JARVIS_URL=http://jarvis-core:8123 JARVIS_TOKEN=… PORT=8199 node build
```

### Android

`android-app/` talks to jarvis-core directly — no proxy, because there is no
browser to hide the token from. The server URL and token are entered in
Settings and stored on the device; `ServerUrl.websocketUrl()` turns
`http://host:8123` into `ws://host:8123/api/websocket`, and
`assist/AssistPipelineClient` runs the same `assist_pipeline/run` exchange with
the same binary framing. `ManagementActivity` is an origin-locked WebView, so
pointing it at the jarvis-web origin gives the phone the same management console
the desktop has.

### ESP32 satellites and scripts

Same socket, same handshake, same framing — a satellite is just a client that
only ever sends `assist_pipeline/run` and audio. For one-shot scripting the REST
mirror is usually easier:

```sh
curl -H "Authorization: Bearer $JARVIS_TOKEN" http://jarvis-core:8123/api/states
curl -X POST -H "Authorization: Bearer $JARVIS_TOKEN" \
     -H 'content-type: application/json' -d '{"entity_id": "light.lab_lights"}' \
     http://jarvis-core:8123/api/services/light/turn_on
```

## Serving a client from jarvis-core

If a directory named `www/` sits next to the `jarvis` package, jarvis-core
mounts it at `/` (after `/api/*` and `/healthz`, which always win). That is for
statically-built clients. jarvis-web is **not** one of them: it needs its Node
server for the token-hiding relay, so run it as its own process and point
browsers at that origin.

## CORS

`jarvis.cors_allowed_origins` in the YAML config sets the allowed origins
(`*` by default). Credentials are disabled on the CORS middleware on purpose —
authentication is bearer tokens, never cookies.
