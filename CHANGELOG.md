# Changelog

Every milestone in `MILESTONES.md` adds an entry here when it is ticked, in the
same commit. Format: one heading per release (or `Unreleased`), one line per
change, newest first, each line naming the milestone it belongs to. Behaviour,
not diff: what a user or operator can now do, or can no longer be bitten by.

## Unreleased

### Fixed
- M29 found four failures that had been live for days with every suite green, because no suite
  had ever looked at the deployment: the model-server sensor was polling `/v1/v1/models` and
  404ing every thirty seconds (an `!env_url` that applied a path the base URL already carried);
  two Jarvises on one MQTT broker were evicting each other 22 times a minute, each eviction a
  twenty-frame traceback, because the default client id was the literal string `jarvis`; every
  `docker compose watch` rule synced code into `/app/…` when all three Python images run from
  `/srv`; and `jarvis-config-init` had chowned tracked files in this repository to a uid that
  does not exist on the host, so the person working on the checkout could not edit their own
  `configuration.yaml`. All four are fixed, and each has a test that fails without the fix.
- M28: `photon` had restarted **2,699 times over two days** — with no `REGION` it downloads a
  58 GB planet index needing 152 GB of temp space, checks the disk, refuses, and
  `restart: unless-stopped` does it again. It is behind `--profile geocode` now and takes a
  country extract. `jarvis-web` reported **unhealthy** for the same two days because its
  healthcheck used `localhost`, which resolves to ::1 first while the server binds IPv4 — the
  console was answering every request perfectly well. Both were true while every test suite in
  the repository was green, which is the argument for M29.

### Added
- M19 (the coding agent): a verify-until-green loop that runs the repository's own check after
  the job says it is finished — and **when it changed nothing**, which is the case that matters,
  because a model that decides it is done before it starts looks exactly like success; one
  commit on the `jarvis/…` branch, with the diff measured from the branch point so a committed
  job still reports what it changed; four permission modes (`ask` · `accept-edits` ·
  `auto-run-tests` · `full-auto`) chosen by the operator and never by the model, with
  destructive commands asking in every mode unless the task allowed that exact line; and a gate
  that **blocks the job** rather than ending a turn — same `jarvis_approval_required` event, same
  `jarvis/approve` command, shown on the job in the console with the diff above the buttons, and
  an unanswered request expiring as a refusal. `fixtures/coding/failing-tests` (three bugs, three
  kinds, standard-library tests because the sandbox has no network) and `evals/coding_eval.py`,
  which re-runs the suite in the container itself and hashes everything outside the job's mount.
- M29 (the suite runs against the real stack): `scripts/verify/live_interaction.sh` starts with
  `docker compose up -d --wait`, 22 of 29 scenarios now address the running jarvis-core and the
  console container rather than a copy of them, and the run fails if any container is unhealthy
  at the start or logged an ERROR-level record by the end. Two resilience scenarios that only
  mean anything against containers: `docker restart jarvis-core` between two turns of one
  conversation (the thread survives), and `docker stop wyoming-whisper` mid-utterance (the turn
  ends with `stt-stream-failed` instead of hanging, and works again once it is back). Safe to
  point at a house somebody lives in: config, `.storage` and the mosquitto volume are tarred
  before the first word and restored after the last, every thread it opens is named
  `test:<scenario>:<variant>`, and anything a scenario created is deleted with its absence
  asserted before the next one starts. The seven research/coding/skills scenarios keep their
  own jarvis-core (`ground: fixture`), because "did it cite three independent sources" is a
  question about a web this repository owns.
- M28 (the compose stack is the runtime): every image pinned — the three Wyoming services to
  the exact versions this repository's WER and latency numbers were measured against — a
  healthcheck on every long-running service including the three voice ones that had none,
  `mem_limit`/`cpus` sized for a 4 vCPU / 8 GB host, `docker compose watch` blocks so a code
  change reaches a running container, and `docs/RUNBOOK.md`: bring-up, teardown, per-directory
  and per-volume backup/restore, and what upgrading a pinned image costs.

### Changed
- Planning (fourth mid-run addition): M48 — every page in the web console on the chosen C2
  direction. `docs/UI_MIGRATION.md` is a walked inventory with one row per page, and
  `scripts/verify/m48-webui-c2.sh` fails while any row is unchecked, any hardcoded style value
  survives token-lint, any page lacks one of the four states, or any old-design component is
  still referenced; the live suite navigates every route and `docs/LIVE_TEST_REPORT.md` gains
  a migration section with per-breakpoint screenshots.
