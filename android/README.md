# Jarvis Android companion (overlay for home-assistant/android)

We do **not** vendor a Home Assistant Android fork in this repo. Instead this
directory holds an **overlay + patch script** that turns any fresh
`home-assistant/android` checkout (Apache-2.0) into the Jarvis companion app
by adding a third product flavor, `jarvis`, next to upstream's `full` and
`minimal`.

## What the jarvis flavor adds

| Piece | Purpose |
|---|---|
| `JarvisAssistActivity` | Siri-like activation: transparent activity, edge-light sweep + rising orb (`JarvisOrbView`) + haptic tick, then forwards into HA's own `AssistActivity` (Assist pipeline, `startListening=true`). Target: <300 ms cold-to-listening. Works on the lock screen. |
| `JarvisVoiceInteractionService` (+ session service/session) | Lets the app hold ROLE_ASSISTANT / be the device assistant on GrapheneOS. The session is a trampoline that launches `JarvisAssistActivity`. |
| `WakeWordGate` | Pure-logic battery gate for always-on "Hey Jarvis" (home zone / car Bluetooth / waking hours). The detection itself is the HA app's existing microWakeWord support. |
| Flavor plumbing (`overlay/patches/apply.py`) | `jarvis` flavor extends **minimal** (no Google Play Services — GrapheneOS is degoogled): appId suffix `.jarvis`, minimal's source dirs + deps mirrored in, mock `google-services.json` guard, and a tiny public `newJarvisIntent()` helper patched into `AssistActivity` so the overlay never touches HA-internal intent extras. |

## Layout

```
android/
├── README.md                 <- you are here
├── keystore.md               <- signing + Obtainium distribution
├── apply-to-fork.sh          <- clone/copy/patch, safe to re-run
└── overlay/
    ├── app/src/jarvis/       <- complete flavor source set (copied verbatim)
    │   ├── AndroidManifest.xml
    │   ├── java/io/homeassistant/companion/android/jarvis/*.kt
    │   └── res/{values/styles.xml, xml/jarvis_voice_interaction_service.xml}
    └── patches/apply.py      <- idempotent Gradle/Kotlin patcher
```

## Build

```bash
cd android
./apply-to-fork.sh                    # clones ha-android-fork/ and applies overlay
# or: HA_ANDROID_DIR=~/src/ha-android ./apply-to-fork.sh

cd ha-android-fork
./gradlew :app:assembleJarvisRelease  # then sign per keystore.md
```

Requires JDK 21 and the Android SDK; the fork's `versions` files pin the
rest. The resulting applicationId is
`io.homeassistant.companion.android.jarvis`, so it installs alongside a stock
HA app if one is present.

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
