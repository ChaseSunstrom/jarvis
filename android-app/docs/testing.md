# Testing the Android app

Three layers, and each one exists because the layer above it cannot see the
failures below it.

| layer | where | needs | what it can prove |
|---|---|---|---|
| Python specs | `tools/*.py` | nothing | the pure rules — tiers, timelines, geofences, wire shapes — and that Kotlin and Python agree about them; plus `instrumentation_contract_test.py`, which checks the instrumented suite's assumptions about the app without a device |
| JVM unit tests | `app/src/test/` | a JDK | pure-logic classes with the Android SDK stubbed out |
| **instrumented tests** | `app/src/androidTest/` | **an emulator** | the app actually starting, drawing, talking to a server, and refusing to do dangerous things |

The instrumented layer is the one this document is about. It is also the one
that catches the class of bug the other two structurally cannot: every unit test
passed on the day the APK crashed on launch with a `ClassNotFoundException`,
because a JVM test never loads a class the way ART does, never inflates a view,
never runs `Application.onCreate`, and never opens a socket.

---

## Running them

### On a local emulator

```bash
# 1. An emulator, running and unlocked. API 33+ is what CI uses; 29+ works.
emulator -avd Pixel_6_API_34 -no-snapshot -no-boot-anim &
adb wait-for-device

# 2. The whole suite.
./gradlew :app:connectedDebugAndroidTest

# 3. The screenshots it produced.
adb pull /sdcard/Android/data/ai.jarvis.app/files/screenshots ./screenshots
```

One class, or one test:

```bash
./gradlew :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=ai.jarvis.app.ConsentGateTest

./gradlew :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=ai.jarvis.app.ConsentGateTest#aTierThreeCommandPromptsWithVerbatimParamsAndDenyStopsIt
```

### The one test that needs a real server

`ConversationE2ETest` drives a real voice turn against a real `jarvis-core`. It
expects the harness at `http://10.0.2.2:8080` — `10.0.2.2` is QEMU's alias for
the loopback interface of the machine the emulator is running on.

```bash
# host: boot jarvis-core with the fake model and voice backends
python -m jarvis --config /path/to/test-config &

./gradlew :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessUrl=http://10.0.2.2:8080 \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessToken=<the-token>
```

The alternative, which needs one more command but exercises the app's *shipping*
transport posture rather than a debug-only exemption:

```bash
adb reverse tcp:8080 tcp:8080
./gradlew :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.jarvisHarnessUrl=http://127.0.0.1:8080
```

`127.0.0.1` is already on the cleartext allow-list in the shipping
`res/xml/network_security_config.xml`; `10.0.2.2` is added only by the
**debug-only** override in `src/debug/res/xml/network_security_config.xml`.

Every instrumentation argument:

| argument | default | meaning |
|---|---|---|
| `jarvisHarnessUrl` | `http://10.0.2.2:8080` | where the real jarvis-core harness is |
| `jarvisHarnessToken` | `jarvis-test-token` | the token it will accept |
| `jarvisHarnessPipeline` | `Jarvis` | the pipeline name to resolve |
| `jarvisExpectedTranscript` | *(unset)* | tighten the transcript assertion to an exact substring |
| `jarvisRequireHarness` | `true` | `false` makes `ConversationE2ETest` **skip** instead of fail when no harness is reachable |

`jarvisRequireHarness` defaults to *required* on purpose. A test that quietly
skips when the thing it tests is absent reports green on a CI job that forgot to
start the server, which is precisely the failure mode this whole exercise exists
to eliminate.

### Emulator prerequisites

* **Unlocked.** `JarvisTestRule` runs `input keyevent KEYCODE_WAKEUP` and
  `wm dismiss-keyguard` before every test. That works on an emulator with no
  screen lock set. It will not work on a device with a real PIN, which is why
  this suite is documented as emulator-only.
* **Permissions.** Granted by `pm grant` in the same rule: `RECORD_AUDIO`,
  `ACCESS_COARSE_LOCATION`, and `POST_NOTIFICATIONS` on API 33+. These are
  Android's runtime permissions, which decide whether an action is *possible*.
  They are a separate axis from the Tier-1/2/3 policy, which decides whether it
  is *allowed* — granting `RECORD_AUDIO` does not make anything skip a consent
  prompt.
* **Animations.** `testOptions { animationsDisabled = true }` zeroes the three
  global scales for the whole run. `BootAnimationTest` restores them for its own
  duration and puts them back; see below.