- Planning (third mid-run addition): reach, routing, delegation, motion and an ecosystem —
  M38 channels (Telegram/Signal behind an identity allowlist, tailnet-only, mock server in CI),
  M39 CalDAV + IMAP/SMTP behind the approval model with fixture containers, M40 a self-hosted
  LiteLLM gateway with policy routing, fallback, caps and a privacy guard that refuses cloud
  routing for anything carrying memory or notes, M41 Claude Code as an optional sandboxed
  coding backend (off until a key is supplied), M42 delegation across backends, M43 hardening
  (injection quarantine, control-token stripping, least privilege, a call-time secrets store,
  `docs/THREAT_MODEL.md` and a red-team scenario file the suite fails on), M44 the motion
  system and its signature moments with a headless perf-trace gate and a reduced-motion path,
  and M45–M47 the skills/plugins ecosystem: one registry over SKILL.md skills, MCP servers and
  plugins, a real management surface, and a catalog whose installs are allowlisted, pinned,
  checksummed, permission-prompted and sandboxed. `docs/AUDIT.md` carries the delta and
  `PROCESS.md` §2d the five rules those milestones are written against.
- Planning (mid-run addition): the compose stack becomes the runtime under test — M28 (pinned
  images, healthchecks on every service including the three Wyoming ones, resource limits,
  named volumes, `docs/RUNBOOK.md`) and M29 (`docker compose up -d --wait` as the live suite's
  first step, scenarios against the real endpoints, a run that fails on an unhealthy container
  or an ERROR log line, resilience scenarios, and volume snapshot/restore around the
  destructive ones). Plus the local AI toolbelt, M30–M37, each under one contract: baseline the
  numbers this suite already reports, adopt, re-measure, and remove the service if nothing
  improved. Docker access arrived on this host while this was written, which is what makes all
  of it — and M19's containment check, and the live research backend — possible.

### Added
- M18 (research): the engine follows leads (a page that names the thing it does not explain is
  searched for, once — `lead_depth`), cross-checks each key claim against the other pages read
  and says in the report whether anything else said it, and comes in two budgets rather than
  two implementations (`quick`: three pages, seconds; `deep`: several angles, leads, claims —
  and a mode can only ever narrow what the operator configured). Reports are written to
  `<config>/research/<date>-<slug>.md` as well as saved as a note. `evals/research_eval.py`
  runs a fixed question set against a fixture web this repository owns — two sites on two
  loopback addresses, so the per-domain cap and corroboration both mean something — and checks
  that every fact in the report is on a page that was read and that every citation resolves.
  The `--backend live` run against the operator's SearXNG is the Scripted claim, and refuses
  clearly rather than pretending when `SEARXNG_URL` is unset.
- `cancel_task`: "actually, stop that" works by voice. Until now the honest answer was "I have
  no tool to stop a background task", which the model said, correctly, while the job ran on.

### Fixed
- The narrated-call nudge could cause an action nobody asked for: asked to STOP a research run,
  Jarvis cancelled it, summarised what it had done, and the summary mentioned `deep_research`
  — so the nudge told it to "make the call properly" and it started the research again. A turn
  that has already called a tool is reporting, not promising, and is never nudged.
- A turn whose only words came before its tool call is no longer replaced by the canned "I
  didn't manage to put an answer into words": "I'll start the research" is true, and the
  preamble is only dropped when something replaced it.
- An empty note search says so, and says where to look instead. A bare empty list had the model
  searching its notes three times with different words for something that was on the web.

### Added
- M17 (interactions): the things Jarvis says without being asked are now kept. A new
  `notifications` integration records every proactive message — a finished job, a failed one,
  the briefing — fires `jarvis_notification` as each is made, and lists them over websocket and
  REST, so "what did you tell me earlier?" has an answer. The console draws them as **moments**
  rather than toasts (a toast is gone in four seconds and these arrive when nobody is looking),
  each with a WHY AM I SEEING THIS? that names the bus event that produced it; the phone gets
  the same records on its own board. Conversations are searchable (`jarvis/conversation/search`
  returns the line that matched, not just an id), a thread resumes with its earlier turns in
  front of the model after a restart, and two clients on one thread see one transcript —
  `testing/e2e/test_threads.py` and `test_continuity.py` prove both against a real server. The
  briefing's schedule and sections are editable from the console without a restart. And a reply
  that used remembered notes now carries which ones, rendered under it as WHY THIS ANSWER:
  personalisation nobody can inspect is indistinguishable from a machine making things up.

