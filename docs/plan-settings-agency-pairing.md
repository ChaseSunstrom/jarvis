<!--
  A PLAN, not a description of the system. Written before the work, and true
  only of the parts already committed — check `git log` before trusting a
  section. Delete this file when the last phase lands.

  Produced by mapping the four subsystems involved, designing each feature
  independently, attacking the designs from three adversarial lenses (23
  findings), and merging the result. The "Contested findings" section at the
  end names the attack claims that were checked and rejected, with reasons;
  those are as load-bearing as the accepted ones, because the next person to
  have the same idea should not have to re-derive why it does not hold.
-->

No underscore-prefixed tool arguments exist today, so central reserved-namespace stripping is safe. Writing the merged plan.

---

# Implementation plan: editable settings, model-authored changes, QR pairing, and the client port

Verified against the tree before writing. Where an attack finding was checked and found wrong, it is named and rebutted rather than dropped (see **Contested findings**).

The plan is 32 commits in four phases. Every commit leaves `make test` green and is independently revertable. Phases A and B both depend on Phase 0; C is independent of A/B; D depends on A and B having landed.

---

## Phase 0 — config provenance plumbing (2 commits)

Both settings designs need to answer "where did this value come from?" and one of them claims to refuse overlaying a package-supplied key. That claim is unimplementable from `load_config`'s output today — `merge_packages` pops `packages:` and merges values that are structurally identical to literals. Rather than delete the claim, restore the information at the one place it exists.

### Commit 0.1 — package provenance out of `merge_packages`

**Files:** `jarvis-core/jarvis/config.py`

Change `merge_packages(config)` to `merge_packages(config, provenance: dict[str, str] | None = None)`. The optional parameter keeps every existing caller and test green. Inside the loop, record who supplied what:

- whole-key writes (`config[key] = value`) record `provenance[key] = pkg_name`;
- shallow dict merges record one entry per merged subkey, `provenance[f"{key}.{subkey}"] = pkg_name`, because the dict-merge branch is exactly where a package supplies `llm.model` while `configuration.yaml` supplies `llm.url`;
- list concatenation records `provenance[key] = pkg_name` (coarse, and the docstring says so — a concatenated list has no per-item origin).

Add `load_config_with_provenance(config_dir) -> tuple[dict, dict[str, str]]` beside `load_config`, which does the same work and returns the map. Leave `load_config` byte-identical in behaviour; it becomes a two-line wrapper that discards the map.

Comment in the voice of the surrounding code, explaining *why*: this exists so the settings overlay can refuse to shadow a file the user edits under `packages/`, and so the console can name the package it would otherwise silently lose to.

**Tests** (`jarvis-core/tests/test_config.py`):
- `test_merge_packages_records_which_package_supplied_each_key` — a package supplying a new top-level key and a package merging a subkey into an existing dict both appear in the map with the package name. Revert the recording and the overlay cannot answer the question its refusal branch is built on.
- `test_load_config_is_unchanged_by_provenance_collection` — `load_config` and `load_config_with_provenance()[0]` return equal dicts for the shipped `config/`. Pins that the new path is a pure addition.

**Subject:** `config: record which package supplied each merged key`

### Commit 0.2 — tag provenance without resolving a single secret

**Files:** `jarvis-core/jarvis/config.py`

Add `load_provenance(config_dir) -> dict[str, dict]`: a second, read-only parse whose only product is `{dotted_path: {tag, env_var, env_set, yaml_default}}`.

The subtlety the reviews caught and the design missed: `_include` calls `load_yaml`, which builds `class _Loader(JarvisSafeLoader)` — a sibling of any provenance loader, not a descendant. A `_ProvLoader` subclass registering marker constructors therefore does not apply to included files, and the shipped `configuration.yaml` reaches `automations.yaml`, `scripts.yaml`, `scenes.yaml` and the whole `packages/` directory through `!include*`. So the design as written would (a) resolve real `!secret` values inside every included file and (b) report no provenance at all for exactly the keys a package supplied.

Implement it properly:

- `class _ProvLoader(JarvisSafeLoader)` with its own `!env_var` and `!secret` constructors returning a `_Tagged` marker. `!secret` records the key *name* only and never touches a value.
- `_ProvLoader.secrets = {}`, always. Even if an include path is missed, `_secret` on an empty map raises `ConfigError` rather than returning a value — fail closed by construction, not by care.
- Its own `!include` / `!include_dir_*` constructors that recurse through a private `_prov_load_yaml` building `class _L(_ProvLoader): pass`, so markers survive the whole include tree.
- Walk the result collecting markers, then discard the tree. `env_set` is `os.environ.get(name) is not None` — the name and a boolean, never the value.

Docstring states the reason: the settings UI has to be able to explain why an environment variable the user set is being ignored.

**Tests** (`jarvis-core/tests/test_config.py`):
- `test_provenance_reports_env_var_names_but_never_values_or_secrets` — fixture config where the `!secret` and the `!env_var` live **inside an `!include`d file**, not at the top level. Asserts `env_var='OLLAMA_MODEL'`, `env_set=False`, `yaml_default='qwen3:8b'`, and that the secret's plaintext appears nowhere in the serialised payload. Placing the fixture behind an include is the whole point: at the top level the test passes against the broken loader chain too.
- `test_provenance_follows_includes_and_package_directories` — a key supplied through `!include_dir_named packages` carries a `_Tagged` marker rather than `None`. Revert the custom include constructors and the source badge goes blank for precisely the values a package supplied.
- `test_provenance_never_resolves_a_secret_even_for_a_missed_path` — monkeypatch one include constructor back to `load_yaml` and assert the parse raises rather than silently resolving. Pins the empty-secrets belt to the constructor braces.

**Subject:** `config: report tag provenance without reading any secret`

---

## Phase A — editable settings in the console (8 commits)

Architecture: a sparse, allowlisted overlay in `.storage/settings.json`, merged over the parsed YAML. Never a YAML rewrite — tag provenance is discarded at parse time, so a round-trip writer would inline resolved secrets into a file dense with load-bearing comments and destroy them.

### Commit A.1 — the overlay and its spec table, as a pure module

**Files:** `jarvis-core/jarvis/settings.py` (new)

`@dataclass(frozen=True) class SettingSpec` with `key, path (tuple), label, group, type, apply ('live'|'restart'|'split'), note, validate, apply_hook, choices_hook`.

`SETTINGS: tuple[SettingSpec, ...]` — the only editable keys, a hardcoded tuple checked by set membership, mirroring the `ENTITY_UPDATE_FIELDS` precedent already in `api/common.py`:

| key | apply | note |
|---|---|---|
| `llm.model` | live | hook sets `agent.model` **and** `agent.client.model`; validator checks membership in `client.list_models()` when Ollama answers |
| `llm.options.temperature` | live | 0.0–2.0 |
| `llm.options.num_ctx` | live | int 256–131072 |
| `llm.max_tool_rounds` | live | 1–20 |
| `llm.approval_ttl` | live | 30–3600; hook sets `registry.approval_ttl` |
| `llm.timeout` | restart | baked into the shared `httpx.AsyncClient` at construction |
| `jarvis.name` / `unit_system` / `currency` / `country` / `language` | live | `/api/config` reads these per request |
| `jarvis.latitude` / `longitude` / `radius` | split | live for `person` (read per call), stale for `sun` (snapshotted at setup) |
| `jarvis.elevation` | restart | `sun` only |
| `jarvis.log_level` | live | hook calls `logging.getLogger().setLevel` |
| `voice.language` / `tts_voice` / `wake_word` | live | `VoiceData` is a non-frozen dataclass read at call time |

Every `apply_hook` resolves its target lazily through `jarvis.data.get(...)` and returns `False` when absent — `settings.py` importing `integrations.llm` would create a cycle and make core depend on an optional integration.

`class SettingsOverlay`:
- `__init__(config_dir)` builds `Store(config_dir, "settings", 1)`.
- `async_load()` re-runs **every** stored entry through the spec table and its validator, dropping and logging anything unknown, out of range or wrong-typed, and clears `pending_restart` on survivors. The stored file is untrusted input; without this, write access to one JSON file is a way around the allowlist the API enforces.
- `apply(raw, package_provenance) -> tuple[dict, list[Unapplied]]` — deep-copies `raw` and writes each patch at its path. **It never raises.** A missing parent, a non-dict parent, or a package-supplied key produces a dropped entry with a reason, returned to the caller for display. This is the single most important behavioural difference from both source designs: `apply()` raising at an unguarded call site in `Jarvis.async_setup` turns an ordinary YAML edit (commenting out the body of `voice:`) into an unbootable process with no API left to fix it from.
- `async_set` / `async_reset` / `describe(...)`.

`apply()` must not mutate its argument.

**Tests** (`jarvis-core/tests/test_settings.py`, new):
- `test_a_hostile_settings_file_is_filtered_on_load` — plant `jarvis.cors_allowed_origins`, `llm.expose`, `jarvis.http.port` and `llm.approval_ttl: "abc"` in the store, load, assert none survive and each was logged.
- `test_apply_never_raises_when_the_yaml_parent_is_gone` — overlay `voice.tts_voice` with `voice:` absent, `voice: null`, and `voice: "a string"`. Each returns a dropped entry with a reason and a usable config. Revert to raising and the box will not boot.
- `test_apply_refuses_a_package_supplied_key` — the file the user edits under `packages/` wins, and the drop reason names the package.
- `test_apply_does_not_mutate_its_argument`.

**Subject:** `settings: allowlisted overlay over the parsed YAML`

### Commit A.2 — wire the overlay in so integrations actually see it

**Files:** `jarvis-core/jarvis/core.py`, `jarvis-core/jarvis/const.py`, `jarvis-core/jarvis/__main__.py`

