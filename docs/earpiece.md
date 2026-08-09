# Earpiece and headset support

The target hardware is an over-ear earpiece worn all day — something closer to
a hearing aid than to headphones — so that talking to Jarvis costs nothing more
than talking. This page is what that actually does, and what it does not.

Everything here is **off by default**. Plugging in a headset never silently
moves the microphone; you turn on **Settings → Headset mode** once.

## Why an earpiece is a different problem

On a phone held at arm's length, the speaker and the microphone are far enough
apart that the reply does not come back in. In an earpiece they are two
centimetres apart:

```
  TTS ──► earpiece speaker ──► (2 cm of air) ──► earpiece mic ──► VAD
   ▲                                                             │
   └─────────────── "the user is talking, barge in" ◄────────────┘
```

Jarvis hears itself, the energy VAD calls it speech, and it interrupts its own
sentence — forever. Raising the VAD threshold is the wrong fix, because then
you have to shout.

The right fix is to capture through the platform's **communication** path,
where the hardware echo canceller knows what is being played and subtracts it.
That path is not free: it also applies noise suppression and automatic gain
control tuned for a phone call, and Whisper does measurably worse on the
result.

So the rule is narrow — pay the accuracy cost **only when there is an echo loop
to cancel**, which is exactly when capture and playback are the same physical
device:

| What is connected | Captures through | Source | Why |
|---|---|---|---|
| Nothing | phone mic | `VOICE_RECOGNITION` | no loop; keep the raw signal |
| Headphones (no mic) | phone mic | `VOICE_RECOGNITION` | playback moved, capture did not |
| Wired headset, USB, BT SCO, LE Audio | the headset | `VOICE_COMMUNICATION` | worn: speaker and mic are coupled |
| BT headset whose call profile is busy | phone mic | `VOICE_RECOGNITION` | capturing over a dead SCO link returns silence |

Playback usage follows the capture source, because an echo canceller with no
reference signal cancels nothing — `USAGE_VOICE_COMMUNICATION` when capturing
through the communication path, `USAGE_ASSISTANT` otherwise. The two are one
decision.

The rules live in [`android-app/app/src/main/kotlin/ai/jarvis/app/audio/AudioRoute.kt`](../android-app/app/src/main/kotlin/ai/jarvis/app/audio/AudioRoute.kt)
as pure logic, and are checked by `android-app/tools/audio_route_test.py` over
every kind × opt-in × link-availability combination.

## The headset button

One physical control, so it does the obvious things — and is not trusted with
anything else.

| Situation | A tap | A long press (≥600 ms) |
|---|---|---|
| Idle | start a turn | start a turn |
| Music playing | **pause the music** | start a turn |
| Mid-conversation | end the turn | end the turn |
| Headset mode off | goes to the media app | goes to the media app |
| **A Tier-3 prompt is waiting** | **nothing** | **nothing** |

That last row is a security boundary, not a preference. A Bluetooth media key
arrives with no indication of who pressed it, from a small object that may be
on a desk, in a bag, or in someone else's hand — and it can be pressed through
a coat pocket. Approving a payment, a message to another person or a shell
command has to cost a deliberate look at a screen and a tap on it.

So while a prompt is pending every press is swallowed: not forwarded to the
assistant, and not forwarded to the media player either, because a media app
taking audio focus can pull the prompt out from under you mid-decision. The
gate has no outcome that could approve anything —
[`MediaButtonGate.Action`](../android-app/app/src/main/kotlin/ai/jarvis/app/audio/MediaButtonGate.kt)
is `IGNORE | PASS_TO_MEDIA | START_TURN | END_TURN` — and
`tools/media_button_test.py` asserts that exhaustively across all 400 input
combinations rather than by example.

Starting a conversation from the lock screen is still allowed: that is the
feature working, and `ConsentGate` independently guarantees nothing requiring
approval can be approved until the phone is unlocked.

Presses Jarvis acted on reset the 350 ms debounce; presses handed to a media
app do not, so double-tap-to-skip keeps working in your music player.

## Warm link

With a worn headset, Jarvis can keep listening after a reply so a follow-up
needs no re-activation. It requires headset mode **and** an active echo
canceller — without cancellation an open mic hears the tail of Jarvis's own
sentence and starts a turn against itself, so warm link without AEC is a
feedback loop rather than a feature. Turning headset mode off disables it
rather than orphaning it.

## What this does not give you

- **It is not a wake word.** There is no hotword detection in the app at all
  (see the honest-limits section of the [README](../README.md)). With an
  earpiece you press the button; you do not say "Hey Jarvis" and have it
  answer.
- **On Android 11 and older** the only routing lever is the legacy SCO pair,
  and Jarvis holds the link only for the duration of a turn. If a conversation
  is killed abnormally the link is released on teardown; if you ever find music
  silenced system-wide, that is what a leaked SCO link looks like.
- **None of this is verified on real hardware.** The routing rules, the button
  policy and the wiring are covered by the specs above and by the instrumented
  suite, but no Bluetooth earpiece has been connected to a real phone running
  this build. See [`verification.md`](verification.md).
