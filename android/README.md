# Jarvis Android companion (overlay for home-assistant/android)

We do **not** vendor a Home Assistant Android fork in this repo. Instead this
directory holds an **overlay + patch script** that turns any fresh
`home-assistant/android` checkout (Apache-2.0) into the Jarvis companion app
by adding a third product flavor, `jarvis`, next to upstream's `full` and
`minimal`.

## What the jarvis flavor adds

| Piece | Purpose |
|---|---|
| `JarvisAssistActivity` | Siri-like activation **and** the full conversation: transparent immersive activity, edge-light sweep + arc-reactor orb (`JarvisOrbView`) + haptic tick, then runs the whole voice turn itself (LISTENING→THINKING→SPEAKING→loop) with barge-in. Target: <300 ms cold-to-listening. Works on the lock screen. |
| `assist/AssistPipelineClient`, `assist/MicStreamer`, `assist/TtsPlayer` | Self-contained Assist client: OkHttp WebSocket to HA's public API (Kotlin port of the web `pipeline.ts`), `AudioRecord` 16 kHz mic streaming, and `MediaPlayer` TTS playback. No HA-app internals. |
| `JarvisConfig` / `JarvisSettingsActivity` | HA URL + long-lived token + pipeline name, stored privately; a minimal settings form shown on first run. |
| `JarvisVoiceInteractionService` (+ session service/session) | Lets the app hold ROLE_ASSISTANT / be the device assistant on GrapheneOS. The session is a trampoline that launches `JarvisAssistActivity`. |
| `WakeWordGate` | Pure-logic battery gate for always-on "Hey Jarvis" (home zone / car Bluetooth / waking hours). The detection itself is the HA app's existing microWakeWord support. |
| Overlay plumbing (`overlay/patches/apply.py`) | Jarvis code lives in **`src/main`** and ships in the existing degoogled **`minimal`** flavor (no Google Play Services). The patcher just merges Jarvis permissions + components into `src/main/AndroidManifest.xml` and writes a mock `google-services.json`. No custom flavor, so there's no brittle flavor-inheritance to maintain. Installed applicationId: `io.homeassistant.companion.android.minimal` (debug adds `.debug`). |

## Layout

```
android/
├── README.md                 <- you are here
├── keystore.md               <- signing + Obtainium distribution
├── apply-to-fork.sh          <- clone/copy/patch, safe to re-run
└── overlay/
    ├── app/src/main/         <- copied into the fork's main source set
    │   ├── kotlin/io/homeassistant/companion/android/jarvis/*.kt
    │   │       └── assist/{AssistPipelineClient,MicStreamer,TtsPlayer}.kt
    │   └── res/{values/jarvis_styles.xml, xml/jarvis_voice_interaction_service.xml}
    └── patches/
        ├── apply.py               <- idempotent manifest-merge patcher
        └── flavor-manifest.old.xml <- reference: the old flavor manifest
```

## Build

### The easy way: GitHub Actions

`.github/workflows/android-apk.yml` builds the APK for you — no local Android
SDK needed:

- **Actions → Build Jarvis APK → Run workflow** (build type `debug` needs no
  secrets and is directly installable), then download the APK from the run's
  **Artifacts**. The run summary prints the exact `adb` assistant-role
  commands for the APK's actual package id.
- Push a tag `vX.Y.Z` to build and attach the APK to a **GitHub Release**
  (defaults to a signed release build).

For **stable, updatable release** signing (so Obtainium can update in place),
add these repo secrets (see `keystore.md` to create the keystore):
`ANDROID_KEYSTORE_BASE64` (`base64 -w0 jarvis-release.keystore`),
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`.
Without them, a release build is signed with a throwaway key (installs fine,
but updating requires an uninstall first).

Note: `debug` builds may carry a `.debug` package suffix — that's why the
workflow prints the role commands with the resolved package id rather than
assuming one.

### The manual way

```bash
cd android
./apply-to-fork.sh                    # clones ha-android-fork/ and applies overlay
# or: HA_ANDROID_DIR=~/src/ha-android ./apply-to-fork.sh

cd ha-android-fork
./gradlew :app:assembleMinimalDebug    # installable, no signing setup
./gradlew :app:assembleMinimalRelease  # then sign per keystore.md
```

Requires JDK 17 and the Android SDK; the fork's `versions` files pin the
rest. Jarvis rides in the **minimal** flavor, so the applicationId is
`io.homeassistant.companion.android.minimal` (a debug build adds `.debug`).
It installs alongside a stock **full**-flavor HA app (base id) if one is
present.

## The adb re-apply requirement (read this)

GrapheneOS **clears the assistant role Secure Settings on every
reinstall/update** of the app. After every Obtainium update, plug in and run:

```bash
scripts/adb-jarvis-role.sh
```

Otherwise the assist gesture / long-press-home stops launching Jarvis until
you re-select it (adb script or Settings → Default apps → Digital assistant
app). Full details, acceptance tests, and the activation UX spec live in
`docs/android.md`; Android Auto constraints in `docs/android-auto.md`; custom
wake word training in `docs/wake-word-training.md`.

## Rebase policy

The overlay anchors on stable upstream landmarks (`create("minimal")`,
`AssistActivity`'s `companion object` + `newInstance()`). When upstream moves
one, `apply.py` fails with a named, actionable error instead of silently
producing a broken tree — fix the single anchor in `apply.py` and re-run.