This is the commit that fixes the defect both settings designs shipped. `core.py` `async_setup` reads `self.config = config` and then hands the **local** `config` variable to `async_setup_integrations`. `integrations/llm/async_setup` builds `OllamaClient`, `create_http_client(timeout)`, `ConversationAgent(model=…, options=…)` and `Exposure` from that block alone; `integrations/voice` builds `VoiceData` the same way. Overlaying only `self.config` therefore leaves every LLM and voice setting inert at boot: the RESTART pill is a permanent lie, and the LIVE keys silently revert on each restart while the console reports `source: overlay`.

So:

1. In `Jarvis.__init__`, after the three registries, `self.settings = SettingsOverlay(self.config_dir)` (local import to avoid the cycle). It is an attribute beside the registries, not `jarvis.data["settings"]`, because the reload handlers must reach it without importing an integration and it has the same lifecycle as core infrastructure. `jarvis.data` stays what its comment says it is.
2. Add `Jarvis.async_install_config(config, package_provenance)`: stashes the raw dict on `self.raw_config`, calls `self.settings.apply(...)`, records dropped entries on `self.settings.unapplied`, assigns `self.config`, and **returns** the overlaid dict.
3. In `async_setup`, `await self.settings.async_load()` then `config = await self.async_install_config(config, provenance)` — rebinding the local, so the `jarvis: areas:` loop and `async_setup_integrations` both receive the overlaid dict. This must run before integration setup, so integrations are constructed from overlaid values on the first boot.
4. In `__main__.async_run`, after `jarvis.async_setup(config)` returns, re-apply `jarvis.log_level` from the effective config. `setup_logging` runs against the raw YAML thirty lines before the `Jarvis` object exists, so a UI-set `debug` silently reverts to `info` on every restart otherwise. A short `_reapply_early_consumers(jarvis)` helper, currently one key, with a comment naming the reason.
5. `const.py`: `EVENT_SETTINGS_UPDATED = "settings_updated"` beside the registry-updated events.
6. `__main__` switches to `load_config_with_provenance`.

**Tests** (`test_settings.py`):
- `test_an_overlaid_llm_setting_reaches_the_integration_at_boot` — plant `llm.model` and `llm.timeout` in the store, boot, assert `jarvis.data['llm'].model` and the shared `AsyncClient`'s timeout match the overlay. A test that only inspects `jarvis.config` cannot see this bug, which is why it is written against the constructed objects.
- `test_an_overlaid_voice_setting_reaches_VoiceData_at_boot`.
- `test_log_level_survives_a_restart` — overlay `debug`, boot, assert the root logger level. Revert the re-apply and debug logging turns itself off overnight.
- `test_a_dropped_overlay_entry_does_not_stop_startup` — the `voice:`-removed case boots, logs, and reports the key as unapplied.

**Subject:** `core: apply the settings overlay before integrations are built`

### Commit A.3 — make the three reload services re-apply the overlay

**Files:** `jarvis-core/jarvis/integrations/{automation,script,scene}/__init__.py`

Each `_handle_reload` does `jarvis.config = fresh` wholesale, so an overlay merged once vanishes the first time someone edits an automation. Replace with `await jarvis.async_install_config(fresh, provenance)`, and **extend each handler's existing try/except over the assignment and the reload**. Today the try covers only `load_config`; leaving the assignment outside it means a failure mid-install leaves `raw_config` new, `config` stale and the reload never run — a silently half-applied reload.

Take `_configs_from(jarvis, …)` from the overlaid config so an overlaid key is visible to the rebuilt automations.

Comment on each, in the surrounding voice: this assignment replaces `jarvis.config` wholesale, and without re-applying, every UI-set setting is silently dropped the first time anyone edits an automation — invisible until a restart.

**Tests:**
- `test_the_overlay_survives_every_reload_service` — set `jarvis.name` through the overlay, call `automation.reload`, `script.reload` and `scene.reload` in turn, assert `/api/config` still reports it and `agent.model` is unchanged.
- `test_a_failing_reload_leaves_the_running_config_intact` — make the install path drop an entry and assert `jarvis.config` and `raw_config` stay consistent and the automations still reloaded.

**Subject:** `automation/script/scene: re-apply the settings overlay on reload`

### Commit A.4 — shared read/write helpers

**Files:** `jarvis-core/jarvis/api/common.py`

Beside the registry helpers:

- `async def settings_payload(jarvis)` — calls `await asyncio.to_thread(load_provenance, jarvis.config_dir)` (real blocking directory I/O, exactly as the reload services call `load_config`) and returns `{settings: [...], restart_required: [...], unapplied: [...], process_time_zone: time.tzname[0], overlay_path: '.storage/settings.json'}`. Each row: `{key, label, group, type, value, source ('overlay'|'yaml'|'package'|'default'|'unapplied'), apply, yaml_value, note, provenance: {tag, env_var, env_set, yaml_default} | null, choices}`. Read-only rows for `llm.url`, `jarvis.time_zone`, `jarvis.webhook_require_auth` and `llm.expose` are included with `editable: false` — they are the keys people most need explained and least safely offered.
- `async def async_update_settings(jarvis, payload)` — `ApiError('invalid_format')` on a missing key, `ApiError('not_found', 404)` for anything outside `SETTINGS`, validator message on a bad value; then `async_set`, fire `EVENT_SETTINGS_UPDATED` with `{key, value, source, apply}` (safe to carry the value only because the allowlist admits no secret), return the refreshed row.
- `async def async_reset_settings(jarvis, payload)` — same validation, then `async_reset`, then **re-run the apply hook with the YAML/default value** so the live object does not keep the removed setting, then fire with `{key, source: 'yaml'}`.
- `SETTINGS_UPDATE_FIELDS = ('key', 'value')` next to `ENTITY_UPDATE_FIELDS`.

**Tests:**
- `test_update_rejects_a_key_outside_the_allowlist` — table-drive `jarvis.http.port`, `llm.expose.domains`, `llm.url`, `web.browser_token`, `jarvis.cors_allowed_origins`; each 404, none reaches `jarvis.config`.
- `test_reset_restores_the_yaml_value_and_reapplies_it_live` — set `llm.model`, assert `agent.model` moved, reset, assert **both** the payload and `agent.model` equal the YAML value. Revert the hook re-run and the API reports the YAML value while the running agent keeps talking to the removed model.
- `test_model_and_url_take_effect_without_a_restart` (model only; url is not editable) — asserts `agent.model` and `agent.client.model` both moved.
- `test_llm_timeout_is_reported_restart_only_and_does_not_mutate_the_client`.
- `test_a_settings_read_never_echoes_a_secret` — no `browser_token`, `approval_secret`, mqtt/camera password or token digest anywhere in the serialised payload.

**Subject:** `api: shared settings read/update/reset helpers`

### Commit A.5 — REST and websocket surfaces

**Files:** `jarvis-core/jarvis/api/rest.py`, `jarvis-core/jarvis/api/websocket.py`

Three routes on `api_router` (which carries `dependencies=[Depends(require_token)]` at line 161, so they are authenticated by construction — never `open_router`): `GET /config/settings`, `POST /config/settings/update`, `POST /config/settings/reset`, each `try/except ApiError -> raise _api_error(err)` exactly as `entity_registry_update` does. Extend the module docstring's route list.

Three `_HANDLERS` entries: `config/settings/list|update|reset`, placed after the area registry entries. No `_PUSH_HANDLERS` entry — settings writes are request/response and must produce a result frame.

**Tests:**
- `test_every_settings_route_and_command_requires_a_token` — all three REST routes 401 with no Authorization header; the websocket commands are unreachable before the handshake.

**Subject:** `api: settings routes and websocket commands`

### Commit A.6 — typed client, pure form logic, mock backend

**Files:** `jarvis-web/src/lib/jarvisClient.ts`, `jarvis-web/src/lib/settings.ts` (new), `tests/web/mock-ha.mjs`

`jarvisClient.ts`: `SettingRow` / `SettingsPayload` interfaces and `listSettings()`, `updateSetting(key, value)`, `resetSetting(key)`. Pass key and value as named fields, never spread a caller object — the file writes `id` last for exactly this reason.

`settings.ts` (node-testable; vitest is node-only and `.svelte` gets no unit coverage): `coerce(row, raw)`, `groupRows(rows)`, `isDirty(row, draft)`, `sourceLabel(row)`. `sourceLabel` is where the user's originating bug gets answered in words: *"configuration.yaml, via `!env_var OLLAMA_MODEL` — that variable is not set in jarvis-core, so the inline default is used."*

`mock-ha.mjs`: `settings` rows and an `overlay` map in `makeWorld()`, plus three `case` entries before the `default` that answers `unknown_command`. Include one row with a `provenance` block carrying `env_set: false`, one restart row, one read-only row, and reject an unknown key with `fail(msg.id, 'not_found', …)` so the console's error path is exercised.

**Tests** (`jarvis-web/src/lib/settings.test.ts`):
- `coerce rejects out-of-range and non-numeric values before sending` — temperature 3.5, `num_ctx: 'abc'`, a choice outside `choices`, and a bool row given the string `'false'`. That last one matters: `bool("false")` is truthy, and the repo already keeps two hand-written fail-closed parsers for this exact reason.
- `sourceLabel explains an env var that is set but ignored`.
- `groupRows keeps read-only rows in their group and marks them`.

**Subject:** `web: typed settings client and pure form logic`

### Commit A.7 — the settings page

**Files:** `jarvis-web/src/routes/settings/+page.svelte`, `jarvis-web/e2e/e2e.spec.ts`