### Added
- M15 (memory): Jarvis now learns facts in passing — after a turn that states one, a single
  bounded model call proposes durable facts, stored as `source: extracted` and linked to the
  turn, so they can be told apart from what you dictated and deleted on that basis. A word
  ("off the record") turns it off for a turn, the transcript itself is never stored, and every
  extracted fact goes through the same redaction and refusals as a dictated one. Plus the half
  that makes "it's your data" true: `GET /api/memory/export` (JSON or markdown, as a file),
  `memory.wipe` — which clears the **vector sidecar** too, because a store that reports itself
  empty while a semantic index still ranks the old text has deleted nothing — and a `/memory`
  console page that shows every note, where it came from, and the two buttons the model does
  not get. `evals/memory_eval.py` proves store → **restart** → retrieve → forget → export →
  wipe against a real server.
- M16 (notes): documents, as markdown files under `<config>/notes/`, with YAML frontmatter,
  `[[wiki links]]` resolved both ways and a SQLite FTS5 index that is *derived* — delete it and
  it rebuilds from the files. Tools `note_create`/`note_append`/`note_search`, a
  full REST and websocket API, a `/notes` console page, a NOTES tab on the phone, and two
  desktop actions (`save_note`, `find_note`) so a snippet on the laptop lands in the house.
  Research now writes its reports here instead of into memory: a four-page report as a
  "remembered note" pushed the user's actual preferences out of a bounded store and put four
  pages of prose in front of every "turn the lights off".

### Added
- M13 (skills): drop a folder with a `SKILL.md` in it into `config/skills/` and Jarvis knows
  it — the open Agent Skills format, YAML frontmatter and a markdown body, no code and no
  restart beyond `skills.reload`. Only the **name and description** reach the system prompt;
  the body arrives when the model calls `use_skill`, because twelve skills of two thousand
  words each would be twenty-four thousand words in front of every "turn the lights off". A
  skill cannot run the scripts beside it (the loader has no execution primitive at all), cannot
  grant itself a tool or lower a tier, and cannot forge a prompt section through a description
  with a newline in it. WS `jarvis/skills/list|get|reload`, REST `/api/skills`, a panel on the
  console's Tools page that also lists the skills that FAILED to load and why.
- M14 (MCP inspect): `jarvis/mcp/inspect` (and `GET /api/mcp/servers/<name>/inspect`) returns
  one server in full — protocol version, server info, every tool's JSON schema, and
  `last_error`, which is the field that matters: a server missing from the tool list told
  nobody why. The console draws it behind the INSPECT button with a **test call** per tool that
  goes through `jarvis/tools/call` — the same approval gate the model uses, because a
  console-only execution path would be a way around it. A server that is down is now retried
  automatically with per-server backoff (30 s doubling to 30 minutes), so an MCP server that
  starts a few seconds after jarvis-core no longer waits for a human to press reconnect.

### Added
- Live interaction testing (M24/M25, folded in mid-run): `testing/live/` talks to Jarvis the
  way a person does — the user's speech is synthesised locally with Piper in `en_US-amy-low`
  (Jarvis answers in `en_GB-alan-medium`, so no transcript can be attributed to the wrong
  side), delivered through the audio-input API **and** through a real headless browser's
  microphone, and Jarvis's spoken replies are transcribed back with the same Whisper the
  system itself uses. Scenarios are YAML fixtures asserting on the house (the service called,
  the state changed, the task created), with a local-LLM judge only where a deterministic
  check cannot express the criterion — and every verdict logged with its reason. 27 scenarios
  ship covering every capability; the 15 whose capability does not exist yet carry
  `gated-on: <milestone>` and fail in full mode until it does.
  `bash scripts/verify/live_interaction.sh --implemented-only` is now part of every remaining
  milestone's verification, and `make verify-all` runs the whole ungated suite.

