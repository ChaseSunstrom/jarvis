# Android action registry and policy engine

Everything Jarvis can do to the phone, what tier it carries, and what it needs
to be granted first.

The server is where the LLM lives. The LLM reads web pages, notifications and
screen text, so it can be wrong and it can be injected. **The phone therefore
decides for itself what is allowed to run.** The tier in an incoming
`device_command` is a hint that can only make things stricter; the table below
is the authority, and it lives in
`app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/Builtins.kt`.

---

## The three tiers

| tier | wire | meaning | gate |
|---|---|---|---|
| `AUTO` | 1 | read-only or trivially reversible | runs immediately |
| `NOTIFY` | 2 | changes device state, recoverable | asks once, then remembers if the user says so |
| `CONFIRM` | 3 | irreversible, costs money, or reaches another person | full-screen prompt with the verbatim action, params and reason — **every single time** |

Rules enforced in code (`policy/PolicyEngine.kt`, mirrored by the executable
spec at `tools/policy_truth_table_test.py`):

* effective tier = `max(local, requested)`. The server can raise. It can never
  lower. A missing or malformed `tier` field contributes nothing.
* `UserPolicy.NEVER` denies, always, before anything else is considered.
* Tier 3 always asks. `ALLOW_ALWAYS` does **not** bypass it, and the answer to
  a Tier 3 prompt is never remembered — `PolicyEngine.canRemember()` returns
  false and the store refuses to write it. The store's guard does not depend on
  the caller passing a tier: `PolicyEngine.mayStore()` looks the id up in the
  local action table and treats an **unknown** tier as Tier 3, so
  `setPolicy(id, ALLOW_ALWAYS)` cannot write a standing yes for `send_sms`.
* Denied or timed out ⇒ status `denied`, and the action's `execute()` is never
  called. A hung or crashed consent UI is a denial too.
* Policy is re-read after the prompt is answered. If the user hit panic, killed
  the master switch or set `NEVER` while the prompt was on screen, the approval
  is discarded — consent to run is not a licence that outlives the kill switch.
* Panic flag and the master switch deny everything, ahead of every other rule.
* A request marked `TrustLevel.UNTRUSTED` — anything derived from page,
  notification, clipboard or screen content — can never be auto-allowed. The
  best outcome it can reach is a fresh human approval.
* Every dispatch writes one line to the audit log, whatever the outcome —
  including "unknown action" and "unsupported", which are refused before any
  policy work and therefore never prompt.
* `tierFor(params)` and `isAvailable()` are called through `safeTierFor` /
  `safeAvailable`: an action that throws while being classified is treated as
  Tier 3 / unavailable, never as safe.

Run the specs:

```bash
python3 android-app/tools/policy_truth_table_test.py   # the truth table
python3 android-app/tools/dispatch_spec_test.py        # the dispatcher's ordering
python3 android-app/tools/action_table_test.py         # tiers vs the brief and these docs
```

`policy_truth_table_test.py` pins `PolicyEngine.decide`. That is necessary and
not sufficient: "a Tier-3 action never runs without a human approving *this*
invocation" is a property of the ORDER of steps in `ActionRegistry.dispatch`, and
a dispatcher that consulted the engine and then executed anyway would pass every
check in that file. `dispatch_spec_test.py` models the dispatcher as a state
machine, runs 1152 dispatches through it, asserts what actually executed, and
structurally checks that the Kotlin still performs those steps in that order.

---

## The actions

Permissions are runtime unless noted. Every action re-checks its own
permissions and returns `permission <name> not granted` rather than throwing —
a denied permission is an answer, not a crash.