Keep the Backend and Event-stream panels (the filter keeps `data-jv-filter`). Delete the now-false read-only copy. Follow the `/areas` idiom verbatim: module-scope `conn`, `$state` for rows/drafts/busy/err/hint/loading/restartRequired/serverChanged, and the `run(what, fn)` wrapper that toasts on both paths and writes an inline `<p class="err" data-testid="error" role="alert">`.

A leading Precedence panel states the rule in one line: overlay beats `configuration.yaml` beats the built-in default, and `.env` only reaches a setting when the YAML uses `!env_var`. Then one `.panel` per group, one `.row` per setting, control chosen by type (`<select>` when `choices`, `label.check` for bool, `input` otherwise), a LIVE/RESTART `.pill`, a source badge, SAVE disabled unless dirty and valid, RESET only when `source === 'overlay'`. Read-only rows render their value and provenance with no control.

Optimistic write then `await refresh()`; a failure restores the server value and reports through both channels. Subscribe to `settings_updated` and, when a key changes while its draft is dirty, add it to `serverChanged` and render a per-row `.notice` rather than clobbering the draft. Persistent notice naming the restart-required keys and the exact command. A separate notice lists unapplied keys with their reason — this is the surface that makes the non-raising `apply()` honest instead of merely quiet.

**Tests** (e2e, and the suite shares one world with `workers: 1`, so restore state before finishing):
- `settings can be changed and reset from the console` — change the model select, assert optimistic update, success toast, badge reads `overlay`, RESET appears; click RESET and assert the badge returns to `yaml`. Also drive a rejected write and assert **both** the toast and the inline `role="alert"`.
- `the settings form fits a 390px viewport with no horizontal overflow` — `scrollWidth - clientWidth <= 1` and each save/reset control's box ends within 391px. `html { overflow-x: hidden }` hides a real overflow from the naive check, which is why the bounding boxes are asserted too.

**Subject:** `web: make the settings page a real form`

### Commit A.8 — documentation

**Files:** `jarvis-core/docs/configuration.md`, `jarvis-web/README.md`

`configuration.md:7-9` currently promises "restart to apply" for everything but automations, scripts and scenes. That becomes untrue. Document the overlay, its file, the precedence rule, which keys are live, which are restart, which are split, and which are deliberately read-only and why. jarvis-web README gains the settings row in the Pages table and a sentence that a `/ws` socket can now write settings, which raises the value of the origin guard.

**Subject:** `docs: the settings overlay and the new restart contract`

---

## Phase B — model-authored settings and automations, behind a two-key boundary (8 commits)

Shape: Tier-3 approval to **store** a proposal; a separate human act on a non-model surface to **activate** it. Refusal, not escalation, on a tainted turn — because `approve_request` re-executes under the original stored `Context`, and the 900 s taint outlives the 300 s approval window, so a held request would refuse itself after the human said yes.

### Commit B.1 — make the gate fail closed everywhere, and reserve the underscore namespace

**Files:** `jarvis-core/jarvis/llm/tools.py`

Three changes, all in the registry, none introducing a tool:

1. `precheck: PreCheck | None` and `pin_required: bool = False` on the `Tool` dataclass and `register()`. In `call()`, run `precheck` **between** `purge_expired()` and `requires_approval` — a refusal must never become a `PendingRequest`. An exception in the precheck refuses.
2. `_pinned_arguments`: today a raising `pin` degrades to approving by name — the one non-fail-closed spot in the gate. With `pin_required=True` it raises `PinFailed`, caught in `call()`, returning the refusal shape. Existing tools that do not set the flag are unchanged, so `lock_control` keeps today's behaviour.
3. **Strip the reserved namespace centrally.** `call()` does `arguments = dict(args)` with no schema validation — there is no voluptuous or jsonschema anywhere in the tree, so a tool's declared `parameters` are documentation to the model, not a filter. A pin only overwrites the keys it names, so a model-supplied `_yaml` survives into `PendingRequest.arguments` and onto the approvals page. In `_request_approval`, before pinning, drop every argument key starting with `_`, then apply the pin's overrides on top. That makes the reserved namespace unreachable for **every** gated tool rather than for the two that remembered — I confirmed no existing tool uses an underscore-prefixed argument, so nothing regresses.

**Tests** (`jarvis-core/tests/test_llm.py`, beside `test_tier3_lock_tool_never_executes_directly`):
- `test_a_failing_pin_refuses_instead_of_approving_by_name`.
- `test_a_precheck_refusal_never_becomes_a_pending_request`.
- `test_a_precheck_that_raises_refuses`.
- `test_model_supplied_underscore_arguments_never_reach_the_approval_payload` — call a gated tool with `_diff`, `_yaml`, `_paths`, `_domains` and `automation_id` supplied and assert none of the model's values survive. Revert the central strip and the model authors the text the human reads while something else executes.

**Subject:** `llm: pre-gate refusal, required pinning, reserved argument namespace`

### Commit B.2 — taint the conversation, not only the turn

**Files:** `jarvis-core/jarvis/api/devices.py`, `jarvis-core/jarvis/llm/agent.py`

The taint set is keyed on `Context.id`, and the agent mints a fresh `Context(origin="llm")` per turn. `_finish` persists the assistant's own reply — the model's paraphrase of whatever it read — into conversation history as an **assistant** message, and that is spliced back into the next turn's prompt. So an injected instruction survives the turn in the second-highest-trust role while the taint does not, and turn N+1 passes every tripwire cleanly.

Add a conversation-scoped mark alongside the existing turn mark: `mark_untrusted_conversation(jarvis, conversation_id)` and `conversation_is_untrusted(jarvis, conversation_id)`, same TTL semantics, TTL running from the last fenced read. The agent passes `conversation.id` alongside the `Context` when a tool result is marked.

Deliberately narrow: **only the Phase B write prechecks consult conversation taint.** `control_device`, `undo_last_action` and `memory.remember` keep their current per-turn behaviour, so no existing test changes and no existing UX regresses. The cost is that a settings change needs a fresh conversation after browsing; that is the price of the invariant being true.

**Tests** (`jarvis-core/tests/test_device_control.py`):
- `test_a_conversation_that_read_a_page_stays_marked_across_turns`.
- `test_the_conversation_mark_expires_on_the_same_ttl_as_the_turn_mark`.
- `test_existing_per_turn_consumers_are_unchanged` — `control_device` still escalates on a tainted turn and does not refuse on a merely tainted conversation.

**Subject:** `devices: mark the conversation, not only the turn, as untrusted`

### Commit B.3 — the settings write policy

**Files:** `jarvis-core/jarvis/settings/policy.py` (new package; move `jarvis/settings.py` from Phase A into `jarvis/settings/__init__.py` in the same commit, keeping the public names)

`WRITABLE_PATHS` is Phase A's `SETTINGS` — one allowlist, not two. `DENIED_PATHS` is an independent second check evaluated **after** the allowlist, so a careless allowlist edit still cannot admit a denied path. It covers network binding (`jarvis.http.host/port`, `jarvis.host/port`), API exposure (`jarvis.cors_allowed_origins`, `jarvis.webhook_require_auth`), LLM blast radius (`llm.expose` and every descendant), approval settings (`llm.approval_ttl` — writable by a human, never by the model), new-capability surfaces (`llm.tools`, `llm.tools_dir`, top-level `tools`, `command_line`, `rest`, `template`), self-modification (`llm.persona`, `llm.persona_file`, `llm.url`), secrets by name and by `*.password`/`*.token`/`*.api_key`/`*.secret` suffix, camera consent and credentials, execution surfaces (`automation`, `script`, `scene`), loader escapes (`secrets`, `packages`) and audit erasure (`recorder.exclude`, `logbook.exclude`).

Auth is out of reach structurally rather than by listing: tokens live in `.storage/auth.json` and no config path maps to them; the module never imports `AuthManager`.

`check_path(path, value) -> str | None`, failing closed on any unexpected shape. `render_diff(current, proposed, paths)` producing a `difflib.unified_diff` over `yaml.safe_dump` of the touched subtrees only — the first `yaml.safe_dump` in the tree, and it produces display text and store payloads, never a file. `redact(config)` for the read tools.

Note the tighter model surface than either design: the model's allowlist is Phase A's minus `llm.approval_ttl`. The registry's own approval TTL is not something the model gets to propose changing.

**Tests** (`test_settings.py`):
- `test_every_denied_path_is_refused_and_never_echoed` — table-drives every entry through the policy and through `redact`.
- `test_a_denied_path_is_still_refused_after_being_added_to_the_allowlist` — monkeypatch `llm.expose` into `WRITABLE_PATHS` and assert it is still refused. Revert the independent second check and a careless edit silently re-opens exposure.
- `test_render_diff_never_opens_configuration_yaml` — hash the file before and after, assert identical bytes and that `!env_var OLLAMA_MODEL` and a known comment line are still present.

**Subject:** `settings: model-write policy, allowlist plus independent denylist`

### Commit B.4 — re-gate at dispatch, not at the service call

**Files:** `jarvis-core/jarvis/automation/actions.py`, `jarvis-core/jarvis/automation/engine.py`

Both source designs install the run-time policy hook inside `ScriptRunner._async_call_service`, immediately after the service name is rendered, and rest their answer to "are they re-gated at execution?" on that. I checked `_async_dispatch` and two branches never reach that function:

- the `scene:` shorthand calls `jarvis.async_call_service("scene", "turn_on", …)` directly;
- the `event:` step calls `bus.async_fire(event_type, data, context)` with an arbitrary type and arbitrary data — no service call at all, so no hook and no contribution to `collect_domains` either. That is enough to fire any `platform: event` automation *and* to forge `EVENT_CALL_SERVICE` records into the very ledger this feature depends on for review, poisoning what `undo_last_action` believes the last action was.