### Fixed
- The spoken reply carried every round's words, not the answer: a turn that guessed before
  calling a tool said both out loud — "The bed light is already off, sir. The bed light is
  now off, sir." — and after a narrated-call correction it read the correction out too
  ("You're right, sir — I described the check without running it"). Text from a round that
  then called a tool is now `ConversationResult.preamble`: still streamed, so a surface can
  show the working, and no longer spoken, archived or returned as the answer. Found by
  talking to it; see `ISSUES.md`.
- A turn whose only words were written before a tool ran came back **empty** — a blank bubble
  on the console and silence on the speaker. The "it said nothing" fallback was asked of
  everything streamed rather than of the answer.
- The voice path spoke the stream, not the answer, so the preamble fix above did not reach it:
  `PipelineRun` now prefers the agent's own final text when the two differ.

### Changed
- The console's palette, type and motion move to Reactor II's values (accent #4fe3ff, Barlow /
  Space Grotesk / JetBrains Mono, 160/260 ms); Compose is enabled in the Android build for the
  generated theme (uncompiled here — M08). Jarvis Code, Android and desktop parity tests now read
  `design/tokens.json` instead of `tokens.ts`.

### Changed
- M09 (one model endpoint): `LLM_URL`/`LLM_MODEL` are the first-class settings everywhere —
  `configuration.yaml`, `.env.example`, compose, the smoke script and the worked example —
  with `OLLAMA_*` kept as a fallback. The orchestrator's fan-out speaks
  `/v1/chat/completions` instead of Ollama's `/api/chat`, and no longer bolts an `ollama/`
  provider prefix onto a model name. The dashboard readout polls `/v1/models` (every
  OpenAI-compatible server serves it) instead of Ollama-only `/api/ps`.

### Added
- M12 (hooks): two named trigger platforms, because both were being written as raw `event`
  triggers and both were wrong. `platform: wake_word` fires once per detection instead of
  fourteen times per voice run, and can be scoped to one satellite (`device_id:`), one word or
  one pipeline. `platform: task` fires on the transition — `started`, `completed`, `failed`,
  `cancelled` are four distinct bus events — so "tell me when the research is done" is one
  notification rather than one per progress tick, and a cancelled job is not reported as a
  failure. `event_data:` keys may now be dotted paths into nested payloads
  (`parcel.carrier`, `steps.0.status`), which is the only way to match anything on this bus.
  `jarvis-core/docs/hooks.md` and `config/examples/hooks.yaml` document all five hooks
  including the webhook's "the id is the secret" and `webhook_require_auth`.

### Added
- M11 (plan → act → verify): background work with more than one thing in it is now planned
  before it is done. The plan's steps land on the task, so the console shows what Jarvis
  intends before it starts; each step is acted on as an ordinary tool-using turn; each outcome
  is judged by a separate call that can see the outcome but not the argument for it; a "not
  done" verdict re-plans what is left, twice at most. `tests/contracts/tool_tiers.json` makes
  the tier meanings (1 direct · 2 background + notify · 3 approval) one table that core, the
  console and the Android mirror all read, and the MCP config comment that promised a
  confirmation tier 2 has never done is gone.

### Fixed
- M11: `run_background_task` looked the conversation agent up under a key nothing sets, so
  every background task the assistant accepted failed with "there is no conversation agent on
  this server" after two retries. Unit tests had mocked past it; the end-to-end test against a
  real server is what found it. A planned task also no longer shows two invented steps
  ("work on it", "write it up") in front of the plan it actually chose.

### Added
- M10 (task engine): `jarvis/taskengine.py` — a bounded queue with a concurrency cap
  (`llm.max_concurrent`, default 2, because every worker ends up talking to one model server),
  retries with jittered backoff, cooperative cancellation that is not a failure, and a queue
  persisted beside the task list so work that was waiting is still waiting after a restart.
  `run_background_task` now actually runs the work; scheduled research and coding jobs queue
  (reminders do not); finished code runs and their diffs are written down instead of living in
  memory; the orchestrator reloads its jobs (`load_persisted` had never been called);
  `jarvis/tasks/retry` puts a finished task back on the queue.
- M09: `llm: local_only:` (default on) resolves the model server's URL at startup and refuses
  a public address — "100 % local" was a promise nothing verified.
- M06 (InfluxDB): `metrics/sources/influx.py` reads an InfluxDB the operator already runs —
  it works out from `/health` and `/ping` whether it is 1.x (InfluxQL) or 2.x/3.x (Flux),
  asks the server for the schema, keeps the token in a header, and never writes. Proven
  offline against a fake of each generation; `scripts/check-influx.py` is the live check.
  A `homelab-gpu` example dashboard ships.