### Device and system

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `get_device_state` | 1 | — | `ACCESS_NETWORK_STATE` (install-time); `ACCESS_FINE_LOCATION` for the SSID only | battery, charging, network type, VPN, metered, wifi SSID, screen on, power save, ringer, DND, per-stream volumes, free storage, model, SDK, uptime |
| `set_volume` | 1 | `stream` (music/ring/alarm/notification/call/system), `level` 0-100 | — | changing ring/notification while DND is on needs DND access; returns a clear error instead |
| `set_ringer_mode` | 2 | `mode` normal/vibrate/silent | notification-policy access for vibrate/silent | special access, granted in Settings, not a runtime permission |
| `toggle_dnd` | 2 | `enabled`, or `filter` off/priority/alarms_only/total_silence | notification-policy access | `open_settings_panel` with `panel=dnd_access` takes the user there |
| `set_brightness` | 2 | `level` 0-100 | `WRITE_SETTINGS` (special access) | forces auto-brightness off; system brightness, not per-window |
| `toggle_torch` | 1 | `on` | — | `CameraManager.setTorchMode`, API 23+, no camera permission needed; unavailable on devices with no flash |
| `vibrate` | 1 | `duration_ms`, or `pattern_ms` array | `VIBRATE` (install-time) | `VibratorManager` on API 31+, `Vibrator` below |
| `get_location` | 1 coarse / **2 fine** | `accuracy` coarse\|fine, `max_age_ms`, `timeout_ms` | `ACCESS_COARSE_LOCATION`, plus `ACCESS_FINE_LOCATION` for fine | `LocationManager` only — GrapheneOS has no Play Services, so there is no fused provider. `accuracy=fine` raises the tier to 2 via `tierFor()` |
| `get_sensors` | 1 | `type` (optional), `timeout_ms` | `ACTIVITY_RECOGNITION` for `steps` | with no `type`, lists every sensor; with one, takes a single reading |

### Apps and intents

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `launch_app` | 1 | `package` or `name` | — | resolving by `name` needs package visibility (below) |
| `open_url` | 1 public, **3 inside the trust boundary** | `url` | — | **http/https only.** `intent:`, `file:` and `content:` are rejected — an `intent:` URL can start arbitrary components. A URL aimed at loopback, the LAN, link-local or a metadata endpoint is raised to Tier 3 by `tierFor()`: the browser makes that request from the user's own network carrying the user's cookies, so `http://192.168.1.1/reboot` is shown before it happens |
| `share_text` | 2 | `text`, `subject` | — | opens the system share sheet |
| `start_navigation` | 2 | `destination`, or `latitude`+`longitude` | — | `geo:` intent, so OsmAnd / Organic Maps handle it; no Google Maps dependency |
| `dial` | **3** | `number` | — | `ACTION_DIAL` only pre-fills the dialer, but anything aimed at a person confirms |
| `open_settings_panel` | 1 | `panel` | — | the sanctioned route to every toggle apps may no longer flip |
| `list_installed_apps` | 1 | `query`, `limit` | — | launcher-visible apps only, subject to package visibility |
| `kill_app` | — | `package` | — | **unsupported**, deliberately. Returns `unsupported` without prompting |

`panel` accepts: `internet`, `wifi`, `wifi_settings`, `bluetooth`, `nfc`,
`volume`, `location`, `display`, `sound`, `battery`, `apps`, `app_info`,
`accessibility`, `dnd_access`, `notification_access`, `write_settings`,
`airplane`, `data_usage`, `vpn`, `date`, `developer`.

### Media

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `media_play` `media_pause` `media_next` `media_previous` `media_stop` | 1 | — | — | uses `MediaSessionManager` when notification-listener access is registered on `ActionEnv`, otherwise `AudioManager.dispatchMediaKeyEvent`, which needs nothing |
| `set_media_volume` | 1 | `level` 0-100 | — | same as `set_volume` with `stream=music` |

### Communications

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `send_sms` | **3** | `number`, `body` | `SEND_SMS` | multipart split automatically; emergency numbers refused; needs telephony hardware |
| `place_call` | **3** | `number` | `CALL_PHONE` | emergency numbers refused — Android does not allow `ACTION_CALL` to dial them anyway |
| `read_contacts` | 2 | `query`, `limit` | `READ_CONTACTS` | read-only, but it is other people's data. Results are marked `untrusted` |
| `send_notification` | 1 | `title`, `text`, `priority` | `POST_NOTIFICATIONS` (API 33+) | local only; nothing leaves the phone. Uses a framework icon, so this module needs no app resources |

