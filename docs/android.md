# P5 — Android companion app (Jarvis flavor of home-assistant/android)

The phone side of Jarvis is a fork of
[home-assistant/android](https://github.com/home-assistant/android)
(Apache-2.0) with a third product flavor, `jarvis`, applied as an overlay —
see `android/README.md` for the mechanics. This doc covers the design
decisions, the GrapheneOS caveats, and the P5 acceptance gate.

## 1. Fork + overlay build

We never vendor the fork. `android/apply-to-fork.sh`:

1. clones `home-assistant/android` at depth 1 (or reuses `HA_ANDROID_DIR`),
2. copies `android/overlay/app/src/jarvis/` → `app/src/jarvis/`,
3. runs `android/overlay/patches/apply.py` (idempotent), which
   - registers the `jarvis` product flavor (dimension `version`,
     `applicationIdSuffix ".jarvis"`, `versionNameSuffix "-jarvis"`),
   - wires the jarvis source set to also compile `src/minimal/*`,
   - mirrors `minimalImplementation` deps as `jarvisImplementation`,
   - inserts a two-line public `newJarvisIntent()` helper into
     `AssistActivity`'s companion object,
   - writes a mock `app/google-services.json` if none exists.

Build: `./gradlew :app:assembleJarvisRelease`, sign with our own keystore
(`android/keystore.md`), distribute via GitHub Releases + Obtainium.

### Flavor rationale: extend `minimal`, not `full`

Upstream ships two flavors: `full` (Google Play Services: FCM push, fused
location, Wear) and `minimal` (no GMS). The target device runs GrapheneOS,
which is degoogled; even with sandboxed Play services installed we don't want
the app depending on them. So `jarvis` **extends `minimal`**: it compiles
minimal's flavor-specific classes (WebSocket-based push, framework location)
plus the jarvis source set on top. Consequences:

- Push arrives over the app's persistent WebSocket connection, not FCM.
- No Play-services crash reporting/analytics — fine, we don't want them.
- The mock `google-services.json` exists only so tooling that keys off the
  file's presence doesn't break; **no flavor built from it can use FCM**,
  which is expected.

### The `AssistActivity` handoff (documented tradeoff)

`JarvisAssistActivity` deliberately does *not* reach into HA's Assist
ViewModel/pipeline internals — those are private and churn upstream. It owns
the first ~250 ms (haptic, edge sweep, orb rise) and then forwards to HA's
`AssistActivity` with `startListening = true`, `fromFrontend = false`, via
the patched-in public helper `AssistActivity.newJarvisIntent(context)`. The
handoff is a plain crossfade (no shared elements): mic capture starts when
`AssistActivity` starts it, ~250 ms in, not at frame zero. That keeps the
overlay robust against upstream refactors at the cost of one crossfade frame;
if upstream changes `newInstance()`, the build fails loudly at the two-line
helper instead of misbehaving at runtime.

## 2. Activation UX spec

Target: **≤ 300 ms from assist trigger to visible + haptic feedback**, and
listening as soon as `AssistActivity` attaches the pipeline.

| t (ms) | What happens |
|---|---|
| 0 | ACTION_ASSIST / assist gesture / VoiceInteractionSession.onShow → `JarvisAssistActivity` (transparent platform theme, no AppCompat, no window animation). |
| first frame | Haptic tick (`VibrationEffect.EFFECT_TICK`, fallback `KEYBOARD_TAP`). Edge-light sweep starts (350 ms, stroked rounded-rect + rotating sweep gradient). Orb rises from bottom (250 ms, decelerate). |
| ~250 | Forward to `AssistActivity` (`startListening=true`) with fade crossfade; `JarvisAssistActivity` finishes (`noHistory`, `excludeFromRecents`). |
| ~250–300 | HA Assist UI up, pipeline connecting, mic opens. |

Orb visual language mirrors the web HUD: **cyan** idle/listening, **amber**
thinking, **gold** speaking (`JarvisOrbView.Mode`), breathing scale at rest,
radius/glow modulated by mic amplitude (`setAmplitude(0..1)`).

Measure cold start:

```bash
adb shell am force-stop io.homeassistant.companion.android.jarvis
adb shell am start -W -a android.intent.action.ASSIST
# TotalTime / WaitTime in ms; repeat 5x, take the median
```

### Lock-screen activation

`JarvisAssistActivity` sets `showWhenLocked` + `turnScreenOn` (manifest and
runtime API 27+), so a long-press / "Hey Jarvis" works with the screen off or
locked, showing the orb over the keyguard. The trampoline session uses
`startAssistantActivity()`, which is the assistant-privileged path allowed
from the background and over the keyguard. Note the handoff target
(`AssistActivity`) draws over the keyguard only as far as upstream allows;
anything that needs the WebView (dashboard follow-ups) still requires unlock
— acceptable for voice-first use.

## 3. Assistant role on GrapheneOS

The `jarvis` flavor declares a `VoiceInteractionService`
(`JarvisVoiceInteractionService` + session service + trampoline session), so
the app is offered as a **digital assistant app** and can hold
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
3. **Latency.** `adb shell am force-stop io.homeassistant.companion.android.jarvis`
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
