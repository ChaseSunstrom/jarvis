# Jarvis for Android

The standalone Jarvis phone app. **No Home Assistant code, no Google Play
Services, no Firebase** — it talks only to your own `jarvis-core` server over
LAN or WireGuard, and it enforces its own safety policy locally, on the device,
outside the model.

This replaces the old `android/overlay/` fork of the Home Assistant companion
app. The good parts of that overlay (the orb, the voice client, the assist
popup) were ported over verbatim into `ai.jarvis.app.*`; the fork is gone.

```
app/src/main/kotlin/ai/jarvis/app/
  MainActivity.kt            orb, tap-to-talk, live transcript, bottom nav
  JarvisAssistActivity.kt    the assist-gesture popup (transparent, lock-screen)
  SettingsActivity.kt        server URL, token, pipeline, device name, wake gating
  ManagementActivity.kt      origin-locked WebView onto jarvis-core's own UI
  ApprovalActivity.kt        THE TIER-3 CONSENT SCREEN
  JarvisApp.kt               Application; notification channels
  ui/     JarvisOrbView · JarvisUi · ApprovalBridge · JarvisScreens
  config/ JarvisConfig · ServerUrl · WakeWordGate
  assist/ AssistPipelineClient · MicStreamer · TtsPlayer · JarvisConversation
          JarvisVoiceInteraction{Service,SessionService,Session} · JarvisRecognitionService
  automation/                owned by the automation module, not by this one
```

## Build

There is no wrapper jar in the repo — only `gradle/wrapper/gradle-wrapper.properties`
(pinned to Gradle 8.10). Generate the wrapper once with any local Gradle:

```bash
cd android-app
gradle wrapper          # writes gradlew, gradlew.bat and the wrapper jar
./gradlew :app:assembleDebug
```

Or skip the wrapper entirely and use your local Gradle (8.9+):

```bash
gradle :app:assembleDebug
```

You need an Android SDK with platform 35 and JDK 17+. Point at it with
`ANDROID_HOME`, or put `sdk.dir=/path/to/Android/sdk` in `android-app/local.properties`.

The APK lands in `app/build/outputs/apk/debug/`. Release builds use the same
`applicationId` with `isMinifyEnabled = false` — deliberately, so the first
build is not also your first R8 debugging session. Turn minification on later;
`app/proguard-rules.pro` already has the keep rules you will need.

| setting | value |
|---|---|
| applicationId | `ai.jarvis.app` |
| minSdk / targetSdk / compileSdk | 29 / 35 / 35 |
| Kotlin / AGP | 2.0.21 / 8.7.3 |
| viewBinding | off — every screen is built programmatically |
| dependencies | androidx core/appcompat/activity/lifecycle/work/datastore, OkHttp 4.12, coroutines |

## Install and grant the roles

Installing is not enough: on GrapheneOS the assistant role is a Secure Setting,
and **it is cleared on every reinstall or update**, so re-run these after each
Obtainium update.

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk

# assist gesture / long-press home -> the Jarvis popup
adb shell settings put secure assistant \
  ai.jarvis.app/ai.jarvis.app.JarvisAssistActivity
adb shell settings put secure voice_interaction_service \
  ai.jarvis.app/ai.jarvis.app.assist.JarvisVoiceInteractionService

# verify
adb shell settings get secure assistant
adb shell settings get secure voice_interaction_service

# smoke-test the popup without touching the phone
adb shell am start -a android.intent.action.ASSIST
```

The GUI equivalent is **Settings › Apps › Default apps › Digital assistant app
› Jarvis**, which Settings reaches directly from the ASSISTANT button.

### The two services the user must switch on by hand

Neither can be granted by adb on a normal build; both are one-time, and both
have a shortcut button in Jarvis Settings under **System access**.

* **Accessibility** — Settings › Accessibility › Installed apps (or *Downloaded
  apps*) › **Jarvis** › on. This is what lets Jarvis read the screen and tap.
  It is the single most dangerous thing you can grant this app, which is why
  every tap or keystroke it performs is Tier 3.
* **Notification access** — Settings › Notifications › Device & app
  notifications › **Notification access** › Jarvis › on. Read-only, and used
  only as a trigger source.

Two more that are worth granting, both under **System access** in Settings:

* **Display over other apps** — without it, a Tier-3 request that arrives while
  Jarvis is in the background cannot open the consent screen directly; you get a
  heads-up notification to tap instead. Nothing runs either way until you approve.
* **Battery optimisation: don't optimise** — keeps the command channel connected.

## Point it at your server

Settings takes the base URL of `jarvis-core` (e.g. `http://192.168.2.10:8123`),
a long-lived access token (PASTE from the clipboard, or SCAN QR — Jarvis bundles
no barcode decoder and hands off to any installed scanner such as Binary Eye from
F-Droid), the pipeline name, and a device name.