### Calendar and clock

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `read_calendar` | 1 | `days_ahead`, `start`, `limit` | `READ_CALENDAR` | results marked `untrusted` — an invitation body is attacker-controlled text |
| `create_calendar_event` | 2 | `title`, `start`, `end` or `duration_minutes`, `description`, `location`, `calendar_id` | `WRITE_CALENDAR` (optional) | with the permission it inserts directly; without it, falls back to opening the calendar app's editor pre-filled |
| `set_alarm` | 2 | `time` "HH:MM" or `hour`+`minute`, `label`, `days`, `vibrate` | `com.android.alarm.permission.SET_ALARM` (install-time) | `AlarmClock` intent with `EXTRA_SKIP_UI`; the clock app decides how to honour it |
| `set_timer` | 2 | `seconds` or `minutes`, `label` | same | same |

`start` / `end` accept epoch millis, epoch seconds, ISO-8601 with or without an
offset, a bare date, or a relative offset such as `+90m` (`actions/TimeParse.kt`).

### Files and clipboard

Every file action is confined to `filesDir/jarvis_files`. There is no absolute
path mode and no SAF picker. Two independent checks:
`PathScope.normalize()` resolves `..` arithmetically and rejects escapes,
percent-encoded separators, backslashes, null bytes and absolute paths; then
`FileSandbox.resolve()` verifies the `canonicalPath` is still inside the root,
which is the only thing that catches a symlink.

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `read_file` | 1 | `path`, `max_bytes`, `base64` | — | app-private storage only; result marked `untrusted` |
| `write_file` | 2 | `path`, `content`, `append`, `base64` | — | 5 MiB cap |
| `list_files` | 1 | `path` | — | `path` may be empty for the root |
| `delete_file` | **3** | `path`, `recursive` | — | not recoverable, so it confirms |
| `read_clipboard` | 2 | — | — | Android 10+ only gives the clipboard to the focused app, the default IME or an accessibility service; otherwise this returns a clear error. Result marked `untrusted` |
| `write_clipboard` | 2 | `text`, `sensitive` | — | `sensitive=true` hides it from the Android 13+ clipboard preview |

### Network

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `http_request` | 2 GET/HEAD, **3 otherwise** | `url`, `method`, `headers`, `body`, `content_type`, `max_bytes` | `INTERNET` (install-time) | see the SSRF guard below. A write method is "submitting a form", so `tierFor()` raises it to Tier 3 |

The guard (`actions/SsrfGuard.kt`, unit-tested):

1. Scheme allowlist (`http`, `https`), no credentials in the URL, no control
   characters.
2. The host is blocked when it is written as loopback, private (RFC1918),
   CGNAT, link-local, multicast, reserved, or a metadata name — in any
   spelling, including `2130706433`, `0177.0.0.1`, `[::ffff:127.0.0.1]` and
   `metadata.google.internal`.
3. If the host is a name, every address it resolves to is re-checked before the
   socket opens.
4. Redirects are not followed automatically; each `Location` goes through steps
   1-3 again, at most 3 hops.

The single exemption is `ActionEnv.jarvisServerHost` — the jarvis-core server
we already trust over the WebSocket. Known residual risk: DNS rebinding between
our check and the platform's own resolution. Closing it needs connect-by-IP
with a `Host` override, which breaks TLS verification, so it is not done; the
response body is treated as untrusted regardless.

### Shell

| id | tier | params | permission | notes |
|---|---|---|---|---|
| `run_shell` | **3** | `command` or `args`, `timeout_ms` | Shizuku permission | ADB-level (uid 2000), **never root**. Shizuku is an optional dependency reached entirely by reflection; without it the action reports `unsupported` with instructions |

`timeout_ms` is enforced by `Process.waitFor`, and both pipes are drained on
bounded daemon threads (64 KiB each) rather than read to EOF first. That
ordering is not stylistic: a blocking `InputStream.read` does not answer to
coroutine cancellation, so draining stdout before waiting made the timeout
unenforceable for every command that keeps its stdout open — `logcat`, `top`, a
tail — and the dispatcher's own `withTimeout` could not have rescued it either.
`destroyForcibly()` closes the pipes, which is what unblocks the readers.

