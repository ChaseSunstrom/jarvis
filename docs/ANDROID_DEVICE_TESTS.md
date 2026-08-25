# What still needs a phone

Everything in `android-app/` that can be proved without hardware now is: the
app builds (`./gradlew assembleDebug`), 178 JVM unit tests pass, lint is
blocking and clean, six screens are captured as Robolectric screenshots and
compared against goldens, and thirty-odd Python mirrors read the Kotlin and
assert the logic it is supposed to implement.

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
| ADT-026 | Screens | Compare the six screenshot goldens with the same screens on a real device; note any difference the JVM renderer hid | Robolectric's Canvas is not a GPU; the goldens prove structure, not fidelity | M08 |