---

## What each test proves

### `AppLaunchTest`

`MainActivity` reaches RESUMED, the orb is instantiated and displayed, and the
home controls are on screen. **This alone would have caught the launch crash the
whole APK shipped with.** The third case checks that a first-run install says
where to start rather than sitting there looking broken.

### `BootAnimationTest`

The power-on sequence plays, drives the real orb, reaches the handoff with the
home UI at full opacity, and removes itself from its parent. `skip()` lands on
exactly the frame the full sequence ends on, and is idempotent. A third case
covers the opposite promise: at animator scale 0 the sequence stands down
immediately instead of holding the home UI invisible.

Two things about this class are worth knowing before editing it:

* It restores the animation scales itself, then waits on
  `ValueAnimator.areAnimatorsEnabled()` — which reads the scale the app process
  has actually cached — before asserting anything about timing. Polling
  `Settings.Global` would prove the write landed, not that the animator knew.
* It drives its own `JarvisBootAnimation` over `TestHostActivity` rather than
  using `MainActivity`'s. The real one plays once per *process*, behind
  `JarvisApp.consumeColdStart`, so whether it plays depends on which class the
  runner happened to execute first.

### `NavigationTest`

Every button on `MainActivity` and `SettingsActivity` opens something or says
why it cannot. The two that matter are AUTOMATIONS and AUDIT LOG: both point at
`ai.jarvis.app.automation.ui.*` activities that are **declared in the manifest**
and **not implemented in this build**. That combination does not throw
`ActivityNotFoundException` — the intent resolves, and the app dies later with
"Unable to instantiate activity". The test asserts the *toast*, which is the
only positive evidence that `JarvisScreens.isPresent` ran, rather than merely
"nothing crashed", which would also pass on a build where the crash happens one
frame later.

If the automation module ever lands, these cases notice and assert that the
screen opens instead.

### `SettingsPersistenceTest`

A server URL and token typed into the screen survive it closing and reopening —
asserted twice, once through `JarvisConfig` (the object the rest of the app
reads) and once by reopening the screen. Also: an invalid URL and a missing
token are both refused *without half-saving*, and the per-install device id is
stable.

### `AssistActivityTest`

`ACTION_ASSIST` and `ACTION_VOICE_COMMAND` both open the transparent assist
surface with the orb on it; Back and a tap both close it; and an unconfigured
device is sent to Settings instead of opening a microphone it has nowhere to
send.

### `ConversationE2ETest`

The real round trip. Synthetic PCM → `MicStreamer` → `JarvisConversation`'s
energy VAD → `AssistPipelineClient` → a real WebSocket to a real jarvis-core →
`stt-end` → the transcript view, `intent-progress` → the response view.

A non-empty transcript is the assertion that carries the weight: the server only
sends `stt-end` after the app streamed audio frames prefixed with the run's
`stt_binary_handler_id` **and** sent the lone end-of-audio byte, so one assertion
covers the entire capture-and-stream path including the VAD deciding the user
stopped talking. Both values are also checked against the error strings the app
puts on screen, so "the response rendered" cannot be satisfied by the words
"connection error".

Two things about this class are worth knowing before editing it, because both
were once got wrong here in ways that produced an assertion which could not fail:

* **The talk button's label proves nothing about the server.**
  `MainActivity.toggleTalk` sets it to `LISTENING… (TAP TO STOP)` synchronously,
  before a socket is opened. A test that waited for the label to leave
  `TAP TO SPEAK` would be waiting for its own tap, and would pass against a
  harness that refused the token. `theServerAcceptsTheRunAndTheTranscriptRenders`
  therefore waits on the transcript view instead.
* **The orb's ERROR state is not readable.** `JarvisOrbView` paints its caption
  onto a `Canvas` — no accessibility node, no getter — and `MainActivity.onError`
  writes only that caption and `responseView`. `onMode`, the sole writer of the
  button, is only ever called with `LISTENING`, `PROCESSING`, `RESPONDING`. So
  the error assertions read `responseView`, and
  `tools/instrumentation_contract_test.py` fails if `onError` stops writing it or
  if an ERROR mode ever appears (at which point the stronger assertion becomes
  available again).

### `DeviceChannelTest`

Authenticate, register an action manifest, execute a Tier-1 `list_files`, get a
`device_result` back. Plus: a redelivered `command_id` is answered from cache and
the action runs exactly once, and an action this build has never heard of is
answered `unsupported` and audited as CONFIRM/DENY.