### Screen and UI automation (delegated)

Not implemented in this module. The accessibility agent implements
`UiAutomationDelegate` and registers it on `ActionEnv.uiDelegate`; what lives
here is the id list and the tier each one carries. With no delegate registered
these return `unsupported` with "enable the Jarvis accessibility service",
never a silent no-op.

| id | tier | params | notes |
|---|---|---|---|
| `ui_click` | **3** | `text`, `content_description`, `view_id`, `index` | taps are how forms get submitted |
| `ui_type` | **3** | `text`, `view_id`, `clear` | typing is how credentials get entered |
| `ui_scroll` | 2 | `direction`, `amount` | moves the view, commits nothing |
| `ui_read_screen` | 2 | `include_invisible` | read-only, but it reads *everything* on screen. Returns `untrusted` content |
| `ui_wait_for` | 2 | `text`, `view_id`, `timeout_ms` | same |
| `ui_back` `ui_home` `ui_open_recents` | 2 | — | global navigation |
| `take_screenshot` | 2 | `save` | accessibility global screenshot action (API 30+). No MediaProjection path — that would put a persistent screen-capture consent in front of the user for something an automation triggers |

---

## What is deliberately impossible on modern Android

None of these are missing features. Android removed them, and the sanctioned
alternative is listed. Jarvis says so honestly instead of pretending.

| wanted | why not | sanctioned alternative |
|---|---|---|
| Toggle wifi | `WifiManager.setWifiEnabled` is a no-op for apps since Android 10 (API 29) | `open_settings_panel` with `panel=internet` (the Android 10+ internet panel) or `wifi`; or `run_shell` with `svc wifi enable` via Shizuku |
| Toggle bluetooth | `BluetoothAdapter.enable()/disable()` deprecated and no-op for apps from Android 13 (API 33) | `open_settings_panel` with `panel=bluetooth`; or `svc bluetooth enable` via Shizuku |
| Toggle mobile data | needs `MODIFY_PHONE_STATE`, a signature permission | `open_settings_panel` with `panel=internet`; or `svc data enable` via Shizuku |
| Toggle airplane mode | `WRITE_SECURE_SETTINGS`, signature-level, since Android 4.2 | `open_settings_panel` with `panel=airplane`; or Shizuku |
| Force-stop / kill another app | no public API; `killBackgroundProcesses` only hints at your own | `kill_app` returns `unsupported`; `open_settings_panel` with `panel=app_info`, or `am force-stop <pkg>` via Shizuku |
| Silent install / uninstall | requires being a device owner or holding `INSTALL_PACKAGES` | `ACTION_INSTALL_PACKAGE` intent with the user tapping through, or `pm install` via Shizuku |
| Read another app's notifications | needs a `NotificationListenerService` plus explicit user grant | out of scope for this module; the notifications agent owns it, and its content is untrusted |
| Take a screenshot without accessibility | `MediaProjection` needs a per-session consent dialog | accessibility global action (`take_screenshot`) |
| Read the clipboard in the background | blocked since Android 10 | bring Jarvis to the foreground, or use the accessibility service |
| Grant itself a permission | not a thing | ask the user; `open_settings_panel` with `panel=app_info` |
| Root | not needed, not asked for | Shizuku gives ADB-level reach after an explicit user grant, and reports itself as exactly that |

---

## Wiring (for the other agents)

### Build once at startup

```kotlin
val registry = Builtins.standard(applicationContext)
```

That one call does all of the wiring, because every piece of it is silent when
forgotten:

| what | why it is not left to the caller |
|---|---|
| `PolicyStore` + `AuditLog` + `UiApprovalGateway` | the three collaborators the dispatcher needs |
| `PolicyStore` gets the action table (`Builtins::tierOf`) | so its Tier-3 guard works without a caller-supplied tier |
| `ActionEnv.refreshFromConfig(ctx)` | jarvis-core host (the one SSRF exemption), notification-listener component, app version |
| `AutomationBridge.dispatcher = registry.asBridgeDispatcher()` | **`JarvisChannel` reads this slot for every `device_command`. Empty, it answers `unsupported` and the phone does nothing at all.** |