**Cleartext is restricted.** `res/xml/network_security_config.xml` denies plain
HTTP by default and permits it only for a documented list of private hosts
(`localhost`, `jarvis.local`, `jarvis.lan`, `jarvis.home.arpa`, and the
reference LAN addresses). `<domain>` has no CIDR syntax, so **add your server's
literal hostname or IP to that file and rebuild**, or put HTTPS on jarvis-core —
user-installed CAs are trusted for those hosts, so a private CA works fine. The
Settings screen refuses `http://` to any non-private host up front rather than
letting the platform fail the connection later with no explanation.

Nothing here is backed up: `allowBackup=false` plus
`res/xml/data_extraction_rules.xml` keep the token, the policy store and the
audit log off cloud backups and out of device-to-device transfers.

## Security model

The LLM runs on the server. The server can be wrong, or prompt-injected by a web
page it read. So **the device decides what is allowed, locally, outside the
model.**

| tier | what | gate |
|---|---|---|
| 1 AUTO | read battery/network/screen state, coarse location, media play/pause, volume, torch, launch an app, post a notification, read calendar | runs |
| 2 NOTIFY | set alarm, create calendar event, write a file in app storage, clipboard, ringer/DND, screenshot, start navigation | ask once, then remember per action |
| 3 CONFIRM | SMS, calls, UI automation that taps or types, shell/Shizuku, delete files, install/uninstall, payments, unlock — anything that reaches another person or cannot be undone | full-screen consent, **every time** |

The rules that hold in code:

* Tier comes from a **device-local table**. The `tier` field on an incoming
  `device_command` may only ever RAISE it (`max(local, incoming)`), never lower it.
* A per-action policy store (`allow_always` | `ask` | `never`) lives in
  SharedPreferences `jarvis_policy`, keys `policy.<action_id>`. `never` outranks
  everything, including the server.
* **Tier 3 can never be auto-approved and is never remembered.** `ApprovalBridge`
  has no code path that returns "always", regardless of what a caller asks for.
* Denied or timed out ⇒ `status: "denied"` and nothing executes.
* Every executed action is appended to a local, user-viewable audit log
  (`filesDir/jarvis/audit.jsonl`).
* Content from the web, notifications or the screen is **untrusted data**. It can
  be displayed and it can be sent to the server as context, but no code path
  turns it into an action without a fresh human approval.

What this module contributes to that story: the consent screen and the bridge in
front of it, the origin lock on the WebView, the cleartext policy, and a manifest
where every dangerous permission is annotated with the tier it serves. The policy
table, the dispatcher and the audit log are the automation module's.

### Things the consent screen does on purpose

* Shows the action id, the **verbatim** parameters, and the reason — with a RAW
  toggle, because pretty-printing JSON can hide a duplicate key in a hostile
  payload, and the user should be able to see the exact text.
* Shows the action's **local description** and the **server's reason** as two
  separately labelled fields. One is ours and trustworthy; the other is remote
  text that may have come from a web page. Blending them would hide the
  difference that matters.
* **The keyguard is part of the gate.** The prompt draws over a locked screen so
  the phone lights up and the question is not missed, but while the keyguard is
  up the parameters are replaced by "Hidden until this phone is unlocked" and
  APPROVE is inert — including through the RAW toggle. The activity asks the
  system to dismiss the keyguard and opens up only once the user is through it.
  DENY stays live throughout, because refusing is safe from anywhere. Without
  this, "a human approved it" would only mean "whoever was holding the phone
  approved it", and an SMS body or a shell command would be readable off a lock
  screen. The rule itself is [`ui/ConsentGate.kt`](app/src/main/kotlin/ai/jarvis/app/ui/ConsentGate.kt)
  — pure logic, no Android imports, unit-tested in `ConsentGateTest`.
* APPROVE is inert for 700 ms *after the prompt becomes readable* (i.e. after
  unlock, not after create) and sets `filterTouchesWhenObscured`, so a tapjacking
  overlay or a stray tap cannot approve anything.
* `FLAG_SECURE` keeps parameters out of screenshots and screen recordings —
  including this app's own accessibility path.
* Back denies. Swipe-away denies. `onDestroy` without an answer denies. The
  countdown denies. There is exactly one way to approve, and the enabled-state
  check is repeated inside the click handler and again inside `answer()`.

## The ApprovalBridge contract

Any module that needs a human decision calls this and nothing else:

```kotlin
import ai.jarvis.app.ui.ApprovalBridge

val ok: Boolean = ApprovalBridge.request(
    context,
    actionId = "sms.send",
    params = """{"to":"+441234567890","body":"On my way"}""",
    reason  = "You asked me to tell Sam you are running late.",
)
if (!ok) return DeviceResult.denied(commandId)
```

It suspends until the human answers, then returns `true` only if they tapped
APPROVE. Implementation is a `CompletableDeferred` in a static map keyed by a
random request id; `ApprovalActivity` settles the entry and every other path
fails closed.

Guarantees you may rely on, and must not weaken:

1. **Fail closed.** Activity cannot start, notifications blocked, process
   killed, deferred dropped, countdown expired — all `false`.
2. **No memory.** Nothing is written to the policy store, and no overload can
   answer "always".
3. **Verbatim.** `params` is displayed exactly as passed. Pass the payload you
   are about to execute, not a summary and not the model's paraphrase. If the
   two can differ, the prompt is a lie.
4. **Cancellation propagates.** Cancelling the calling coroutine throws
   `CancellationException` rather than silently reporting a decision nobody made.
5. **Timeout is 60 s** and a caller may only shorten it (floor 10 s), never
   extend it. How long a consent prompt lives is not a remote server's decision.

Two extra entry points exist:

* `requestOutcome(...)` returns `Outcome.APPROVED | DENIED | TIMED_OUT |
  UNDELIVERABLE` instead of a Boolean, so the audit log can record a lapse
  differently from a refusal. It also accepts `description`, `tierLabel` and
  `commandId` for a richer prompt.
* A nine-argument `request(context, actionId, description, params, tier, reason,
  commandId, rememberable, timeoutMs): String` overload exists purely so the
  automation module's `UiApprovalGateway` compiles unchanged. It returns
  `"approved"` / `"denied"` / `"timeout"`. `tier` is typed `Any?` so this module
  never imports from `automation/`. **`rememberable` is ignored** — this prompt
  has no "always" control, so a Tier-2 caller asking to remember simply gets
  asked again. Erring toward more prompting is the only safe direction.

## What this module gives the other modules

`AndroidManifest.xml` is complete and **nobody else should need to edit it**. It
already declares:

* every permission, each annotated with the tier it serves;
* the assist components and the three voice-interaction services;
* `ai.jarvis.app.automation.JarvisAutomationService` (foreground, `specialUse|dataSync|microphone`);
* `ai.jarvis.app.automation.accessibility.JarvisAccessibilityService` (+ `@xml/jarvis_accessibility_service`);
* `ai.jarvis.app.automation.notify.JarvisNotificationListener`;
* `ai.jarvis.app.automation.triggers.{BootReceiver, SystemEventReceiver, AlarmReceiver}`;
* `ai.jarvis.app.automation.ui.{AutomationsActivity, AuditLogActivity}`.

Provide classes with exactly those names and they are wired. `MainActivity` and
`SettingsActivity` launch the last two by name through `ai.jarvis.app.ui.JarvisScreens`
(a missing class is a toast, not a crash), which keeps the dependency one-way:
this module never imports from `automation/`.

Also shared:

* `JarvisConfig` — `serverUrl`, `token`, `pipeline`, `deviceName`, a stable
  per-install `deviceId` for `jarvis/device/register`, and the wake-word gating
  settings. `JarvisConfig.Policy` mirrors the policy/audit storage names; the
  automation module's `PolicyStore` is the authority if they ever disagree.
* `JarvisApp.CHANNEL_APPROVAL` / `CHANNEL_SERVICE` / `CHANNEL_ALERTS` — three
  channels created at startup. Post through these rather than creating more, so
  the user gets one coherent set of switches. (`CommsActions` currently makes its
  own `jarvis_actions` channel lazily; folding that into `CHANNEL_ALERTS` would
  be a tidy-up.)
* `JarvisUi` — colours, fonts, panels, pills, ghost buttons, consent buttons,
  corner brackets. Use it and every screen keeps looking like one app.
* `ServerUrl` — origin parsing, `sameOrigin`, `isPrivateHost`, `websocketUrl(base)`
  for `/api/websocket`, and `check()` for validating what a user typed.
* The unit-test source set: `app/build.gradle.kts` wires `src/test/kotlin` plus
  JUnit 4 and `kotlinx-coroutines-test`, so no other module needs to touch the
  build file to add tests.

## Ported from the old overlay

These came from `android/overlay/.../jarvis/`, repackaged to `ai.jarvis.app.*`
with Home-Assistant wording updated to jarvis-core and `haUrl` renamed to
`serverUrl`. Behaviour is unchanged:

`JarvisOrbView`, `JarvisUi` (extended, not rewritten), `JarvisConfig` (extended),
`AssistPipelineClient`, `MicStreamer` (one dead local removed), `TtsPlayer`,
`JarvisConversation`, `JarvisAssistActivity`, the three voice-interaction
classes, `WakeWordGate`, and the icon vectors.

Two behaviours deliberately **do not** match the originals, because the port
carried two bugs across:

* `AssistPipelineClient.absolute()` used to accept any `tts_output.url` starting
  with `http`, and `TtsPlayer` then fetched it with the bearer token attached.
  Both now pin to the configured origin (see above).
* `JarvisAssistActivity.onNewIntent` started a second `JarvisConversation`
  without stopping the first, leaving an `AudioRecord`, its capture thread and a
  WebSocket running unowned — two open mics for one question. `begin()` now
  stops the previous conversation first.

## Tests

```bash
./gradlew :app:testDebugUnitTest
```

Plain JVM unit tests, JUnit 4, no device and no network. The build file wires
`src/test/kotlin` and the `junit` + `kotlinx-coroutines-test` dependencies, so
the automation module's tests and this module's run together.

From this module: `config/ServerUrlTest` (24), `config/WakeWordGateTest` (5) and
`ui/ConsentGateTest` (7) — 36 tests covering origin parsing and port defaulting,
same-origin comparison, private-host classification, cleartext refusal,
WebSocket URL derivation including a reverse-proxy path prefix, the wake gate
including midnight-wrapping windows and out-of-range rejection, `resolveOnServer`
(the origin pin on server-supplied URLs, below), and the consent gate's
lock/arm/answered truth table. Every class under test is deliberately free of
Android imports for exactly this reason.

Because this container has no Android SDK, those three classes were compiled and
run in a throwaway JVM-only Gradle project alongside the automation module's
pure-logic tests: **75 tests, 1 failure** — and the failure is not in this
module. `automation/actions/SsrfGuardTest.kt:104` asserts
`SsrfGuard.check("http://evil-jarvis.lan/", setOf("jarvis.lan")).allowed == false`,
but a name that is neither exempt nor a literal address correctly returns
`allowed = true, needsDnsCheck = true` — "not decided yet, resolve it and
re-check", which is what `HttpRequest` then does. The assertion, not the guard,
is testing the wrong property. That file belongs to the automation module.

Still uncovered: `ApprovalBridge`'s fail-closed paths. That one file is the
whole safety story, and it needs Robolectric or instrumented coverage —
specifically that a dropped deferred, a refused activity start, a killed
process, and an expired countdown each produce a denial.

### Server-supplied URLs are pinned to the server's origin

`ServerUrl.resolveOnServer(base, urlOrPath)` returns a URL only if it stays on
the configured server's scheme/host/port, and null otherwise. It exists because
the voice pipeline hands back `tts_output.url` and the phone then fetches it
**with the bearer token in an `Authorization` header** — so a server that is
wrong or prompt-injected (which the threat model assumes it can be) could
otherwise name any host and be given the long-lived token. It refuses another
origin, a scheme-relative `//evil.example/x.mp3`, embedded credentials,
non-http(s) schemes, and path-relative values; an absolute path is appended to
the base so a reverse-proxy prefix survives. `AssistPipelineClient` refuses
off-origin TTS URLs, and `TtsPlayer` independently refuses to play (and
therefore to authenticate to) anything that is not on that origin.

### Executable specs (`tools/`, no Gradle, no device)

The parts that decide whether something is allowed to run are written down twice
— once in Kotlin, which this container cannot compile, and once in Python, which
runs. Each script also structurally checks that the Kotlin still says what the
spec says, so the two cannot drift silently.

```bash
python3 -m pytest android-app/tools -q
```

| script | what it pins |
|---|---|
| `policy_truth_table_test.py` | `PolicyEngine`: the tier/policy truth table, the panic and master switches, trust levels, and what may be written to the policy store |
| `dispatch_spec_test.py` | `ActionRegistry.dispatch` as a state machine — 1152 dispatches — asserting what actually **executed**, plus the ORDER of the steps in the Kotlin |
| `action_table_test.py` | every action's tier against the brief and against `docs/actions.md`, no duplicate ids, and that content-returning actions declare `untrustedOutput` |
| `channel_protocol_test.py`, `screen_prune_test.py`, `task_vars_test.py`, `schedule_calc_test.py`, `geofence_test.py` | the other modules' equivalents |

The truth table alone is necessary and not sufficient: "a Tier-3 action never
runs without a human approving *this* invocation" is a property of the order of
steps in the dispatcher, and a dispatcher that consulted the engine and then
executed anyway would pass every check in that file. That is what
`dispatch_spec_test.py` is for.