### `ConsentGateTest` — the important one

Everything else here checks that a feature works. This checks that a feature
**cannot be made to work** by the one component the threat model says may be
lying.

`delete_file` is Tier 3 CONFIRM, so a file is planted, the server asks for it to
be deleted, and:

1. The prompt appears, showing the action id, the tier, the server's reason
   labelled as the *server's*, and the verbatim parameters.
2. RAW shows the exact string that was handed to `ApprovalBridge` — not a
   re-serialisation, which can lose duplicate keys in a hostile payload.
3. There is no control anywhere on it that could remember the answer.
4. DENY reports `denied`, **and the file is still there.** That last clause is
   the only assertion a lying implementation could not fake: a test that checked
   the wire alone would pass on a build that reported a denial and deleted the
   file anyway.
5. A second, identical command with a fresh `command_id` prompts **again**, and
   the policy store still contains no standing answer for the action.
6. Back denies rather than leaving the request unanswered.
7. A server claiming `"tier": 1` for a Tier-3 action still gets a Tier-3 prompt.

**The test answers through the real UI, and there is no hook that could do
otherwise.** `ApprovalBridge.deliver` is called by `ApprovalActivity` and by
nothing else. A test hook that could resolve an approval would be a mechanism
that could resolve an approval, and adding one would destroy the thing being
tested.

### `CompanionAskTest`

A `jarvis_message` of kind `ask` renders with its options; tapping one sends
`jarvis_message_result` with **that exact option string** (the buttons upper-case
their labels for display only, and sending `"YES"` back for an option the server
spelled `"yes"` is a different answer). A duplicate delivery replays the stored
reply and raises no second screen. Dismiss and Back both report `dismissed`, so
the server escalates rather than waiting.

---

## How the tests are built

### The fake server, and the real one

`ConversationE2ETest` talks to the real harness because the point of it is a real
round trip. The channel tests want the opposite — total control over what the
server says and exactly when — so they use `support/FakeJarvisServer`, an
in-process OkHttp `MockWebServer` on `127.0.0.1` speaking the handshake.
Everything on the *device* side of that socket is production code: the real
`JarvisChannel`, `CommandGate`, `TierGuard`, `ActionRegistry`, `PolicyEngine`
and `ApprovalActivity`.

Loopback is deliberate. It is already on the shipping cleartext allow-list, so
those tests need no debug-only network exemption and no `adb reverse`, and the
host pin is exercised for real.

### Waiting

`support/Waits` — poll for a condition, never sleep for a duration. A sleep
encodes a guess about how fast the machine is; on a CI emulator that guess is
wrong in both directions.

There are exactly two fixed delays in the suite. `Screenshots.takeAfterSettling`
waits for "the pixels look right", which nothing exposes as a condition, and
never gates an assertion. `BootAnimationTest` sleeps to reach a moment *inside* a
1400 ms animation — "still running 300 ms in" is a claim about elapsed time, so
elapsed time is the right tool, and a `ValueAnimator`'s clock is wall-clock so
the assertion cannot go the other way however slow the machine is.

`Waits.neverBecomesTrue` and `Activities.assertDoesNotStart` are the shapes for
proving something did *not* happen, which is necessarily a real wait: there is
no event for the absence of an activity.

### Activities

`support/Activities` uses `Instrumentation.ActivityMonitor`, not
`ActivityScenario`. `ActivityScenario`'s documentation states it does not support
activities declared `launchMode="singleTask"`, and `MainActivity` and
`JarvisAssistActivity` are both exactly that — deliberately, because it is what
makes the assist popup behave like an assistant instead of an app.

"Which of our screens is showing" is asked of the in-process lifecycle registry
rather than `dumpsys activity`: exact, no output parsing, and no false positive
from the system Settings app, several of whose screens are also called
something-`SettingsActivity`.

### Toasts

`support/Toasts` catches the accessibility event the platform fires for a toast
(`TYPE_NOTIFICATION_STATE_CHANGED`, `className = android.widget.Toast`) via
`UiAutomation.executeAndWaitForEvent`. Espresso cannot see a toast at all — it
lives in its own window, outside the activity hierarchy — and scraping
UiAutomator's window list for `TYPE_TOAST` is inconsistent across platform
versions.

### Finding views with no ids

Every Jarvis screen is built programmatically, so there is no `R.id` to match
on. `support/Views` matches on class and on text, case-insensitively — the
latter is load-bearing, because `isAllCaps` is a *display* transformation and the
accessibility node still carries the original string. `Views.findScrolling`
scrolls to the top and then works down, so a screen taller than the emulator's
display does not turn into "the control is not there".