- M05 (dashboards): `jarvis/metrics/` defines one shape for anything graphable and ships the
  `internal` source — the recorder's entity history, this host's load/memory/disk, and counters
  for turns, tool calls and task outcomes; `integrations/dashboards/` stores layouts per token
  (a token is the identity), with a shipped `homelab` example; `/dashboards` draws six chart
  types with no charting dependency and lets a widget be added, resized, moved, swapped and
  removed from the keyboard; `tests/contracts/dashboard_layout.json` binds both sides.
- M04 (task-execution UI): `tests/contracts/task_events.json` binds server and console;
  `TaskRegistry` gains `tool_started`/`tool_finished`, `output()`, `raise_if_cancelled()` and a
  persisted per-task log replayed by `jarvis/tasks/log`; the coding agent and research emit tool
  calls and stream their output live; orchestrator delegate and code jobs are registered as tasks
  and polled; `/tasks/[id]` shows the plan, live calls, streaming output, a timeline and cancel.
- M03 (web console on the system): every screen declares its status through `ScreenState`
  (loading · empty · error · offline), `src/lib/screens.ts` is the manifest three things read,
  `+error.svelte` catches a thrown route, and `src/lib/online.ts` tells a dead relay from a dead
  network. Page-level horizontal clipping is gone and `e2e/responsive.spec.ts` proves every
  screen fits at 360/414/768/1024/1440; `e2e/states.spec.ts` drives every screen into the states
  it can be driven into; `e2e/controls.spec.ts` requires every control to be nameable, focusable,
  and — when disabled — to say why. `jarvis-web/src` is clean under the token lint.
- M02 (component library): `$lib/ui` — 18 token-only components (Button, IconButton, Input,
  Select, Toggle, Field, Panel, Row, Pill, Toolbar, Tabs, Dialog, SkeletonRows, EmptyState,
  ErrorState, OfflineState, `ScreenState`, and the `Reactor` instrument), each with a
  `@component` doc block, a README section, an SSR test and a live demo on `/styleguide`;
  the eight hand-copied empty states across the console are now one component.
- M01 (design system): `design/tokens.json` is the single source of truth (Reactor II);
  `python3 design/build.py` generates `tokens.css`/`tokens.ts` (web), `tokens.py` (desktop),
  `JarvisTokens.kt` + a Compose `JarvisTheme.kt` + `tokens.xml`/`colors.xml` (Android), with
  `--check` for drift; `scripts/verify/token_lint.py` (ratchet baseline) fails
  `make verify-all` on any new hard-coded colour/spacing/type/motion value; `/styleguide`
  renders every token and the four screen states; Barlow, Space Grotesk and JetBrains Mono
  are self-hosted; `.claude/skills/jarvis-design-system` + `.claude/rules/design-system.md`
  bind future sessions. `make tokens`, `make tokens-check`, `make token-lint`.
- Design: three divergent visual directions for the redesign (Instrument, Ledger, Reactor),
  each mocked on the chat/voice, live-task and dashboard screens as static HTML with inlined
  tokens under `docs/design/`, rendered headlessly to `docs/design/shots/` by
  `docs/design/screenshot.mjs`. Direction C chosen and revised as Reactor II
  (`docs/design/c2-reactor.html`: the reactor as an instrument, flat panels, sliding-underline
  tabs, real motion; stills + WebM clips via `screenshot-c2.mjs`). M01/M02 build from it.
- M00: `make verify-all` and `scripts/verify/` — one check script per milestone; a
  failing check is the definition of unfinished work. Playwright's port is now
  `E2E_PORT` so the suite runs beside a live install.
- Agent-intelligence targets folded in: `docs/AUDIT.md` §10–15 (research engine, coding
  agent, subagents, memory, notes, user interactions) and milestones M15–M20 with verify
  scripts `m15-memory.sh` … `m20-subagents.sh`; desktop automation, the phone flag and final
  integration renumbered M21–M23 (`m21-…`, `m22-…`, `m23-…`).
- Planning artefacts for the milestone run: `docs/AUDIT.md`, `MILESTONES.md`,
  `PROCESS.md`, `BLOCKERS.md`, this file.