Only the accessibility service fills in a slot of its own, when it connects:

```kotlin
ActionEnv.uiDelegate = accessibilityBridge
```

Call `ActionEnv.refreshFromConfig(ctx)` again after the user edits the server
URL or grants/revokes notification access.

### WebSocket client

```kotlin
// register
"capabilities" to registry.capabilities()               // only what is usable right now
// tools for the server-side LLM
registry.manifest()                                     // JSONArray: id, tier, description, params, …
// a device_command arrives:
val reply = registry.handleCommand(commandJson)         // full device_result frame, ready to send
socket.send(reply.toString())
```

`handleCommand` parses `tier` with `ActionTier.fromWire`, so the client never
has to interpret it — and cannot accidentally let it lower anything.

Statuses map straight through: `ActionResult.status.wire` is one of `ok`,
`denied`, `error`, `unsupported`.

For anything triggered by content rather than by the user or the server, call
`dispatch(..., trust = TrustLevel.UNTRUSTED)`. That is the structural half of
"untrusted text is data, not instructions": such a request can never be
auto-allowed, only approved fresh by a human.

### Which results ARE content

`markUntrusted()` on a result flags the payload for the server. That is only
half the job, because the server is the thing we are defending against. The
machine-readable half is on the action itself:

```kotlin
registry.producesUntrustedOutput("http_request")   // true
```

and the same flag appears in the manifest as `untrusted_output`. It is true for
`http_request`, `read_file`, `read_clipboard`, `read_contacts`, `read_calendar`,
`run_shell`, `list_installed_apps`, `ui_read_screen`, `ui_wait_for` and
`take_screenshot`; an id the registry has never heard of answers `true`.

**The task runner must taint a `store_as` variable when the action that filled
it has this flag**, so a later step interpolating that variable dispatches
`TrustLevel.UNTRUSTED`. Without that, `http_request` → `store_as: page` →
`open_url {{page.body}}` is a web page choosing an action with no human in the
loop, which is precisely the thing the trust level exists to prevent.
`tools/action_table_test.py` fails if an action calls `markUntrusted()` without
declaring the flag.

### Consent UI

`ActionRegistry` calls `ApprovalGateway`. The default implementation,
`UiApprovalGateway`, is the module's only reference to the UI layer, and it
calls `ai.jarvis.app.ui.ApprovalBridge.request(...)`:

```kotlin
suspend fun request(
    context: Context,
    actionId: String,
    description: String,
    params: Any?,          // VERBATIM — serialised and displayed as-is
    tier: Any?,            // display only
    reason: String,        // untrusted server text — displayed, never obeyed
    commandId: String?,
    rememberable: Boolean,
    timeoutMs: Long,       // clamped to at most ApprovalBridge.TIMEOUT_MS (60s)
): String                  // "approved" | "denied" | "timeout"
```

Anything unrecognised — including an empty string from a crashed prompt — is
read as `denied`. Fail closed.

The bridge never answers `approved_always`: its prompt has no "always allow"
control. A Tier-2 action therefore keeps asking until the user sets
`ALLOW_ALWAYS` for it on the settings screen, which writes straight to
`PolicyStore`. Tier 3 can never be set that way at all — `PolicyStore.remember`
and `setPolicy` both refuse it, and `PolicyEngine` would ignore it anyway.

To swap the UI out entirely, pass your own gateway to
`Builtins.standard(context, approvals = …)`.

### Policy storage keys

`PolicyStore` writes SharedPreferences file `jarvis_policy`, keys
`policy.<action_id>` with values `allow_always` / `ask` / `never` (lower case),
plus `automation_enabled` and `panic`. `JarvisConfig.Policy` mirrors these
constants for the settings UI; reads are case-insensitive and unknown values
fail closed to `ask`, so either side may write.

### Accessibility service