### Screenshots

Every test captures at least one PNG into the app's external files dir
(`/sdcard/Android/data/ai.jarvis.app/files/screenshots`), which needs no storage
permission and which CI can `adb pull` wholesale. `JarvisTestRule` adds a
failure screenshot **and the window hierarchy** on any failure — a stack trace
alone is usually not enough to diagnose a UI failure you cannot reproduce.

Screenshots never fail a test. A suite where the diagnostic tool can itself turn
the build red is a suite people learn to ignore; a capture that fails leaves a
`<name>.FAILED.txt` beside where it should have been.

The directory is cleared **once per run**, before the first test — `am instrument`
runs the whole suite in one process, so `JarvisTestRule` does it behind an
`AtomicBoolean`. That matters because the app's data survives `adb install -r`
and survives a re-run against the same device: without the clear, a test that
failed *before* reaching its `Screenshots.take` would leave the previous run's
picture in place, CI would upload it, and somebody would debug a screenshot of a
passing run.

`ApprovalActivity` and `CompanionAskActivity` set `FLAG_SECURE`, which is
correct — a Tier-3 prompt's parameters must not reach the screen recorder.
`UiAutomation.takeScreenshot` captures secure layers because it runs with system
privilege; if a platform build ever declines to, those two come out black. The
assertions in those tests are made against the accessibility tree, never against
pixels.

### "Nothing crashed", checked twice

The instrumented suite runs inside the app's own process, so a crash on the main
thread takes the whole run down and is impossible to miss. A crash on a
*background* thread — the WebSocket reader, the mic worker, a coroutine
dispatcher — does not, and would otherwise surface as an unrelated assertion
timing out several tests later. `JarvisCrashHandler` records every uncaught
throwable before delegating, so `JarvisTestRule` clears that log before each test
and fails the test if anything is in it afterwards.

---

## The debug-only hooks

`app/src/debug/kotlin/ai/jarvis/app/testing/` — compiled into the debug variant
and **no other**. `app/build.gradle.kts` additionally registers
`assertNoTestHooksInRelease`, which unzips every release APK **and every release
AAB** and fails the build if `ai.jarvis.app.testing` appears in any entry, so the
guarantee survives someone adding a flavour or a source set later.

Three encodings are searched, over every entry rather than only `*.dex`: the DEX
descriptor `ai/jarvis/app/testing/`, the UTF-8 class name, and the same name in
**UTF-16LE** — which is how a binary `AndroidManifest.xml` stores it. The debug
manifest declares `TestHostActivity`; a DEX-only, UTF-8-only scan would have
called a release manifest carrying that declaration clean.
`tools/instrumentation_contract_test.py` fails if any of that wiring is removed.

| hook | why it has to exist |
|---|---|
| `TestHooks.configure` | typing a URL and a 40-character token through the UI before every test is slow, and makes an unrelated failure look like a settings failure |
| `TestHooks.feedSyntheticSpeech` | an emulator has no microphone; see below |
| `TestHooks.startChannel` | nothing in the shipping app constructs `JarvisChannel` yet — see "What is not wired" |
| `TestHooks.policyDecisions` | read the app's own audit log, which already records `(action, tier, decision, status)` |
| `TestHooks.screenshotDir` | one agreed place for CI to pull artefacts from |
| `TestHooks.resetState` | isolate tests from each other |

**None of them can approve, skip, remember or weaken a consent prompt.** There is
no hook that answers an approval, no hook that writes the policy store
(`policyDecisions` and `userPolicies` are read-only), and no hook that changes a
tier or shortens `ConsentGate.ARM_MS`. `resetState` only ever makes the device
*more* cautious: an unconfigured phone talks to nobody and a cleared policy store
asks about everything.

### The one seam added to production code

`MicStreamer.debugPcmSource` — a `@Volatile` factory of a one-method
`PcmSource` interface, read through `BuildConfig.DEBUG` at the point of use so
R8 folds the branch away in release.

It exists because an emulator has no microphone. `AudioRecord` initialises
happily and then returns silence forever, so `JarvisConversation`'s energy VAD
never sees speech, never sends end-of-audio, and no instrumented test can drive a
voice round trip at all. The seam replaces the *input device* and nothing else:
every byte still travels the same path, through the same client, to the same
socket. It cannot skip a consent prompt or change a tier — the policy gate lives
in `automation/policy` and `ui/ApprovalBridge`, which `MicStreamer` does not
import and has no way to reach.

