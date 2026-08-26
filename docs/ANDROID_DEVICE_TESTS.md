# What still needs a phone

Everything in `android-app/` that can be proved without hardware now is: the
app builds (`./gradlew assembleDebug`), the JVM unit tests pass, lint is
blocking and clean, the screens are captured as Robolectric screenshots and
compared against goldens, and 63 Python mirrors read the Kotlin and assert
the logic it is supposed to implement — on a machine with the SDK; this host
runs only the mirrors (BLOCKERS §3).

This file is the rest — the checks that cannot be made on a build machine, each
one written so somebody holding a phone can do it without reading the source
first. They are **not** a backlog of work to do here; they are the boundary of
what a host with no device can honestly claim, and `docs/verification.md` marks
every one of them **Unproven** for exactly this reason.

## How to run one

```bash
bash android-app/tools/bootstrap-toolchain.sh     # JDK + SDK under $HOME
cd android-app && ./gradlew installDebug          # a phone on USB, developer mode on
```

Then follow the check. Record the result in the PR, or file what happened.

## The list

| ID | Area | Check | Why device-only | Milestone |
|---|---|---|---|---|
| ADT-001 | Assist role | Set Jarvis as the device assistant, long-press home, and confirm the assist surface opens over the current app | The assist role is granted by the system's own picker; no API can claim it | M08 |
| ADT-002 | Assist role | Do the same after a GrapheneOS factory reset with no Google services present | GrapheneOS resolves the role differently and has no Play services fallback | M08 |
| ADT-003 | Lock screen | Raise a Tier-3 approval while the phone is locked; confirm the prompt appears and that APPROVE is inert until the device is unlocked | `KeyguardManager` behaviour cannot be simulated; the inert-until-unlocked path is the security claim | M11 |
| ADT-004 | Approvals | Raise a Tier-3 approval while a third-party app is in the foreground; confirm it appears over that app | Background-activity-start rules differ per OEM and per Android version | M11 |
| ADT-005 | Approvals | Let an approval expire untouched; confirm the countdown is announced and the action is refused | The auto-deny clock runs in the foreground service; a screenshot cannot show time passing | M11 |
| ADT-006 | Wake word | Say "Hey Jarvis" with the phone in a pocket, screen off, for an hour; confirm detections and battery cost | Microphone capture, Doze and the foreground-service budget are all device state | M04 |
| ADT-007 | Wake word | Confirm the QS tile toggles listening and that the tile's subtitle follows | `TileService` only runs inside the system's quick-settings host | M04 |
| ADT-008 | Audio | Play music, then start a turn; confirm Jarvis takes transient focus and the music ducks and returns | Audio focus is a system arbitration between real players | M04 |
| ADT-009 | Audio | Take a phone call mid-turn; confirm the listener stands down and resumes when the call ends | Telephony/VoIP mode changes cannot be faked on the JVM | M04 |
| ADT-010 | Audio | Pair a Bluetooth headset; confirm capture routes to it and the media button starts a turn | Routing and media-button delivery need real hardware | M04 |
| ADT-011 | Speech | Speak a sentence in a quiet room and in a noisy one; confirm on-device STT transcribes both | The recogniser is a system service backed by a device model | M04 |
| ADT-012 | Speech | Confirm TTS plays through the earpiece when the phone is at the ear and the speaker otherwise | Proximity sensor plus audio routing | M04 |
| ADT-013 | Updater | Install a newer APK through the in-app updater; confirm the system install prompt appears and the app restarts on the new version | `PackageInstaller` shows a system UI that only exists on a device | M08 |
| ADT-014 | Reminders | Set a reminder, reboot the phone, and confirm it still fires | `AlarmManager` across a real restart | M12 |
| ADT-015 | Accessibility | Turn TalkBack on and drive a whole turn: wake, listen, tool call, answer | Screen-reader output is the thing under test | M08 |
| ADT-016 | Accessibility | Enable the accessibility service and confirm automation can read and act on a third-party app | The service is granted in Settings and reads other apps' trees | M22 |
| ADT-017 | Permissions | Walk the first-run flow and grant, then refuse, each of the 16 dangerous permissions; confirm every refusal degrades rather than crashes | Runtime permission dialogs are system UI | M08 |
| ADT-018 | Notifications | Grant notification access; confirm a task-done notification arrives and opens the task | Notification listener access is a Settings toggle | M17 |
| ADT-019 | Location | Configure a `home` geofence; confirm the wake-listen gate opens on arrival and closes on leaving | Geofencing needs real GPS transitions | M04 |
| ADT-020 | Pairing | Pair the phone with jarvis-core by QR code over the tailnet | A camera and a second device | M06 |
| ADT-021 | Voice identity | Enrol a voice, then confirm a different speaker is refused | Enrolment records real speech | M16 |
| ADT-022 | Android Auto | Project onto a head unit and confirm the driving surface appears with a usable tap target | Requires a car or the DHU | M08 |
| ADT-023 | Boot | Confirm the boot animation runs on a cold start and is skipped when animations are disabled system-wide | The animation scale is a system setting read at runtime | M08 |
| ADT-024 | Insets | Confirm the layout on a device with a display cutout and gesture navigation, at API 29 and at API 35 | Cutouts and gesture insets are hardware and version specific | M08 |
| ADT-025 | Full-screen intent | With the screen on and unlocked, confirm a Tier-3 prompt degrades to a heads-up notification rather than silently doing nothing | The degradation is enforced by the system, not the app | M11 |
| ADT-027 | PHONE_AUTOMATION | Build with `-PphoneAutomation=true`, enable the accessibility service in Settings, and confirm Jarvis can read a third-party app's screen | The service only receives events from real apps on a real device | M22 |
| ADT-028 | PHONE_AUTOMATION | With that build, confirm an injected tap lands on the control it was aimed at in three different apps | Node geometry and focus behaviour differ per app and per OEM skin | M22 |
| ADT-029 | PHONE_AUTOMATION | With that build, turn the master switch off and confirm every phone action is refused while the service stays enabled | Two independent switches, and the point is that either one stops it | M22 |
| ADT-030 | PHONE_AUTOMATION | Confirm a notification's content never reaches jarvis-core in a default build, by watching the wire while messages arrive | Only a real phone receives real notifications | M22 |
| ADT-026 | Screens | Compare the ten screenshot goldens with the same screens on a real device; note any difference the JVM renderer hid | Robolectric's Canvas is not a GPU; the goldens prove structure, not fidelity | M08 |
| ADT-031 | Reactor | Open the app on an AMOLED panel at real brightness in a dark room and in daylight; confirm the instrument's hairlines (ticks, coil, iris arcs) are visible at both, and that the lens reads as a lens rather than a black hole | The goldens are drawn on a JVM canvas with no panel, no gamma and no ambient light; the tokens' dimmest greys were chosen against a monitor | M51 |
| ADT-032 | Reactor | Speak to the phone and watch the level arc: it must follow the voice with no visible lag and rest at zero in silence; then say nothing for a minute and confirm the idle breath is the only movement | The arc is driven by the real microphone's RMS through the real gain; the JVM tests feed it a number | M51 |
| ADT-033 | Reactor | Trigger the wake word with another app in the foreground; confirm the floating instrument draws whole (bezel to bezel, nothing clipped square) over that app, and that its blades turn at the same rate as the home screen's | The overlay window's bounds and z-order are the system's; `siri_overlay_test.py` proves the arithmetic, not the pixels | M51 |
| ADT-034 | Reactor | Watch a cold start: the instrument assembles lens → level → coil → blades, the wordmark resolves, the checks type, and the settled screen is the same picture with nothing snapping on at the handoff | Robolectric cannot play the boot's animator against a real vsync; the handoff is a timing question | M51 |
| ADT-035 | Chrome | On the console frame, confirm the accent underline sits under the current tab and moves with it; on the approval screen, that APPROVE is the one filled control and DENY is the quiet one; on the home screen, that MANAGE is the one filled control | Layout and colour are asserted on the JVM; whether the hierarchy READS at arm's length is a person's call | M51 |
| ADT-036 | Voice screen | Ask something with a two-sentence answer; confirm the first sentence is heard before the second is written and that nothing is said twice | Early speech (`tts-chunk`, then `remainder_url`) is audio timing on a real speaker; the JVM proves the queue, not the ear | M61 |
| ADT-037 | Voice screen | Turn a light on by voice with the phone unlocked; confirm the activity strip under the reactor shows the tool row live, then done, with its duration | The strip draws from bus events over the device's own socket; only a handset shows it over the launcher at the right density | M61 |
| ADT-038 | Voice screen | Press a Zigbee button twice; confirm two rows, not one | `jarvis_mqtt_event` rows carry the time in their id; the eye is the test of whether two look like two | M61 |
| ADT-039 | Goldens | Done on this host, 26 Aug: `voice-activity` and `voice-graph` recorded, looked at (the graph's labels were fixed on the first look) and committed; kept so the numbering stays stable | Nothing device-only remains in this row | M61 |
| ADT-040 | Camera | Ask for a photo (`take_photo`, back then `facing: front`) with the phone upright, then again held in landscape; confirm the Tier-3 prompt each time, that the JPEG under `jarvis_files/photos/` is sensibly exposed and upright in a viewer, and that `read_file` with `base64` returns it | The exposure settling, the sensor mounting and the JPEG encoder are the HAL's; the JVM proves the arithmetic that chooses the lens, the size and the orientation, not the picture | M61 |
| ADT-041 | Camera | Ask for a photo while another app holds the camera open, and again with the camera permission refused; confirm each fails within seconds with the sentence naming the cause ("in use by another app", "permission … not granted") and nothing hangs or crashes | `CameraDevice.StateCallback` errors and the permission dialog only happen on a device | M61 |
| ADT-042 | Scanner | With Binary Eye installed, ask `scan_code`; confirm the scanner opens over the current app, a QR's text comes back and the result carries `untrusted: true`; back out and confirm "the scan was cancelled"; uninstall the scanner and confirm the action answers `unsupported` naming an app to install | The hand-off is an Activity result from another app; there is no decoder to test on the JVM | M61 |
| ADT-043 | Messages & calls | Ask `read_sms` and `read_call_log` with the grants never given; confirm the Tier-3 prompt, then the system dialog, then the newest entries first with `box`, `from`, `type` and `since` honoured; refuse a grant and confirm the honest `permission … not granted` | The providers and their permission dialogs are the platform's | M61 |
| ADT-044 | Calls | During a call, ask Jarvis to hang up; confirm the Tier-3 prompt and that the call ends on APPROVE; with no call in progress confirm "there is no call to end" | `TelecomManager.endCall` only means anything with a live call on a real modem | M61 |
| ADT-045 | NFC | `nfc_write` a text and then a URI to a blank Type 2 sticker and `nfc_read` each back; confirm the "hold a tag" toast, the platform's tag sound, the round trip (text, language, URI), the refusal on a read-only tag and on one too small, and the timeout message with no tag presented | Reader mode and tag I/O are radio; the JVM proves the NDEF bytes, not the antenna | M61 |
| ADT-046 | Background | With the phone locked in a pocket, send `scan_code` and `nfc_read` from the hub; confirm each fails within about four seconds with "never reached the screen" and that nothing opens or lingers when the phone is unlocked | Background activity-start refusal is silent and the platform's | M61 |
