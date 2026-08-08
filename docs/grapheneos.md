# Running Jarvis on GrapheneOS

GrapheneOS is the target platform, not an afterthought. This documents why the
old Home-Assistant-fork build crashes there, what the standalone app does
differently, and how to diagnose a crash properly.

## Why the HA fork crashes on GrapheneOS

The Home Assistant Android app is built around Google Play Services. Even the
`minimal` (degoogled) flavour carries a lot of machinery that assumes a normal
Google-flavoured Android. The usual failure modes on GrapheneOS:

| Cause | What you see |
|---|---|
| **Play Services / Firebase init** — the `full` flavour hard-depends on GMS; a mock `google-services.json` makes it *build* but the runtime init still throws | Immediate crash on launch, `FATAL EXCEPTION` mentioning `com.google.android.gms` or `FirebaseApp` |
| **Network permission denied** — GrapheneOS adds a per-app **Network** toggle that plain Android doesn't have. Apps that assume `INTERNET` is always granted get an unexpected `SecurityException`/`UnknownHostException` | Hangs on the loading screen, or crashes on first request |
| **Sensors permission toggle** — GrapheneOS can deny the sensors group; HA's sensor manager touches many sensors at startup | Crash or repeated errors from the sensor worker |
| **hardened_malloc** — GrapheneOS's allocator aborts on heap corruption that other ROMs tolerate silently | `SIGABRT` in a native library, no Java stack trace |
| **Memory tagging (MTE)** on Pixel 8+ | Native `SIGSEGV`/`SIGABRT` with `MTE` in the tombstone |
| **exec-based spawning** (no zygote preload) | Slow first launch; native libs with bad assumptions fail |
| **`QUERY_ALL_PACKAGES` restrictions** | App can't see other apps; feature silently broken |
| **Background execution limits** | Foreground service killed, wake word stops |

The honest summary: the HA app is not tested on GrapheneOS, and its
Google-oriented plumbing is the main source of instability.

## What the standalone app does differently

`android-app/` (package `ai.jarvis.app`) is a fresh project, not a fork:

- **No Google Play Services, no Firebase, no `google-services.json`, no
  Play Integrity.** Nothing to fail to initialise.
- **No native libraries** — pure Kotlin/AndroidX, so `hardened_malloc` and MTE
  have nothing to abort on.
- **Networking is optional-by-design.** The app checks for the GrapheneOS
  Network permission and shows a clear "Network permission denied — enable it in
  Settings → Apps → Jarvis → Permissions → Network" screen instead of throwing.
- **Every permission is requested at runtime and every failure is handled.**
  A denied permission degrades one action, never crashes the app. Actions
  return `permission X not granted` as a result, not an exception.
- **Uses `<queries>` rather than relying on `QUERY_ALL_PACKAGES`** where the
  app only needs specific intents.
- **A global crash handler** writes the stack trace to app storage and shows it
  in Settings → Crash logs, so a crash is diagnosable without a laptop.

## Diagnosing a crash (do this before reporting one)

```bash
scripts/collect-crash-logs.sh                 # auto-detects installed packages
scripts/collect-crash-logs.sh ai.jarvis.app   # or name it explicitly
```

It writes a report directory. Read in this order:

1. `40-SUMMARY.txt` — FATAL/AndroidRuntime lines and anything mentioning the package
2. `20-exit-info-*.txt` — the OS's own record of **why the process died**
   (`dumpsys activity exit-info`): crash, ANR, low memory, or user stop
3. `11-permissions-*.txt` — granted/denied runtime permissions, **including the
   GrapheneOS Network toggle**, which is the single most common "it just doesn't
   work" cause
4. `00-device.txt` — OS build, security patch, MTE / exec-spawn state

If the crash hasn't happened since boot, the buffers may be empty. Capture it
live:

```bash
<report-dir>/RUN-LIVE-CAPTURE.sh ai.jarvis.app
# then reproduce the crash, Ctrl-C, and send live-crash.txt
```

## GrapheneOS setup checklist

After installing the APK:

1. **Network permission** — Settings → Apps → Jarvis → Permissions → **Network**
   → Allow. Without this nothing reaches the server.
2. **Microphone** — needed for voice; requested at first use.
3. **Assistant role** — `scripts/adb-jarvis-role.sh`, or Settings → Apps →
   Default apps → Digital assistant app → Jarvis.
   **This clears on every reinstall/update** — re-run it after each update.
4. **Accessibility** (only if you want UI automation) — Settings →
   Accessibility → Jarvis. This is powerful; read `android-app/docs/ui-automation.md`
   before enabling it.
5. **Notification access** (only for notification triggers) — Settings →
   Notifications → Device & app notifications → Jarvis.
6. **Battery** — Settings → Apps → Jarvis → Battery → **Unrestricted**, so the
   automation foreground service and wake word survive.
7. **Exact alarms** — allow if you use time-based automations.

## Known GrapheneOS limitations (not bugs)

- Toggling Wi-Fi/Bluetooth/mobile data programmatically is not possible for any
  third-party app on modern Android; Jarvis opens the relevant settings panel
  instead.
- Always-on wake word costs battery: third-party apps get no low-power DSP path,
  which is why the wake gate (home zone / car Bluetooth / waking hours) exists.
- Secure Settings written by `adb` (the assistant role) are cleared on reinstall
  by design.