`TestHooks.feedSyntheticSpeech` installs `SyntheticSpeech`, which emits a 220 Hz
sine in a repeating 900 ms loud / 3.5 s silent pattern. The repetition is not
laziness: the socket is not ready the instant the mic starts, and audio produced
before `run-start` arrives has no binary handler id to be prefixed with and is
dropped. A one-shot utterance would be a race between a fake microphone and a
real network.

---

## What cannot be tested on an emulator

Listed so that "there is no test for it" is a known gap rather than an
assumption.

* **A real microphone, and therefore real speech.** The synthetic source proves
  the streaming path, the VAD thresholds and the framing. It says nothing about
  whether the wake word fires on a real voice in a real room, about AGC, echo
  cancellation, or how `VOICE_RECOGNITION` behaves on a particular phone's audio
  HAL.
* **The wake word.** Always-on detection means an open mic and a foreground
  service; there is nothing on an emulator to say "Hey Jarvis" to.
* **Bluetooth and audio routing.** `WakeWordGate`'s car mode keys off a
  connected car headset. An emulator has no Bluetooth stack worth the name, so
  earpiece/speaker/headset routing, `AUDIO_BECOMING_NOISY`, and the in-call
  behaviour are all untested here.
* **The assist gesture and the assistant role.** The role is a Secure Setting
  written by the user or by adb (see the README) and the gesture is a system
  input path. The tests fire `ACTION_ASSIST` directly, which proves the app's
  half — that the intent the system *would* send reaches an activity that draws
  the right thing.
* **GrapheneOS-specific behaviour.** The whole reason `compat/GrapheneCompat`
  exists: the per-app Network toggle that reads GRANTED while every socket is
  refused, a stripped `QUERY_ALL_PACKAGES`, the stricter `Settings` reads, the
  storage scopes. A stock AOSP emulator image reproduces none of it. `docs/grapheneos.md`
  is the manual checklist, and `SystemCheckActivity` is the on-device one.
* **Real Tier-3 side effects.** `ConsentGateTest` proves the gate with
  `delete_file`, whose blast radius is one file in app-private storage. Sending
  an actual SMS, placing an actual call, or running an actual Shizuku shell
  command is deliberately not automated — the point of Tier 3 is that a machine
  does not get to approve it, and that includes this machine.
* **The camera.** An emulator's virtual camera produces a test pattern, which
  proves the plumbing and nothing about "what am I looking at".
* **A locked phone with a real credential.** The tests dismiss a swipe keyguard.
  The `ConsentGate` and `CompanionAskGate` rules about a locked phone — hidden
  parameters, inert APPROVE, live DENY — are covered by the JVM unit tests
  against those pure objects, not on a device.
* **Battery, doze and long-lived behaviour.** Whether the command channel
  survives a week of Doze on a real phone is not something a CI run can answer.

## What is not wired

`JarvisChannel` has **no production call site**. Grep it: the class is complete
and tested, but nothing in the shipping app constructs it. `TestHooks.startChannel`
performs exactly the wiring `channel/DeviceLink` documents, which is what the
automation foreground service is expected to do when that lands.

So `DeviceChannelTest`, `ConsentGateTest` and `CompanionAskTest` prove the
channel *works*. They do not prove that anything *starts* it. That is a real gap
and it is in the app, not in the tests.

## Where these tests are compiled, and where they are not

`src/androidTest` is compiled by exactly one thing today: the emulator job in
`.github/workflows/e2e.yml`, inside the same step that boots the AVD. Neither
`ci.yml` (the fast lane) nor `android-apk.yml` compiles it —
`android-apk.yml` compiles `:app:compileDebugUnitTestKotlin` and stops there.

The consequence is worth stating plainly: **a Kotlin compile error in this
suite is not caught until roughly thirty minutes into an emulator run**, and it
fails after the AVD has booted rather than before. The fix is one extra Gradle
invocation — `gradle :app:assembleDebugAndroidTest` — either as a step in
`android-apk.yml` beside the unit-test compile, or as a preflight step in the
emulator job before the AVD cache is restored. It also warms the Gradle cache
for the run that follows.

`tools/instrumentation_contract_test.py` is the part of that gap which *can* be
closed without an SDK, and it runs in the fast lane on every push. It does not
compile anything; it checks the assumptions the suite makes about the app.
