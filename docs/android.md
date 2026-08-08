# P5 — Android companion app (Jarvis on home-assistant/android)

The phone side of Jarvis is a fork of
[home-assistant/android](https://github.com/home-assistant/android)
(Apache-2.0) with the Jarvis code added to the **main** source set and shipped
in the existing degoogled **`minimal`** flavor — applied as an overlay, see
`android/README.md` for the mechanics. This doc covers the design decisions,
the GrapheneOS caveats, and the P5 acceptance gate.

## 1. Fork + overlay build

We never vendor the fork. `android/apply-to-fork.sh`:

1. clones `home-assistant/android` at depth 1 (or reuses `HA_ANDROID_DIR`),
2. copies the Jarvis sources into the **main** source set
   (`app/src/main/kotlin/.../jarvis/**`, `res/values/jarvis_styles.xml`,
   `res/xml/jarvis_voice_interaction_service.xml`) — uniquely named so they
   never clobber upstream files,
3. runs `android/overlay/patches/apply.py` (idempotent), which
   - merges the Jarvis permissions + components into
     `app/src/main/AndroidManifest.xml` (marker-guarded),
   - writes a mock `app/google-services.json` if none exists.

Build: `./gradlew :app:assembleMinimalDebug` (or `assembleMinimalRelease`,
signed per `android/keystore.md`), distribute via GitHub Releases + Obtainium.

### Why ship in `minimal` (not a custom flavor)

Upstream ships two flavors via a convention plugin: `full` (Google Play
Services: FCM push, fused location, Wear) and `minimal` (no GMS). The target
device runs GrapheneOS, which is degoogled, so we want `minimal`.

An earlier design added a third `jarvis` flavor that "extended" minimal, but
upstream moved flavor **sources, dependencies and BuildConfig wiring** into
convention plugins, and a new flavor cannot cleanly inherit all of that —
it produced a cascade of unresolved-reference build failures
(`LocationSensorManager`, `MatterManagerImpl`, cronet, …). So Jarvis now lives
in **`src/main`** (compiled into every flavor) and we simply build the
existing, known-good **`minimal`** flavor. Consequences:

- Zero flavor-inheritance to maintain — robust against upstream refactors.
- Installed applicationId is `io.homeassistant.companion.android.minimal`
  (a debug build adds `.debug`); it installs alongside a stock **full** HA.
- Push is over the app's WebSocket connection, not FCM; the mock
  `google-services.json` is only a tooling-presence guard, **no FCM**.
- The Jarvis Kotlin classes stay in package
  `io.homeassistant.companion.android.jarvis.*` regardless of applicationId,
  so the assistant-role component names are stable (see
  `scripts/adb-jarvis-role.sh`, which auto-detects the installed package).

### Self-contained assist client (no HA-app internals)

`JarvisAssistActivity` owns the **entire** interaction and keeps the orb on
screen throughout — it does not hand off to HA's own Assist UI. It speaks the
public Home Assistant WebSocket API directly, the same protocol as the browser
HUD (a faithful Kotlin port of `jarvis-web/src/lib/pipeline.ts`):

- `assist/AssistPipelineClient.kt` — OkHttp WebSocket: auth handshake →
  `assist_pipeline/pipeline/list` (resolve the `Jarvis` pipeline) →
  `assist_pipeline/run` (stt→tts), streaming mic frames prefixed with the
  run's `stt_binary_handler_id`, dispatching `run-start`/`stt-end`/
  `intent-progress`/`intent-end`/`tts-start`/`tts-end`/`run-end`/`error`.
- `assist/MicStreamer.kt` — `AudioRecord` 16 kHz mono PCM16, raw little-endian
  frames straight onto the wire, with an RMS level for the orb + VAD.
- `assist/TtsPlayer.kt` — `MediaPlayer` playing HA's `tts_output.url` with the
  bearer token as a request header.
- The activity runs the turn cycle **LISTENING → (VAD end-of-speech) →
  THINKING → SPEAKING (TTS) → LISTENING**, supports **barge-in** (talking over
  the reply cancels TTS and starts a new turn), continues multi-turn via
  `conversation_id`, and closes on an inactivity timeout, a tap, or Back.

This depends on **no** HA-app internals — only the public WebSocket API and a
URL/token from `JarvisConfig`. OkHttp/okio are already on the app classpath
(HA uses them), so the flavor needs no extra dependency. Tradeoff vs. the old
handoff: we now own the pipeline plumbing (mirrored from the tested web
client) instead of borrowing HA's, in exchange for the orb owning the whole
experience.

### Configuration (first run)

The client needs the HA base URL and a long-lived token. On first launch (or
if unconfigured) `JarvisAssistActivity` opens `JarvisSettingsActivity` — a
minimal form for **HA URL**, **access token** (profile → Security →
Long-lived access tokens), and **pipeline name** (default `Jarvis`), stored in
a private `SharedPreferences` file (`JarvisConfig`), separate from the HA
app's own session. Use the URL you reach HA on (WireGuard/LAN). `RECORD_AUDIO`
is requested at runtime on first use.

## 2. Activation UX spec

Target: **≤ 300 ms from assist trigger to visible + haptic feedback**, and
listening as soon as the pipeline's `run-start` arrives.

| t (ms) | What happens |
|---|---|
| 0 | ACTION_ASSIST / assist gesture / VoiceInteractionSession.onShow → `JarvisAssistActivity` (transparent platform theme, immersive, no window animation). |
| first frame | Haptic tick (`VibrationEffect.EFFECT_TICK`, fallback `KEYBOARD_TAP`). Edge-light sweep (350 ms) + arc-reactor orb scale-in (260 ms). Mic capture + WebSocket connect begin. |
| ~250–500 | Auth + pipeline resolve complete; `run-start` arrives, orb enters LISTENING, mic frames stream. |
| conversation | LISTENING → THINKING → SPEAKING → LISTENING, orb colour + caption tracking each state; transcript and streamed response render in the lower third. |

Orb visual language mirrors the web HUD: **cyan** idle/listening, **amber**
thinking, **gold** speaking (`JarvisOrbView.Mode`), breathing scale at rest,
radius/glow modulated by mic amplitude (`setAmplitude(0..1)`).

Measure cold start:

```bash
adb shell am force-stop io.homeassistant.companion.android.minimal
adb shell am start -W -a android.intent.action.ASSIST
# TotalTime / WaitTime in ms; repeat 5x, take the median
```

### Lock-screen activation

`JarvisAssistActivity` sets `showWhenLocked` + `turnScreenOn` (manifest and
runtime API 27+), so a long-press / "Hey Jarvis" works with the screen off or
locked, showing the orb over the keyguard. The voice-interaction session uses
`startAssistantActivity()`, the assistant-privileged path allowed from the
background and over the keyguard. Because the whole conversation now runs
inside `JarvisAssistActivity` itself, the full voice interaction — including
the spoken reply — works over the keyguard without unlock (voice-first, no
WebView needed).

## 3. Assistant role on GrapheneOS

The app declares a `VoiceInteractionService`
(`JarvisVoiceInteractionService` + session service + trampoline session), so
it is offered as a **digital assistant app** and can hold
ROLE_ASSISTANT. Selection paths:

- UI: Settings → Apps → Default apps → Digital assistant app → Jarvis.
- adb (scripted): `scripts/adb-jarvis-role.sh`, which writes the
  `assistant`, `voice_interaction_service`, and `assist_gesture_enabled`
  Secure Settings and verifies them by reading back.

### The reinstall caveat (important)

**GrapheneOS clears `assistant` and `voice_interaction_service` Secure
Settings on every reinstall AND on updates of the assistant app.** Keeping
the same signing key preserves app data across updates — but not this role.
Practical consequence:

> After **every** Obtainium update of the Jarvis app, plug into a laptop and
> run `scripts/adb-jarvis-role.sh` (or re-pick the assistant in Settings).
> Until then, the assist gesture falls back to nothing/the previous default.

This is an OS hardening behavior, not a bug in our app; there is no
in-app workaround (an app cannot grant itself the assistant role).

## 4. Always-on "Hey Jarvis" (microWakeWord)

The HA companion app (2026.3+) ships **experimental on-device wake word**
support using microWakeWord — the same model family ESPHome voice satellites
use. `hey_jarvis` is one of the stock models. Enable under Settings →
Assist/Voice in the app. Known properties:

- **Battery-heavy by design.** Third-party apps get no access to the DSP
  low-power hotword path (that is reserved for the OEM assistant via
  `SoundTrigger`/`AlwaysOnHotwordDetector`, which requires privileged
  permissions we can't hold on GrapheneOS). Detection therefore runs as a
  normal **foreground service holding the microphone**, CPU-decoding audio
  continuously. Expect a visible battery line item and the persistent mic
  indicator.
- Requires the mic permission granted permanently ("while using the app" +
  foreground service) and notifications enabled for the foreground-service
  notification.
- **Once-per-activation quirks:** the experimental implementation stops
  detection while an Assist session is active and occasionally needs the
  toggle cycled after the pipeline errors out; treat wake word as
  best-effort and keep the assist gesture as the reliable path.

### WakeWordGate battery policy

`WakeWordGate` (pure logic, unit-testable, no Android imports) decides when
the detection service should run:

```
carBtConnected            -> listen (any hour; night drives count)
isHome && wakingHour      -> listen (default window 07:00–22:59)
otherwise                 -> off (pocket/away = wasted battery + open mic in public)
```

Inputs are injected: home-zone state from the companion's zone sensors /
server state, car BT from `BluetoothProfile` connection callbacks, hour from
the clock. Wiring the gate to start/stop the wake word foreground service is
a small amount of glue in the flavor (or, simpler and shipped first: an HA
automation that flips the app's wake word setting via notification command —
see `docs/android-auto.md` for the car-BT trigger design).

## 5. P5 acceptance gate (real Pixel, GrapheneOS)

Run in order; all must pass:

1. **Install & role.** Install signed APK via Obtainium; run
   `scripts/adb-jarvis-role.sh`; script exits 0 with all three settings
   verified.
2. **Assist intent.** `adb shell am start -a android.intent.action.ASSIST`
   → orb + edge sweep + haptic appear, then HA Assist UI listening.
3. **Latency.** `adb shell am force-stop io.homeassistant.companion.android.minimal`
   then `adb shell am start -W -a android.intent.action.ASSIST`:
   median TotalTime over 5 runs **≤ 300 ms**.
4. **Voice round trip.** Assist gesture → speak "turn on the office lamp" →
   lamp turns on, TTS response plays on the phone.
5. **Lock screen.** Screen off, phone locked → trigger assist (gesture or
   "Hey Jarvis" if enabled) → orb shows over keyguard, voice round trip
   completes without unlocking.
6. **Update survival.** Bump version, update via Obtainium → app data
   intact; confirm the assistant role was cleared (expected) and that
   `scripts/adb-jarvis-role.sh` restores it.