So the hook goes in `_async_dispatch`, before the branch, where every step type is screened by construction and a future step type cannot silently opt out.

Add `policy: ServicePolicy | None` to `ScriptRunner.__init__`, stored on the runner. `ServicePolicy.check(step_kind, domain, service, data)` is a small frozen dataclass holding the denylist plus the gated-domain rule; it fails closed on exception. Crucially it derives domains from **every entity id it can find anywhere in `data`, recursively, including mapping keys** — not just from the service name. That is what makes the run-time gate independent of the static one rather than a weaker copy, and it is what catches `scene.apply` with `data.entities: {lock.front_door: unlocked}`.

`engine.py`: read a reserved `_jarvis_managed` key in `Automation.__init__` (stripped from any model-supplied config by the validator), store `self._policy`, and pass `policy=self._policy` to the `ScriptRunner`. YAML automations get `policy=None` and are byte-for-byte unchanged.

Also extend `collect_domains`: a `scene` key whose value is a template must report `DOMAIN_UNKNOWN` rather than the literal domain `"scene"`, and `scene.apply` must contribute the domains of its `data.entities` keys. Today `if "scene" in node: found.add("scene")` does not inspect the value at all.

**Tests** (`jarvis-core/tests/test_automation.py` and `test_settings.py`):
- `test_a_scene_shorthand_step_is_screened_by_the_policy` — a policy-carrying automation with `- scene: "{{ target }}"` is refused at run time. Revert the move to `_async_dispatch` and the hook is never consulted.
- `test_an_event_step_is_refused_for_a_policy_carrying_automation`.
- `test_scene_apply_entities_are_screened_by_domain` — `data.entities: {lock.front_door: unlocked}` is refused at run time even though the service domain is `scene`.
- `test_collect_domains_reports_unknown_for_a_templated_scene_step`.
- `test_yaml_automations_are_unaffected` — a `policy=None` runner behaves exactly as before.

**Subject:** `automation: screen every step type, not only service calls`

### Commit B.5 — authored-automation store, validator and transitive analyser

**Files:** `jarvis-core/jarvis/settings/authored.py` (new)

`transitive_domains(jarvis, sequence)`: seed with `collect_domains`, then expand `script.<name>` through `Script.domains`, `scene.turn_on` through `Scene.domains`, `automation.trigger` through the target's own transitive set (depth-capped, cycle-guarded, `DOMAIN_UNKNOWN` on overflow), and `scene.apply` through its `data.entities` keys. This is the analysis those two docstrings were written for and which nothing consumes today.

