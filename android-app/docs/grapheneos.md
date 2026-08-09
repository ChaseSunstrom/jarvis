# GrapheneOS: launch, hardening, diagnosis

GrapheneOS is the target platform for this app, not a device it happens to
install on. The Home-Assistant fork it replaces crashed there constantly and
the reasons are catalogued in the repo-level `docs/grapheneos.md`; this file is
the app-side half — what `android-app/` actually does about it, and where to
look when something still goes wrong.

Code:

| file | what | Android? |
|---|---|---|
| `ui/BootTimeline.kt` | the power-on sequence as a function of elapsed ms | **no — pure** |
| `ui/JarvisBootAnimation.kt` | the overlay that plays it, on one clock | yes |
| `ui/JarvisOrbView.kt` | the orb, with the boot-drive hooks | yes |
| `ui/SystemCheckActivity.kt` | the requirements checklist screen | yes |
| `ui/CrashLogActivity.kt` | crash logs, on the phone | yes |
| `compat/GrapheneCompat.kt` | network verdict, battery, requirements | mostly pure |
| `crash/JarvisCrashHandler.kt` | the global handler and its JSONL log | yes |

The pure parts are mirrored by an executable spec that needs no Android SDK:

```bash
python3 android-app/tools/boot_timeline_test.py     # 53 checks
```

It also reads the Kotlin back and fails if the two copies have drifted.

---

## 1. Launch: no white flash, ever

Three things happen between tapping the icon and the HUD, and all three are
the same colour.

**The system splash** (`res/values-v31/themes.xml`). From API 31 the platform
draws a splash on every cold start whether you ask for it or not, using
`windowBackground` and the launcher icon. Left alone on a light-themed device
that is a white flash followed by a black app. So it is configured explicitly:
`windowSplashScreenBackground` is `@color/jarvis_bg` (`#04070C`, the same value
as `JarvisUi.BG`), and the icon is `@drawable/ic_jarvis_splash` with a
transparent backplate.

**The handoff.** `MainActivity.installSplashHandoff()` calls
`setOnExitAnimationListener` and removes the splash view immediately rather
than animating it out — the boot sequence's first frame is black, which is what
the splash was already showing, so there is nothing to cross-fade. The whole
thing is wrapped in a `try`, and a timer (`SPLASH_FALLBACK_MS`) starts the boot
anyway if the listener never fires, because a ROM with a broken `SplashScreen`
implementation must cost a beat rather than a permanently black screen.

**The power-on.** See below.

Below API 31 there is no platform splash; the boot animation starts directly.

### The boot animation

~1.4 s, tap anywhere to skip:

```
   0 ms  black; a single hairline scan sweeps top -> bottom
 120 ms  the reactor core ignites from a point, with a bloom flare
 300 ms  rings materialise outward one at a time, each overshooting slightly
         (inner rim -> dashed mid -> fine dashes -> gauge ticks)
 600 ms  "J A R V I S" resolves in, per-letter fade + blur, spacing settling
         from 0.90em to 0.55em
 850 ms  three system-check lines type on in monospace, right-aligned
1200 ms  everything but the orb fades; the home UI fades up around it
```

Two design rules, both enforced by the spec:

**One clock.** A single `ValueAnimator` runs 0 → 1 and every frame asks
`BootTimeline` what to draw at that millisecond. No `postDelayed` chains, so
nothing can fall out of sync, nothing leaks past `onDetachedFromWindow`, and
`skip()` is just "set the clock to `TOTAL_MS`" — it lands on exactly the frame
the full sequence would have ended on, because it runs the same functions.

Order matters inside `skip()`, and it is easy to get backwards. `Animator
.cancel()` sends `onAnimationCancel` **and then `onAnimationEnd`**, so
cancelling re-enters the view's own end listener. The clock is therefore moved
and the final frame pushed *before* the cancel; otherwise that re-entrant
`finish()` settles the orb and detaches the overlay while the clock is still
mid-sequence, and every statement after the cancel runs against a detached view
whose `orb` and callbacks the detach has already nulled.

**One orb.** The overlay does not draw the reactor. It is transparent, and it
drives the real `JarvisOrbView` that the home screen already owns via
`setBootDrive()`. The orb does not jump at the handoff because it never changed
object — only who was telling it what size to be. The overlay's wordmark lands
on `JarvisOrbView.wordmarkBaselineY()` at `WORDMARK_SPACING`, the orb's own
resting metrics, so the two are pixel-identical when they cross-fade.

The third check line uses real data — how many actions this device registered
with the server the last time `jarvis/device/register` succeeded, persisted by
`JarvisConfig.lastActionCount` and written by `JarvisChannel` on `registered`.
A missing value, a wrong type, or a locked-storage read all mean the line is
simply omitted; it never types "0 actions ready".

It counts *actions*, not devices, because that is the only number of this shape
the phone actually learns — the register result answers with the size of the
manifest the server accepted. There is a spec check (`test_the_third_check_line
_has_something_writing_its_input`) that fails if the writer ever disappears: a
boot line whose input nothing writes is a line that never appears, and it looks
exactly like a working feature in a screenshot.

