# The phone

The Android app lives in [`../android-app/`](../android-app/) and has its own
documentation. This page is the map, plus the few platform facts that belong
to the whole project rather than to the app's source tree.

> **The old fork is gone.** Jarvis on the phone used to be an overlay applied
> to a fork of `home-assistant/android`, shipped in that project's degoogled
> `minimal` flavour. `android-app/` replaced it with a standalone app
> (`ai.jarvis.app`) that speaks the same WebSocket protocol and depends on
> none of HA's internals. The overlay, its patch script and its build notes
> were deleted; see [`removed.md`](removed.md).

## Where to read what

| topic | doc |
|---|---|
| Building, installing, granting the roles, pointing it at your server | [`../android-app/README.md`](../android-app/README.md) |
| Launch behaviour, hardening, crash diagnosis on GrapheneOS | [`../android-app/docs/grapheneos.md`](../android-app/docs/grapheneos.md) |
| The action registry and the on-device policy engine | [`../android-app/docs/actions.md`](../android-app/docs/actions.md) |
| Triggers and the task engine | [`../android-app/docs/automations.md`](../android-app/docs/automations.md) |
| The device command channel | [`../android-app/docs/device-channel.md`](../android-app/docs/device-channel.md) |
| UI automation via the accessibility service | [`../android-app/docs/ui-automation.md`](../android-app/docs/ui-automation.md) |
| Why the HA fork crashed on GrapheneOS in the first place | [`grapheneos.md`](grapheneos.md) |
| Voice in the car, and why it is limited | [`android-auto.md`](android-auto.md) |
| Training a `hey_jarvis` wake word | [`wake-word-training.md`](wake-word-training.md) |

## The assistant-role caveat (still true, still annoying)

**GrapheneOS clears the `assistant` and `voice_interaction_service` Secure
Settings on every reinstall AND on every update of the assistant app.**
Keeping the same signing key preserves app data across updates — but not this
role.

> After **every** update of the Jarvis app, run
> `scripts/adb-jarvis-role.sh` (USB debugging enabled), or re-pick the
> assistant under Settings → Apps → Default apps → Digital assistant app.
> Until then the assist gesture falls back to nothing, or to whatever the
> previous default was.

This is OS hardening, not a bug, and there is no in-app workaround: an app
cannot grant itself the assistant role.

## Activation target

**≤ 300 ms from assist trigger to visible + haptic feedback**, listening as
soon as the pipeline's `run-start` arrives. Measure a cold start with:

```bash
adb shell am force-stop ai.jarvis.app
adb shell am start -W -a android.intent.action.ASSIST
# TotalTime / WaitTime in ms; repeat 5x, take the median
```

## Wake word costs battery, by design

Third-party apps get no access to the DSP low-power hotword path — that is
reserved for the OEM assistant via `SoundTrigger`/`AlwaysOnHotwordDetector`,
which needs privileged permissions no sideloaded app can hold. On-device wake
detection therefore runs as an ordinary foreground service holding the
microphone and CPU-decoding audio continuously. Expect a visible battery line
item and a persistent mic indicator.

That is why the app gates *when* it listens rather than listening always:

```
carBtConnected            -> listen (any hour; night drives count)
isHome && wakingHour      -> listen (default window 07:00–22:59)
otherwise                 -> off (pocket/away = wasted battery + open mic in public)
```

Keep the assist gesture as the reliable path; treat wake word as best-effort.