`validate(jarvis, config) -> str | None`, refusing on the first of:
1. a model-supplied `id` or `automation_id` — ids are minted here as `authored_<uuid4hex[:12]>`, because two configs slugifying alike get the same `entity_id`, the second evicts the first from `AutomationManager.automations`, and the orphan's triggers stay attached with no way to detach them;
2. any trigger platform outside `{state, numeric_state, time, time_pattern, sun}` — `webhook` because `/api/webhook/{id}` is open by default and a model-chosen id would be the only secret on an internet-reachable actuation; `jarvis_start`, `event`, `mqtt`, `template` as immediate or attacker-reachable fire paths;
3. `time_pattern` intervals under 60 s;
4. `transitive_domains` intersecting `GATED_DOMAINS`, or containing `DOMAIN_UNKNOWN`;
5. any service on `SERVICE_DENYLIST`, headed by `llm.approve` (an authored automation calling it approves the registry's own held Tier-3 requests — a complete, direct escape that would make every gate in the tree decorative), plus `scene.apply`, `conversation.process`, `orchestrator.*`, `command_line.*`, `automation.*`, `*.reload`, `settings.*`, `homeassistant_compat.*`;
6. any `event:` step at all;
7. any entity target failing `registry.exposure.is_exposed` — refused without naming the hidden entity, following `undo`'s precedent;
8. a construction smoke test: build `Automation(jarvis, config)` in a try/except and discard it, because `int(config["max"])` raises out of `async_add` and the load precedes every `services.register` call, so one bad row costs the automation services entirely.

`AuthoredStore` over `Store(config_dir, "authored_automations", 1)`, entries `{id, config, enabled, domains, created_at, actor, request_id, yaml}`.

`async_load_into(manager)` sets `config["initial_state"] = bool(entry["enabled"])` — **not** a hardcoded `False`. "Created disabled" is a property of the write path, not the load path. `engine.py` reads `initial_state` at construction and `automation.turn_on` only mutates the live object, so forcing `False` on load silently re-disables every rule the human enabled, on every `automation.reload`, with nothing logged and the store still saying `enabled: true`. Each entry is added inside its own try/except.

`async_enable(id, actor)` / `async_disable(id, actor)` flip the stored flag and call `automation.turn_on/off`.

**Tests:**
- `test_an_authored_automation_calling_llm_approve_is_refused`.
- `test_an_authored_automation_reaching_lock_through_a_script_is_refused` — action `service: script.goodnight` where that script's sequence calls `lock.unlock`. Revert the transitive expansion and `collect_domains` reports `{script}` and the laundering path documented in `docs/security.md` becomes model-authorable.
- `test_scene_apply_is_refused_at_authoring`.
- `test_a_webhook_or_jarvis_start_trigger_is_refused`, including `time_pattern: {seconds: '/5'}`.
- `test_an_enabled_authored_automation_stays_enabled_across_reload` — enable, `automation.reload`, assert state is `on` and it still fires. The mirror test for the disabled case goes in too, but this one is the one that catches the bug.
- `test_a_malformed_stored_automation_does_not_take_down_the_integration` — plant `{"max": "abc"}`, boot, assert the good entries loaded and `automation.reload` is still registered.

**Subject:** `settings: authored-automation store, validator and transitive analysis`

### Commit B.6 — audit ledger

**Files:** `jarvis-core/jarvis/settings/audit.py` (new)

`_AUDIT = logging.getLogger("jarvis.settings.audit")`, mirroring the web integration, plus a persisted capped ring (2000 entries) in `Store(config_dir, "settings_audit", 1)`.

`EVENT_TOOL_CALLED` is unusable as the trail: it fires only on the success path of `_execute`, so refusals, denials and expiries leave no trace. One record per lifecycle event — `proposed`, `refused`, `approved`, `denied`, `expired`, `applied`, `apply_failed`, `enabled`, `disabled`, `runtime_refused` — carrying `at`, `kind`, `decision`, `actor` (context id, origin, and the token id from `api_context`), `tool`, `request_id`, `paths`/`automation_id`, the verbatim diff or YAML, the model's `reason` clearly labelled as model-written, and the computed domain set.

Also fire `jarvis_settings_changed` and `jarvis_automation_authored` on the bus so they land in the recorder and logbook. Subscribe to `EVENT_APPROVAL_REQUIRED`/`EVENT_APPROVAL_RESOLVED` and reconcile on a periodic sweep, because `purge_expired` drops silently and an expiry is otherwise invisible.

**Tests:**
- `test_the_audit_ledger_records_refusals_and_denials_not_only_writes` — drive proposed/refused/approved/denied/expired and assert a row for each with actor and request_id.
- `test_the_ledger_is_capped`.

**Subject:** `settings: append-only audit ledger for every decision`

### Commit B.7 — the settings integration and the human surfaces

**Files:** `jarvis-core/jarvis/integrations/settings/__init__.py` (new), `jarvis-core/jarvis/const.py`, `jarvis-core/jarvis/api/{common,rest,websocket}.py`

`const.py`: `DOMAIN_CONFIG = "config"` and extend `GATED_DOMAINS` to `{lock, notify, config}`. Belt and braces with `tier=TIER_APPROVAL`, exactly as `lock_control` carries both — deleting either declaration still leaves the tool gated. Verified safe against the other two consumers: `_gate_targets` resolves entity ids and no `config.*` entities exist, and `undo` refusing to undo gated actions is desirable here.

Shared precheck for both write tools, in this order: refuse if the **conversation** is untrusted; refuse if `is_fenced(json.dumps(args, default=str))` — a second, independent tripwire copying `steps_carry_fenced_content`, so a future integration that fences but forgets to mark still cannot write config (`is_fenced`, the broad tripwire, never `is_wrapped`); then the validator.

`propose_settings_change(changes, reason)` and `propose_automation(alias, description, trigger, condition, action, reason)`, both `tier=TIER_APPROVAL, domain="config", pin_required=True, precheck=_precheck`. Their pins set `_diff`/`_paths` and `_yaml`/`_domains` respectively — safely, now that the registry strips the underscore namespace before pinning. Both handlers re-validate against the denylist before writing, because `approve_request` executes stored arguments minutes later and the policy may have changed.

Read tools `list_settings`, `get_setting`, `list_proposed_automations` at `tier=TIER_DIRECT`, allowlist-filtered, mirroring `TokenInfo.as_dict`: `get_setting("web.browser_token")` answers "no such setting", not a value.

**Services: read-only.** Register `settings.get` and `settings.list_authored` and nothing else. `settings.set` and `settings.enable_automation` are **not** registered as services. `services.async_call` has exactly one rejection path (`ServiceNotFound`) and consults nothing else — the design's own rationale says so — and `run_script` is ungated, so a capability registered there is reachable from any YAML script the model can invoke. A second key whose security property is "the model cannot reach it" must not live on the substrate documented to authorize nothing.

`api/common.py`: `async_settings_write`, `async_authored_list`, `async_authored_enable`, `async_audit_read`, `async_pending_requests` — shared bodies mirroring `async_approve`. A human at the console is their own approval, so no `PendingRequest` is created; the write is audited with `actor = token id`. `async_authored_enable` parses its flag through `common.approval_flag`, never `bool()`.

`api/rest.py`: `GET/POST /api/jarvis/settings`, `GET /api/jarvis/automations/authored`, `POST /api/jarvis/automations/{id}/enable`, `GET /api/jarvis/audit`, `GET /api/jarvis/pending` — all on `api_router`. `api/websocket.py`: the matching `_HANDLERS` entries.

**Tests:**
- `test_a_tainted_conversation_cannot_propose_a_settings_change` — `web_fetch` in turn 1, `propose_settings_change` in turn 2 of the same conversation; `status == "refused"` and `pending_requests() == []`.
- `test_a_tainted_turn_cannot_propose_an_automation`, additionally asserting the authored store is empty.
- `test_a_fenced_proposal_is_refused_even_on_a_clean_conversation`.
- `test_the_settings_tools_are_tier_three_and_config_domain` — including an argument literally named `tier`.
- `test_no_service_can_write_settings_or_enable_an_automation` — walk `jarvis.services.services` and assert nothing in it reaches `SettingsStore.async_write` or `AuthoredStore.async_enable`.
- `test_enabling_an_authored_automation_is_not_reachable_from_any_tool` — walk `registry.tools`, and assert that with `expose: domains: [automation]` configured the generic `turn_on` tool still cannot flip the stored flag.
- `test_the_approval_payload_carries_the_verbatim_diff` — the `+` lines equal the values later written.

**Subject:** `settings: tier-3 proposal tools and token-guarded human surfaces`

### Commit B.8 — approvals console, proposals panel, docs

**Files:** `jarvis-web/src/routes/approvals/+page.svelte` (new), `jarvis-web/src/routes/automations/+page.svelte`, `jarvis-web/src/lib/{jarvisClient,shortcuts,commandPalette}.ts`, `jarvis-web/src/routes/+layout.svelte`, `tests/web/mock-ha.mjs`, `jarvis-web/e2e/e2e.spec.ts`, `jarvis-core/docs/security.md`

Nothing consumes `jarvis_approval_required` anywhere in the tree today, so without this commit every proposal expires silently after 300 s and the feature reads as broken rather than as safe.

New `/approvals` page on the `/areas` shape. On mount, `pendingRequests()` then subscriptions to `jarvis_approval_required` and `jarvis_approval_resolved`; teardown unsubscribes before closing. **What the human sees is `arguments._diff` / `arguments._yaml` verbatim**, as text in a `<pre>` inside its own `overflow-x: auto` container — never `{@html}`. The model's `reason` renders separately in a `.notice` labelled "Jarvis says", so a model-written sentence is never mistaken for the change. A request carrying neither `_diff` nor `_yaml` shows a refusal card and a DENY button only, following the `web.browse` no-steps rule: if there is nothing to show, there is nothing that can be approved. Countdown from `expires_at`; APPROVE and DENY with aria-labels.

`/automations` gains a "Proposed by Jarvis — not running" section showing verbatim YAML and computed domains, whose ENABLE calls `enableAutomation` — never `callService('automation', 'turn_on')`, so widening `llm: expose: domains: [automation]` can never reach it.

Register `/approvals` in NAV, `CHORDS` as `g p`, `PAGE_ITEMS`, and the README Pages table. Wrap every new command in the `isUnsupported` degradation path. Add the mock cases plus a `pending` array and a `jarvis_approval_required` broadcast; leave one command answering `unknown_command` so the degradation path stays covered.

`docs/security.md` gains the authored-automation section next to the existing `run_script`/`activate_scene` note, stating the four independent controls and that authoring a *script* is refused precisely because `run_script` is ungated.

**Tests:**
- `approvals page renders the diff verbatim and denies fail closed` (e2e) — the `<pre>` matches the payload character for character, the model's `reason` sits in a separately-labelled notice, DENY sends `approved: false`, and a payload with no `_diff` shows DENY only.
- `approvals route is registered everywhere a route must be` — extend the existing duplicate-chord/duplicate-destination unit test.

**Subject:** `web: approvals page and the proposed-automations panel`

---

## Phase C — QR device pairing (8 commits)

The QR carries a URI, never a credential of lasting value:

```
jarvis://pair?v=1&u=http%3A%2F%2F192.168.2.10%3A8080&c=<27-char base64url>
```

A URI beats JSON: shorter, parseable without a JSON parser in a security path, and it self-identifies so the phone can tell a pairing payload from the bare token an older workflow produces. Nothing is registered as an Android deep link.

### Commit C.1 — pairing model, limiter, and the claim proof

**Files:** `jarvis-core/jarvis/pairing.py` (new)

Shaped deliberately like `auth.py`. `PairingCode` (id, label, code_hash, created_at, expires_at, created_by, claimed_at, claimed_by, claimed_ip, token_id, failures) whose `as_dict()` omits the digest. `PairedDevice` (id, label, device_id, device_name, platform, app_version, token_id, paired_at, claimed_via, claimed_ip) — every claimant-supplied string control-char-stripped and truncated on the way in, not just `claimed_via`.

`PairingManager`: `Store(config_dir, "pairing")`, `purge_expired()` at the top of every public method, `async_create(label, ttl, created_by)` returning `(code, secret)` with only a SHA-256 digest stored, TTL clamped 30–120 s (see the decision on the 600 s ceiling below), `MAX_PENDING = 5`.

`async_claim(code, device, nonce, claimed_via, ip)` under an `asyncio.Lock`, iterating every pending code with `hmac.compare_digest` and **no early exit**, marking the code claimed *before* awaiting the mint so two concurrent claims cannot both win, and returning `(secret, token_id, proof)` where

```
proof = HMAC-SHA256(key=code_secret, msg=nonce || device_id || token_id)
```

That proof is the fix for the largest hole in the original pairing design: the claim is otherwise a one-way bearer exchange against an operator-typed address with nothing proving the responder minted the code. `LanHost.checkUrl` returns a permitting verdict for any LAN host before it ever consults the acknowledged-cleartext set — I checked, `if (cls.isLan) return Verdict(true, …)` precedes it — so every validator on the phone passes for a spoofed `jarvis.local`, and all of them only check the URL's *shape*. With the proof, a responder that does not know the code cannot produce it, and the phone stores nothing.

`ClaimLimiter(limit_per_ip=5, window=60, clock=time.monotonic)` — **per-IP only, as a rejection**. The global counter becomes a logging threshold, never a refusal. A global rejection window on an unauthenticated route is a permanent kill switch: 20 junk claims a minute from one address exhausts it, the operator's phone gets 429 for the whole life of every code, and the 160-bit code never needed a global bound anyway. Grinding is bounded instead by a per-code failure budget: 10 wrong attempts burns that code and tells the console why.

`async_setup_pairing(jarvis, …)` / `get_pairing(jarvis)`, copied in shape from `auth.py`.

**Tests** (`jarvis-core/tests/test_pairing.py`, new):
- `test_a_pairing_code_is_stored_only_as_a_digest`.
- `test_the_claim_proof_is_computed_over_the_nonce_device_and_token`.
- `test_a_concurrent_double_claim_mints_exactly_one_token`.
- `test_a_code_burns_after_its_failure_budget_and_says_so_to_the_console`.
- `test_one_client_cannot_rate_limit_another` — 20 failures from one IP, a claim from another still succeeds. Revert to a global window and any LAN peer holds a kill switch on pairing.
- `test_claimant_supplied_strings_are_truncated_and_stripped`.

**Subject:** `pairing: single-use codes, per-code budget, and a claim proof`

### Commit C.2 — minting needs a second secret

**Files:** `jarvis-core/jarvis/pairing.py`, `jarvis-core/jarvis/__main__.py`, `docker-compose.yml`, `jarvis-core/.env.example`

Both pairing mitigations in the source design lean on the `/ws` origin guard. I read it: `if (origin === undefined || origin === null || origin === '') return true;`, in both `backend.ts` and its hand-copy in `ws-proxy.js`, and the docstring states the intent — a missing Origin means a non-browser client. The guard therefore protects against exactly one attacker, a hostile web page, and by construction protects against none of the others. The attacker who converts transient LAN reach into a permanent token is a script: it opens `ws://host:8199/ws` with no Origin, the relay attaches `JARVIS_TOKEN`, it mints a code and claims it from a plain HTTP client that also sends no Origin.

So minting requires `JARVIS_PAIRING_SECRET`, held by jarvis-core, typed by the operator into the console panel and passed on `jarvis/pair/create`. Unset means minting is disabled and the panel says so — fail closed, the same direction as `require_token`. This is the pattern jarvis-browser already uses to keep possession of the API token from being enough to approve a payment.

Wire `async_setup_pairing` into `async_run` immediately after `async_setup_auth` and **above** the `--create-token` early return, so it stays out of that path exactly as the existing comment intends for uvicorn.

**Tests:**
- `test_minting_requires_the_pairing_secret` — a create with no secret, a wrong secret, and no configured secret each refuse and mint nothing.
- `test_the_pairing_secret_is_never_echoed_by_any_read`.

**Subject:** `pairing: mint behind a second secret the relay does not hold`

### Commit C.3 — REST surface, and make revocation actually revoke

**Files:** `jarvis-core/jarvis/api/rest.py`, `jarvis-core/jarvis/api/websocket.py`, `jarvis-core/jarvis/api/common.py`

On `api_router`: `POST /pair/codes` (the only time the code is transmitted, with the comment mirroring `create_token`'s), `GET /pair/codes`, `DELETE /pair/codes/{id}`, `GET /pair/devices`, `DELETE /pair/devices/{id}`.

On `open_router`, beside the webhook route: `POST /api/pair/claim`, in strict order — refuse any request carrying an `Origin` header (browsers always send one, phones never do); per-IP rate limit with `Retry-After`; 503 if pairing or auth is unset, never mint; claim, catching `PairingError` into one byte-identical 400 for unknown, expired and already-used. Success returns `{access_token, token_id, proof, device_name, pipeline, server:{name, version, ha_version}}`. A dedicated `jarvis.pairing.audit` logger records successes and failures with the client IP; the secret and the code never appear in a log line.

**`GET /pair/devices` is built by joining `auth.list_tokens()` with the pairing records, not from the pairing store alone.** `Store._load_sync` catches `JSONDecodeError` and `OSError` and returns `None`, so a truncated write or a partial restore leaves every paired full-privilege token live and invisible, with no token UI anywhere in the console to revoke from. Joining means every stored token appears — labelled `paired` when a record matches and `unknown token` when it does not — each with a REVOKE that calls `auth.revoke` directly. A corrupt store surfaces as a banner, never as an empty list rendered as "no devices".

Live-socket registry: after `self.user_id = info.id`, add the handler to `jarvis.data["ws_sessions"]`; discard it in `_release()`. Module-level `close_sockets_for_token(jarvis, token_id)` sends `auth_invalid` and closes. Called by `async_revoke_device` **and** by the existing `DELETE /api/auth/tokens/{id}`, which today leaves a revoked token's open socket fully authorised until it happens to reconnect.

Websocket: `jarvis/pair/create|list|cancel|devices|revoke` in `_HANDLERS`, delegating to the same `common.*` helpers. Deliberately no `jarvis/pair/claim` — a claiming client cannot get past `_authenticate`, and adding a pre-auth command would put an unauthenticated door in the handshake.

**Tests** (`test_pairing.py`, `tests/test_api.py`):
- `test_claiming_mints_a_token_and_burns_the_code`.
- `test_unknown_expired_and_used_codes_are_indistinguishable`.
- `test_the_claim_endpoint_refuses_a_request_carrying_an_origin_header`.
- `test_revoking_a_paired_device_kills_its_token_and_its_live_socket` — pair, open a socket, register a device, revoke, assert the socket closed and a fresh handshake gets `auth_invalid`.
- `test_a_corrupt_pairing_store_still_lists_every_token_as_revocable`.
- `test_pairing_reads_never_return_a_secret`.
- `test_every_pairing_route_needs_a_token_except_claim`.

**Subject:** `api: pairing routes, and make token revocation hang up live sockets`

### Commit C.4 — pairing payload builder and QR tokens

**Files:** `jarvis-web/src/lib/pairing.ts` (new), `jarvis-web/src/lib/styles/tokens.css`, `jarvis-web/src/lib/tokens.ts`, `jarvis-web/src/lib/jarvisClient.ts`

`buildPairingUri({url, code})` — trims trailing slashes, requires http/https, rejects loopback with "the phone cannot reach a loopback address — use the address it will dial", rejects a code outside `/^[A-Za-z0-9_-]{16,64}$/`, fixed parameter order as part of the cross-language fixture. `suggestPairUrl(backendUrl, pageOrigin)` returns `''` for loopback, because compose sets `JARVIS_URL=http://127.0.0.1:8080` and the console genuinely does not know the address the phone will use. `countdown(expiresAt, now)` with injected `now`.

`--jv-qr-dark: #04070c;` and `--jv-qr-light: #ffffff;` on `:root`, one declaration per line as the tokens test's parser requires, mirrored into `TOKENS`. These deliberately do not follow the theme: scanners expect dark-on-light and many refuse an inverted symbol.

Client methods `createPairingCode(label, ttl, secret)`, `listPairingCodes`, `cancelPairingCode`, `listPairedDevices`, `revokePairedDevice`.

`src/lib/qr.ts` and `qr.test.ts` are already tracked and OpenCV-round-trip-verified (I confirmed with `git ls-files`; the codebase map saying otherwise is stale). Reuse it. Writing a second encoder is unjustifiable and the CSP forbids the alternatives.

**Tests:**
- `buildPairingUri produces the exact fixture string and refuses a loopback address` — pins the byte-for-byte payload the Kotlin parser is tested against.
- `the QR colour pair is opaque and stays dark-on-light in both themes` — 7:1 over each other, neither an `rgba()`.

**Subject:** `web: pairing payload builder and QR colour tokens`

### Commit C.5 — the pairing panel

**Files:** `jarvis-web/src/routes/settings/+page.svelte`, `tests/web/mock-ha.mjs`, `jarvis-web/e2e/e2e.spec.ts`

A panel on `/settings` rather than a new route: a new route costs NAV, CHORDS, PAGE_ITEMS, README and e2e entries, and `/settings` is already where a user looks for this.

Controls: label, address (prefilled from `suggestPairUrl`), pairing secret (password-typed, never persisted client-side), PAIR A DEVICE. On success, `{@html qrSvg(buildPairingUri(...), {...})}` inside an `overflow-x: auto` wrapper — safe because the string is built by our own code and `qrSvg` escapes every attribute, so the payload only reaches path geometry. The QR re-renders when the address changes without minting again; the code is server-side, the address is not.

Below it: a live countdown, the code behind a REVEAL toggle for the no-scanner fallback, and a one-line warning that anyone who can see the screen can use it until it expires. Subscribe to `jarvis_pairing_claimed`/`jarvis_pairing_expired`.

**The claimed state shows the operator's own label plus the client IP and `claimed_via` — never the claimant's `device.name` as the headline.** The stolen-QR story in the source design rests on the operator noticing that their own claim failed; but the claimant chooses `device.name`, the console would render "Paired · Pixel 8 · android" as the success state the operator was waiting for, and the operator's phone then reports the deliberately-generic "unknown, expired or already-used" — the same sentence it would print if they had simply been slow. Showing facts the claimant cannot forge is what makes the theft visible.

The paired-devices panel lists label, platform, device id, last used, claimed-via, claimed-from, and a `.btn.danger` REVOKE per row, including rows for tokens with no pairing record.

Mock: the five commands, a `jarvis_pairing_claimed` broadcast, and a rejected mint with no secret so the console's error path is exercised.

**Tests:**
- `the pairing panel renders a QR, hides the code until revealed, and reports the claimant's IP not its chosen name` (e2e).
- `a mint without the pairing secret reports through both channels`.

**Subject:** `web: pairing panel on the settings page`

### Commit C.6 — the phone's payload parser

**Files:** `android-app/app/src/main/kotlin/ai/jarvis/app/config/PairingPayload.kt` (new), its JVM test, `android-app/tools/pairing_payload_test.py` (new)

No Android imports — `java.net.URI` and `URLDecoder` only, so it is a plain JVM unit test and mirrorable in Python, exactly like `ServerUrl.kt` and `LanHost.kt`. All rules fail closed: scheme `jarvis`, authority `pair`, `v` exactly `1`, `u` percent-decoded then run through the *existing* `ServerUrl.check` (a QR must never relax the cleartext rule — this is the same validator the typed path uses), `c` matching the base64url charset, whole payload capped at 512 chars, any ISO control character rejects. Decode only `u`, never `c`.

**Tests:** the Kotlin test and the Python mirror assert the same fixture and the same rejection table — `v=2`, `javascript:`, a control character, a 600-char payload, `http://` to a public host. Revert one implementation and the three drift apart, which is the failure `policy_truth_table_test.py` already exists to prevent. The Python mirror runs in `make test-android` with no SDK.

**Subject:** `android: pure pairing-payload parser with a python mirror`

### Commit C.7 — the claim client

**Files:** `android-app/app/src/main/kotlin/ai/jarvis/app/config/PairingClaim.kt` (new)

Before dialling, `LanHost.checkUrl(url, acknowledgedCleartextHosts = emptySet())` — an empty ack set on purpose: `ChannelConfig` states the ack list is the user's own and must never be populated from the network, and a QR is the network.

OkHttp with `connectTimeout(5s)`, `readTimeout(10s)`, `followRedirects(false)`, `followSslRedirects(false)` — a 30x would carry the code to another host. POST `{code, nonce, device:{id,name,platform,app_version}}` with no `Origin` header. On 200, recompute `HMAC-SHA256(code, nonce||deviceId||tokenId)` and compare with `MessageDigest.isEqual`; a mismatch returns `Refused` and stores nothing. Outcomes: `Paired`, `Rejected(message)` on 400 surfacing the server's generic string verbatim, `RateLimited(retryAfter)`, `Unreachable(reason)` distinguishing `UnknownServiceException` ("this Android build refuses plain http to <host>; add it to `res/xml/network_security_config.xml` or put HTTPS on jarvis-core") from `SSLHandshakeException` ("install your private CA on this device"). Token-shaped values go through `Redact.token`.

**Tests** (`androidTest`, MockWebServer):
- `aClaimWhoseProofDoesNotVerifyStoresNothing` — this is the mDNS-spoof test. Revert the proof check and a QR becomes a way to hand the phone a server the user never typed, which then holds the phone's AUTO-tier dispatcher and the `http_request` SSRF exemption.
- `aRejectedClaimIsReportedVerbatimAndStoresNothing`.
- `theCleartextRefusalIsReportedSpecifically`.

**Subject:** `android: claim a pairing code and verify the server's proof`

### Commit C.8 — settings screen wiring and docs

**Files:** `android-app/app/src/main/kotlin/ai/jarvis/app/SettingsActivity.kt`, `android-app/app/src/androidTest/.../SettingsPersistenceTest.kt`, `jarvis-core/docs/{clients,security}.md`, `jarvis-web/README.md`, `android-app/README.md`

In `onActivityResult`, try `PairingPayload.parse` first. On a payload, claim off the main thread with progress. On a parse failure, keep today's behaviour **only** when the string looks like a bare token (no `://`, no whitespace, 20–200 chars); otherwise toast "That QR is not a Jarvis pairing code."

On `Paired`, write atomically and only then: `config.serverUrl = check.normalized` (the normalised form, never the raw QR string), `config.token`, and `deviceName`/`pipeline` from the response **only when the local value is still the default** — a server must not rename a device its user named. Then the same three refreshes `save()` performs, for the same reasons its comment gives: `ActionEnv.refreshFromConfig` caches the jarvis-core host that is the single exemption `http_request` has from its SSRF guard; `DeviceChannelHost.configChanged()` because the channel builds an immutable snapshot per connect; `JarvisAutomationService.ensureRunning`. Repopulate the fields.

Relabel the button SCAN TO PAIR. Do **not** add an intent-filter — keeping `SettingsActivity` `exported="false"` means a `jarvis://pair` URI can only arrive from a scan the user initiated.

Docs: `clients.md` gains a Pairing section with the payload grammar, a worked example, the six endpoints and the explicit statement that no token is ever carried in a QR. `security.md` records the exposure window, single use, the Origin refusal, the per-code budget, the pairing secret, and the standing limitation that a minted token is full-privilege because jarvis-core has one privilege level.

**Tests:** extend `SettingsPersistenceTest` with `aFailedPairingClaimStoresNeitherUrlNorToken`, mirroring the existing refused-URL assertions.

**Subject:** `android: scan to pair, and store nothing unless the claim verifies`

---

## Phase D — porting to the other clients (6 commits)

The port is not symmetric, because the three "clients" are not three clients.

**jarvis-browser gets nothing.** It has no human surface — nine FastAPI routes called by core — and its config is deliberately an env-only, extend-only ratchet so a half-typed env var cannot un-gate `checkout`. It holds two deliberately different secrets so that holding the API token is not enough to approve a payment.

**jarvis-desktop gets a documentation line.** Its founding claim is that the server cannot widen anything and that no action writes config, policy or the file-roots list; a remote settings write path inverts the program.

**Only Android is a real port target**, and it splits by cost-to-keep-correct: reading and switching house automations goes native (four calls on a socket that is already open and already authenticated); everything that *authors* opens the console, because an authoring validator must exist once, in one language.

### Commit D.1 — the contract, and a test that fails when it drifts

**Files:** `jarvis-core/docs/clients.md`, `jarvis-core/tests/test_packaging.py`

Extend the Commands table with every command Phases A–C added: exact `type` string, request fields with types, result shape, error codes. Add the versioning rule explicitly: a client receiving `unknown_command` MUST hide the feature, never surface it as an error, never fail open. Correct the Android paragraph, which currently says pointing `ManagementActivity` at the jarvis-web origin gives the phone the same console — it requires a separate console URL setting, and the core bearer token is not sent there.

`test_every_websocket_command_is_documented` — import `_HANDLERS`, parse the table, assert set equality both ways with an explicit skip-list for the device-channel frames documented elsewhere. `test_packaging.py` already runs cross-file guards of exactly this shape.

**Subject:** `docs: document every websocket command and pin it with a test`

### Commit D.2 — a console URL, stored separately from the core URL

**Files:** `android-app/.../config/JarvisConfig.kt`, `SettingsActivity.kt`, `androidTest/.../SettingsPersistenceTest.kt`

`ManagementActivity` loads `config.serverUrl` — jarvis-core — and core serves a JSON index there because `jarvis-core/www` does not exist and jarvis-web cannot be a static bundle: it needs its Node process for the token-hiding relay. The fallback the whole port leans on is broken, and fixing it comes first.

Add `consoleUrl` backed by a new key, defaulting to `serverUrl`'s host at port 8199 (the reference compose layout) so an existing install keeps working without typing. Validate with the **same** `ServerUrl.check` as the server URL — one transport policy, one place it is decided — and refuse the whole save on failure. Add the field to the screen. Update the three-way hand-copy self-check for the prefs file name if a new mirror is needed.

**Tests:** `consoleUrlIsValidatedAndARefusedOneStoresNothing` and `consoleUrlDefaultsFromTheServerHostWhenUnset`, mirroring the existing refused-server-URL case.

**Subject:** `android: a console URL, validated like the server URL`

### Commit D.3 — point the WebView at the console and stop sending it the token

**Files:** `android-app/.../ManagementActivity.kt`, `androidTest/.../ManagementOriginTest.kt` (new)

Load `config.consoleUrl`, and compute `serverOrigin` from that same value so `isAllowed()` locks to the origin actually loaded — otherwise, with console and core on different hosts, the lock refuses the page it just loaded or permits an origin it is not showing.

Replace both `loadUrl(url, mapOf("Authorization" to "Bearer ${config.token}"))` calls with a plain `loadUrl(url)`. jarvis-web has no bearer auth and never reads the header; sending it hands the key to the whole house to a different origin for nothing. Delete the class-doc paragraph claiming core turns the first authenticated request into a session — core has no session mechanism (`allow_credentials=False`, no cookie code). Accept an optional `EXTRA_PATH` so callers can deep-link `/settings` or `/automations`. Leave `addJavascriptInterface` absent and its comment intact: settings editing landing in the page is exactly the pressure that would justify a native config bridge, and the answer is no.

**Tests:** `managementActivityLoadsTheConsoleWithoutAnAuthorizationHeader` and `managementActivityOriginLockFollowsTheConsoleUrl`.

**Subject:** `android: load the console, not core, and send it no bearer token`

### Commit D.4 — the house-automation seam

**Files:** `android-app/.../channel/ChannelFrames.kt`, `automation/tasks/HouseAutomations.kt` (new), `automation/JarvisAutomationService.kt`, `channel/DeviceLink.kt`, `channel/DeviceChannelHost.kt`, `android-app/tools/house_commands_test.py` (new)

`ChannelFrames.codeOf(msg)` beside `errorOf` (which flattens code and message into one display string) plus `ERROR_UNKNOWN_COMMAND`. Without a structured code the phone cannot distinguish "this backend is older" from "this call failed", and the degradation rule cannot be implemented.

`HouseAutomationsClient` — `isConnected`, `list()`, `setEnabled(entityId, on)`, `runNow(entityId)` — with a result type that can carry an `unsupported` flag distinct from a failure. It lives in the automation module so the dependency keeps pointing channel → automation. `AutomationRuntime.house` beside `deviceEvents` and `askJarvis`.

`DeviceLink` implements it over `channel.request(...)`, exactly as `ask()` already does: `get_states` filtered to `automation.`, and `call_service` with `turn_on|turn_off|trigger`. `DeviceChannelHost` sets and clears it beside the existing wiring.

`house_commands_test.py` in the shape of `channel_protocol_test.py`: the exact frames, matched against the documented command types; `unknown_command` maps to unsupported-and-hidden; and `setEnabled(false)` never emits anything but `automation.turn_off`, so a refactor cannot turn a user disabling a rule into a user firing it.

**Subject:** `android: house-automation client over the existing device channel`

### Commit D.5 — the HOUSE section and the server panel

**Files:** `android-app/.../automation/ui/AutomationsActivity.kt`, `SettingsActivity.kt`

`refresh()` today is two local disk reads and draws nothing until its single `replaceContent` at the end of the coroutine. Adding a `channel.request` inside it puts the whole screen — TASKS, the PAUSE button, every consent toggle, the PANIC line — behind a network round trip that does not return null when the socket is up but the server is busy; it waits out the full timeout. And `toggleEnabled` and `runNow` both end in `refresh()`, so every local consent toggle would pay for a `get_states`.

So render in two passes: `replaceContent` from local state plus the last house snapshot immediately, then a second render when a background `list()` returns something different. Three distinct house states — loading, unsupported/disconnected, loaded — and never an empty list rendered as "none configured". The mutation paths do not refetch server state.

Rows show name, entity id, last triggered, an ON/OFF ghost, and a RUN NOW ghost whose hint says it skips the automation's conditions, because `automation.trigger` defaults `skip_condition: true`. An EDIT IN CONSOLE ghost opens `ManagementActivity` with `EXTRA_PATH = "/automations"`. Update the class doc: the screen now shows two disjoint things, and `setEnabledByUser` remains reachable only from the TASKS rows.

`SettingsActivity` gains a read-only SERVER panel — location name, version, time zone, config source, fetched once via `get_config` — plus a SERVER SETTINGS ghost opening `/settings`. Nothing here is natively editable. One hint line distinguishing the two URL fields.

**Tests:** an `androidTest` asserting the TASKS block renders before any house call resolves, and the Python mirror's `test_unknown_command_hides_the_section_and_is_not_an_error`.

**Subject:** `android: house automations as a second section, rendered in two passes`

### Commit D.6 — make the cross-surface suites run, and fix the docs that are now wrong

**Files:** `Makefile`, `docs/architecture.md`, `android-app/README.md`, `jarvis-desktop/README.md`, `jarvis-browser/README.md`, `DEVIATIONS.md`, `jarvis-core/tests/test_packaging.py`

`make test` is `test-python` only today, so a protocol change between core, the console and the phone is caught by nothing. Add `test-contract` running the conformance test plus `test-android`, and add `test-android` to `test`'s prerequisites. Every anti-drift mechanism in this plan exists but is never executed until this lands.

Add `test_the_console_origin_is_not_in_the_browser_lan_allowlist` to `test_packaging.py`. This is the dependency both settings and pairing quietly rest on: jarvis-browser's SSRF guard blocks RFC1918 and loopback, and `lan_allowlist`/`act_allowlist` are empty tuples by default — that, not the origin guard, is what stops a prompt-injected model from driving a scriptable Chromium at the console's own write surface. The test fails if jarvis-web's host or port appears in either list, and the docs say why.

Fix `android-app/README.md:17` (ManagementActivity is a window onto jarvis-web, not core's own UI). `jarvis-desktop/README.md`: the management console is jarvis-web at :8199, and jarvis-desktop deliberately has no settings UI and no remote write path, citing its existing invariant. `jarvis-browser/README.md`: no settings surface by design, sensitive lists extend-only. `docs/architecture.md`: the two Android origins. `DEVIATIONS.md`: core settings are not natively editable on Android, with the reason.

**Subject:** `build: run the contract and android suites in make test`

---

## Decisions where the inputs disagreed

**Overlay, not a YAML rewrite.** All four designs agree and the code forces it: `!env_var` resolves to a plain `str` at parse time, there is no writer in the tree, and a comment-preserving library still could not recover which scalar came from which environment variable. A round-trip writer would inline `JARVIS_BROWSER_TOKEN` into a file users paste into issues.

**`llm.url` is not editable — Design 2 wins over Design 1.** Design 1 calls it "an exfiltration lever" and ships it because the user asked. It is worse than that: `agent.client.url` is re-read every round, `run_script` and `activate_scene` are registered with no tier, domain or gate, and both are in `DEFAULT_EXPOSED_DOMAINS` — so whoever answers inference authors the tool-call stream and reaches `lock` with no human, persistently, across restarts. That makes the `llm: expose:` invariant Design 1 boasts about unenforceable.

**`llm.model` stays editable, but validated server-side against `list_models()`.** This is the user's originating bug and the headline of the feature; dropping it guts the deliverable. Constraining the value to a model the *configured* endpoint actually serves removes the redirect property while keeping the feature.

**`jarvis.time_zone` and `jarvis.webhook_require_auth` are read-only rows, not editable.** Design 1 offers time zone as editable-but-inert with a note and `webhook_require_auth` as narrowing-only. An inert control and a one-way toggle are both traps. They appear on the page with full provenance and a warning when the process zone differs — which is the information the user actually needs — and no control.

**Refuse a tainted turn rather than escalate.** Design 2's reasoning holds and I verified the mechanics: `approve_request` executes with the request's stored `Context`, and the 900 s taint outlives the 300 s approval window, so an escalated proposal would refuse itself after the human said yes.

**Taint is conversation-scoped for the write prechecks, turn-scoped everywhere else.** Attack 1 is right that the per-turn mark does not cover the model's own persisted reply. Widening it globally would change `control_device` and break existing tests for no gain in this feature, so only the Phase B prechecks consult it.

**Reserved-namespace stripping is central, in `_request_approval`, not per-pin.** Attack 3's finding: a pin only overwrites keys it names, there is no schema validation anywhere, and Design 2's two pins each strip only their own reserved keys. Central stripping covers every gated tool, including ones not yet written.

**The run-time policy check goes in `_async_dispatch`, not `_async_call_service`.** Both designs put it in the latter. I read the dispatcher: the `scene:` and `event:` shorthands never reach it.

**`settings.set` and `settings.enable_automation` are not services.** Design 2 registers them, then argues elsewhere that the service registry authorizes nothing and that `run_script` is ungated. Attack 1 is right that this puts the second key on the one substrate the design itself identifies as unguarded.

**Package provenance is restored rather than the claim dropped.** Both attacks are right that `merge_packages` destroys it. Design 1 concedes and lives with the overlay silently shadowing a package; Design 2 claims a refusal it cannot implement. Five lines in `merge_packages` make the claim true and let the source badge name the package, so both findings die at once.

**`apply()` never raises.** Design 1 specifies `SettingsError` at an unguarded call site in `async_setup`. Two attacks independently walked it to an unbootable process recoverable only by hand-deleting a file nobody knows about.

**The overlay is applied to the dict handed to `async_setup_integrations`, not only to `self.config`.** Neither settings design does this. Verified: `core.py` passes the local variable, and `integrations/llm` builds the client, the agent and the shared `AsyncClient` from its own block. This was the single largest defect in the inputs.

**`initial_state` on load comes from the stored flag.** Design 2 forces `False` unconditionally. Created-disabled is a property of the write path; forcing it on load silently re-disables every enabled rule on each reload, and the design's own test only pins the disabled case, so it would pass while the bug ships.

**Pairing minting requires a second secret; the origin guard is not cited as a mitigation.** Verified `isOriginAllowed` returns `true` for a missing Origin *by design*, in both copies. The guard is inert against the script that is the actual threat. `JARVIS_PAIRING_SECRET` mirrors jarvis-browser's existing two-secret split.

**The claim is mutually authenticated by an HMAC over the shared code.** No design has the responder prove anything. Verified `LanHost.checkUrl` permits LAN cleartext before consulting the acknowledged set, so a spoofed `jarvis.local` passes every validator on the phone.

**Rate limiting is per-IP and per-code; the global window becomes logging only.** A global rejection window on an unauthenticated route is a kill switch, and the 160-bit code never needed one.

**The paired-devices list is joined from `auth.list_tokens()`.** `Store` fails silently to `None` on a corrupt read, and there is no other token UI in the console.

**Pairing TTL is clamped to 120 s and the "a code never outlives the panel" invariant is dropped.** Svelte teardown does not run on tab close or a closed lid, and the cancel is fire-and-forget on a socket the same teardown closes. The TTL is the real bound, so it is made short enough to be one.

**The Android HOUSE section renders in two passes.** Design 4 puts the network call inline in `refresh()`, which is also the mutation path for every local consent toggle.

**QR rendering stays client-side and reuses `src/lib/qr.ts`.** It is tracked (the map is stale), OpenCV-verified, and its own docstring names this feature. Server-rendering would only defend against hostile JS already on the console origin, which can mint its own code anyway.

---

## Contested findings — attack claims I am not acting on

**Attack 1's `remember` laundering channel is wrong.** It states that `tool_remember` "accepts `context` and ignores it". It does not. `integrations/memory/__init__.py` calls `turn_is_untrusted(jarvis, context)` and refuses, with a comment saying precisely why: this is the only model-reachable write that outlives the turn, `looks_fenced` does not survive paraphrase, and `source` defaults to a trusted value. The write side is already closed. The *conversation-outlives-the-turn* concern in its sibling finding is real and is handled in Commit B.2; the `remember` fix is not needed and is not planned.

**Attack 1's claim that the `/api/pair/claim` Origin guard is only cosmetic is not made, and Attack 2 is right that it is load-bearing** — jarvis-core defaults to `allow_origins=["*"]` with `allow_credentials=False`, so without the refusal any page could read a minted token. Kept as specified.

---

## Deliberately not doing

**Not making `jarvis: time_zone:` real.** It would need `os.environ['TZ']` plus `time.tzset()` process-wide, affecting every `datetime.now().astimezone()` including the recorder and templates, and `triggers.py` sleeps the whole interval so already-armed triggers keep the old offset until they next fire. A setting that appears to work and moves schedules eight hours later is worse than a labelled read-only row.

**Not gating `run_script` and `activate_scene`.** The transitive analyser built in Commit B.5 is the two-line fix and `tests/test_llm.py` has to change with it. It is a real, documented hole, but closing it changes the behaviour of tools users rely on today and belongs in its own change with its own test migration. The inconsistency is stated in `docs/security.md`: an authored automation calling `script.goodnight` is refused while the model may still call `run_script('goodnight')` directly.

**Not adding a policy hook inside `services.async_call`.** Correct in the long run, blast radius across every call in the tree, and it does not stop a proposal being *stored*.

**Not letting the model author scripts.** `run_script` is ungated, so a model-authored script is a model-authored ungated action.

**Not enforcing the device-id binding in `jarvis/device/register`.** A minted token is recorded against the announced device id, but any authenticated socket may still claim any id. Enforcement is a behaviour change to `ConnectedDevices.register`; recorded as a risk in `security.md`, not implemented.

**Not scoping tokens.** jarvis-core has one privilege level: `verify()` returns a `TokenInfo` no route consults for scope. Pairing narrows the blast radius (per-device, revocable, hangs up live sockets) but does not scope it. Real scoping is a separate feature and the docs say so.

**Not adding a login to jarvis-web.** Reachability remains authority; the origin guard, the firewall, and now the pairing secret are the controls.

**No settings write path for jarvis-desktop.** Its stated invariant is that the server cannot widen anything and no action writes config, policy or the file-roots list. A remote settings endpoint inverts the program. It gets a documentation line pointing at the console.

**No settings surface for jarvis-browser.** Its API token and approval secret are deliberately different so that holding the token cannot approve a payment, and `BROWSER_SENSITIVE_KEYWORDS`/`SELECTORS` extend only. A settings endpoint there is a way to un-gate `checkout`.

**Not wiring `jarvis_approval_required` to the Android `ApprovalBridge`.** The phone has exactly the right consumer already — fail-closed, keyguard-gated, verbatim params, no memory, 60 s cap — and it is wired to `device_command` only. It is the highest-value adjacent Android work and it is deliberately not smuggled into a settings port; the `/approvals` console page in Commit B.8 is this feature's consumer.

**No shared Field/Form component in jarvis-web.** Three editable pages arriving at once is the moment that decision gets locked in, and there is no component unit-test harness to protect a shared component if one is introduced. Each page keeps the hand-rolled `.row` idiom until there is a fourth.

**No per-tool approval TTL.** 300 s is plausibly short for reading a config diff, but changing it is a registry-wide change with its own test surface. The `/approvals` countdown makes the window visible; the audit sweep records expiries rather than letting them vanish.

**No `Store` migration helper.** Nothing in the tree has one and only the memory integration passes a non-default version. The first schema change to `settings.json`, `authored_automations.json` or `pairing.json` needs one written from scratch; recorded in `DEVIATIONS.md`.