**Reduced motion is respected.** If `Settings.Global.ANIMATOR_DURATION_SCALE`
is 0, or `TRANSITION_ANIMATION_SCALE` is 0 (Android has no "prefers reduced
motion" flag; that is the closest the platform gives a user), the sequence
collapses to its end state on the first frame and never animates. A slowed
scale is honoured and capped at `MAX_DURATION_MS`, so a developer-options 10x
cannot hang the launcher.

On API 31+ the sequence is normally started by the splash-exit listener, but a
sequence that will not play does not wait for it — `willPlay()` is false and
`MainActivity` starts (and therefore immediately completes) it inline. Waiting
would hold the home controls at alpha 0 for the length of a splash exit, which
is a black screen for the one user who explicitly asked for no animation.

**Cold start only.** The flag lives in the `JarvisApp` Application object
(`consumeColdStart()`), not in the Activity, so a rotation, a return from
Settings, or a resume from recents never replays it. `savedInstanceState != null`
is treated as "not a launch" as well.

One consequence worth knowing if you touch `JarvisOrbView`: the ignition starts
the core at a radius of *exactly zero*, and `RadialGradient` throws on a zero
radius while a `DashPathEffect` with zero-length intervals is undefined in
Skia. Every drawing primitive in the orb bails out below `MIN_DRAW_PX`. Before
the boot existed the orb never went below 70% scale and none of this mattered.

---

## 2. The requirements checklist

**Settings → SYSTEM CHECK**, or the banner on the home screen.

On GrapheneOS an app can be installed, launched, and completely inert because a
toggle the user has never heard of is off. `GrapheneCompat.requirements()`
answers that with a list: what Jarvis can use, whether it has it, what breaks
without it, and a button that opens the exact settings page.

| id | essential | opens |
|---|---|---|
| `network` | yes | app details (per-app **Network** toggle) |
| `microphone` | yes | app details |
| `assistant` | no | `VOICE_INPUT_SETTINGS` |
| `accessibility` | no | `ACCESSIBILITY_SETTINGS` |
| `notifications` | no | `ACTION_NOTIFICATION_LISTENER_SETTINGS` |
| `battery` | yes | `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` |
| `exact_alarms` | no | `REQUEST_SCHEDULE_EXACT_ALARM` |

Network is first because it is the single most common GrapheneOS surprise.
Battery counts as satisfied only when the app is exempt from doze **and** not
background-restricted — either one alone still gets the automation service
killed.

The home screen shows a banner whenever an essential requirement is missing,
and that banner is the loudest route to the System Check screen. That is
deliberate: a diagnostics screen that only exists in a menu is one nobody finds
on the day they need it. It is not the only route, though — Settings → More
also has SYSTEM CHECK and CRASH LOGS, because "everything is granted, but the
server still is not answering" is a real state and the banner is silent in it.

### The Network toggle specifically

`INTERNET` is an install-time permission on stock Android — `checkSelfPermission`
returns `GRANTED` for the life of the install and there is no supported way for
a user to take it back. GrapheneOS adds a per-app **Network** toggle on top of
it. Whether flipping that toggle is visible to `checkSelfPermission` depends on
the OS version and on how the block is enforced; what is certain is that the
sockets fail. So the permission check is a useful signal, not a reliable one,
and `GrapheneCompat` folds in what actually happened on the wire:

```kotlin
GrapheneCompat.noteNetworkFailure(throwable)   // JarvisChannel.Session.onFailure
GrapheneCompat.noteNetworkSuccess()            // onOpen, and onFailure with a response
```

Those call sites are the feature. Without them the verdict is only ever the
permission check — the one signal this section just said cannot be relied on —
and `SUSPECT` is unreachable. `onFailure` with a non-null `response` counts as a
*success*: we reached the server and it answered, even if what it answered was
401.

`classify()` reads the cause chain. A `SecurityException` anywhere in it is the
OS telling us directly, and denies immediately. An `UnknownHostException` is
circumstantial — nothing resolving is what a blocked app sees, but it is also
what a typo sees — so it takes `SUSPECT_THRESHOLD` (3) consecutive ones before
the verdict becomes `SUSPECT` and the banner hedges with "if this is
GrapheneOS". Everything else (connection refused, timeouts, TLS) is a
server-side story and never accuses the user's settings. One success outranks
any amount of accumulated *suspicion*.

It does not outrank a `SecurityException`, and that ordering is deliberate. The
Network toggle is revocable while the app is running, and `noteNetworkSuccess()`
clears the denial counters — so a non-zero `securityDenials` can only have been
recorded after the last success. Ranking the stale success higher would pin the
verdict to `GRANTED` for the rest of the process the moment somebody revoked
Network mid-session, and the banner explaining the outage would never appear.

The banner text names the path, because "check your network permissions" helps
nobody:

```
Network permission denied — Settings → Apps → Jarvis → Permissions → Network
```

### Package visibility

`AndroidManifest.xml` declares a `<queries>` block for every intent Jarvis
hands off to — launcher entries, browser, dialler, SMS, geo, share, the QR
scanner, Shizuku. None of those needs `QUERY_ALL_PACKAGES`, so they keep
working on a device where that permission has been stripped. Only "list every
installed app by name" degrades, and it degrades to a clear "no matching app"
rather than a crash.

---

## 3. Crash logs, on the phone

**System Check → CRASH LOGS.**

```
filesDir/jarvis/crashes.jsonl
```

One JSON object per line, newest last, app-private, excluded from backups,
never sent anywhere. Rotating: the newest `MAX_RECORDS` (50) lines, and under
`MAX_FILE_BYTES` (512 KiB) whatever they contain. Rewritten via a temp file and
a rename, so a kill halfway through leaves either the old log or the new one.

Each record carries the timestamp, thread, exception class and message, the
full stack trace (truncated at 12 000 chars, then redacted), app version and version code,
Android release and SDK level, device manufacturer and model, and the build
fingerprint — which is how you tell a GrapheneOS build from a stock one at a
glance.

The screen lists headlines, opens one full report, copies to the clipboard, and
clears. There is no upload button and no crash reporting service.

**The message and the stack trace are redacted on the way in**, through
`channel/Redact.kt` — the same masking the command channel uses to keep the
bearer token out of logcat. That is not belt-and-braces here. This screen's
whole purpose is a COPY button, so a crash report is the one diagnostic in the
app that is *expected* to leave the device, and exceptions out of OkHttp or a
JSON parser routinely quote the frame or the URL that failed. Redacting on
write rather than on display matters too: the file is what the button reads.
The clipboard entry is flagged `EXTRA_IS_SENSITIVE` on Android 13+ so the
system's copy preview does not render it either.

Four properties of `JarvisCrashHandler`, in order of importance:

1. **It is installed first.** `JarvisCrashHandler.install(this)` is the first
   statement in `JarvisApp.onCreate`, before `super.onCreate()` and before the
   notification channels. The crashes most worth catching are the ones during
   startup, and a handler installed after them catches nothing. The spec
   asserts this literally.
2. **It never becomes the crash.** Every step is wrapped. A handler that throws
   while handling replaces a diagnosable failure with a mysterious one, and on
   a hardened OS the difference between those is a week.
3. **It always delegates.** The previous handler — normally the platform's,
   which is what actually kills the process and writes the tombstone — is
   called in a `finally`. Swallowing the exception would leave a process alive
   with a dead thread and no window, which is worse than a crash.
4. **It redacts before it writes.** See above. The redactor is itself wrapped:
   a regex that somehow blew up would cost the text, never the record.

If the app dies before this is installed (a linker failure, an OOM at zygote
fork), nothing here will have it. That is what the repo-level
`scripts/collect-crash-logs.sh` and `dumpsys activity exit-info` are for.

---

## 4. Rules for anything added to this app

These are the ones that bite specifically on GrapheneOS, and the spec checks
several of them mechanically.

- **Every `getSystemService` is nullable.** Use the `Class<T>` overload and
  handle null — `?: return`, `?.let`, or an explicit branch. There is no
  device on which every service is guaranteed present, and hardened builds
  remove more than most.
- **Every permission-guarded call is wrapped.** A denied permission degrades
  one action and returns `permission X not granted` as a *result*. It is never
  an exception that reaches the UI.
- **No `!!` on a platform type.** A value that came from the framework is
  nullable no matter what the signature says.
- **`registerReceiver` passes an export flag on API 33+.** See
  `ContextCompatRegister` in the automation module. A null-receiver sticky
  broadcast read is exempt — it is a query, not a registration.
- **No native libraries.** `hardened_malloc` aborts on heap corruption other
  allocators tolerate silently, and MTE on Pixel 8+ turns a latent bug into a
  `SIGSEGV` with no Java stack trace. Pure Kotlin/AndroidX gives both nothing
  to abort on.
- **No Play Services, no Firebase, no `google-services.json`.** Nothing to
  fail to initialise.
- **Nothing reads a hidden `Settings` key.** Use the public API
  (`RoleManager`, `AccessibilityManager`,
  `NotificationManager.isNotificationListenerAccessGranted`,
  `AlarmManager.canScheduleExactAlarms`). Hidden keys are exactly what a
  hardened build restricts, and reading one is a `SecurityException` on launch.

The APK also ships without AGP's dependency-metadata blob
(`dependenciesInfo { includeInApk = false }`): it is a Google-signed encrypted
section nothing on a degoogled phone can read, and it makes builds
non-reproducible.

---

## Setup order on a fresh install

The System Check screen walks this, but for the record:

1. **Network** — Settings → Apps → Jarvis → Permissions → Network → Allow.
   Nothing reaches the server without it.
2. **Microphone** — requested at first use.
3. **Battery** — Settings → Apps → Jarvis → Battery → Unrestricted.
4. **Assistant role** — `scripts/adb-jarvis-role.sh`, or Settings → Apps →
   Default apps → Digital assistant app. **This clears on every reinstall**;
   re-run it after each update.
5. **Exact alarms** — only if you use time-based automations.
6. **Accessibility** — only for UI automation. Read `ui-automation.md` first.
7. **Notification access** — only for notification triggers.