```kotlin
interface UiAutomationDelegate {
    val supportedActions: Set<String>   // ids from UiAutomationDelegate.ALL
    fun isReady(): Boolean              // service enabled AND connected
    suspend fun perform(actionId: String, params: JSONObject): ActionResult
}
```

Policy is already enforced before `perform` is called — a Tier 3 `ui_click`
only arrives after a human approved that exact invocation. Mark any text taken
off the screen with `markUntrusted()`.

### Audit log

JSONL at `filesDir/jarvis/audit.jsonl`, capped at 5000 entries and rotated in
place. One line per dispatch: timestamp, action, redacted params, enforced
tier, decision, status, ok, error, source, `command_id`, duration, and the
policy explanation. `AuditLog.read()` for the UI, `readJson()` for a list
adapter, `clear()` for the user's wipe button.

Params are redacted on the way in, by key: anything that tokenises to `token`,
`password`, `pin`, `otp`, `code`, `secret`, `key`, `cvv`, `session` and friends
becomes `[redacted]`, and long values are truncated to 256 characters.
Over-redaction is the intended direction.

### AndroidManifest

The manifest belongs to the app agent. These actions need:

```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
<uses-permission android:name="android.permission.VIBRATE"/>
<uses-permission android:name="com.android.alarm.permission.SET_ALARM"/>
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
<uses-permission android:name="android.permission.ACTIVITY_RECOGNITION"/>
<uses-permission android:name="android.permission.READ_CONTACTS"/>
<uses-permission android:name="android.permission.READ_CALENDAR"/>
<uses-permission android:name="android.permission.WRITE_CALENDAR"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
<uses-permission android:name="android.permission.SEND_SMS"/>
<uses-permission android:name="android.permission.CALL_PHONE"/>
<uses-permission android:name="android.permission.WRITE_SETTINGS"/>

<!-- resolve apps by name and open geo:/tel:/share intents under Android 11+
     package visibility -->
<queries>
    <intent><action android:name="android.intent.action.MAIN"/>
            <category android:name="android.intent.category.LAUNCHER"/></intent>
    <intent><action android:name="android.intent.action.VIEW"/>
            <data android:scheme="https"/></intent>
    <intent><action android:name="android.intent.action.VIEW"/>
            <data android:scheme="geo"/></intent>
    <intent><action android:name="android.intent.action.DIAL"/>
            <data android:scheme="tel"/></intent>
    <intent><action android:name="android.intent.action.SEND"/>
            <data android:mimeType="text/plain"/></intent>
    <intent><action android:name="android.intent.action.INSERT"/>
            <data android:mimeType="vnd.android.cursor.dir/event"/></intent>
</queries>
```

`WRITE_SETTINGS` and notification-policy access are *special* accesses: they are
granted on a settings screen, not by a runtime dialog. `open_settings_panel`
with `panel=write_settings` or `panel=dnd_access` takes the user straight there.

Starting an activity from the background (every intent-based action here) is
restricted from Android 10. The app needs a foreground service, an overlay
permission, or the user's attention; when the platform refuses, these actions
report it rather than pretending to have opened something.

---

## Tests

| what | where | runs? |
|---|---|---|
| policy truth table — 36 tier/policy combinations, 288 with switches and trust | `tools/policy_truth_table_test.py` | **yes**, `python3` |
| policy engine, same table in Kotlin | `app/src/test/kotlin/…/policy/PolicyEngineTest.kt` | needs the Android SDK |
| path traversal | `app/src/test/kotlin/…/actions/PathScopeTest.kt` | needs the Android SDK |
| SSRF guard | `app/src/test/kotlin/…/actions/SsrfGuardTest.kt` | needs the Android SDK |
| secret redaction | `app/src/test/kotlin/…/audit/RedactorTest.kt` | needs the Android SDK |
| time parsing | `app/src/test/kotlin/…/actions/TimeParseTest.kt` | needs the Android SDK |

The Kotlin tests need JUnit 4 on the unit-test classpath
(`testImplementation("junit:junit:4.13.2")`) and nothing else — every class they
touch is free of Android imports.
