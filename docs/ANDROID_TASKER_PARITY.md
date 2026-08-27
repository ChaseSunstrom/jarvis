# Android — what the phone can do, against what Tasker can do

The operator's bar for M61: *anything Tasker is capable of, Jarvis should be
able to do*. This table is the measure. Each row is one Tasker action
category or a representative action in it; the Jarvis column names the
action id in `android-app/app/src/main/kotlin/ai/jarvis/app/automation/actions/builtin/`
(or `accessibility/` for `ui_*`), its tier (direct · confirm · approve), the
Android permission it needs, and the test that proves it. The gate
(`scripts/verify/m61-android-tasker.sh`) reads this file: every id in a
**done** row must exist in the registry, and the count of done rows is the
number the milestone reports.

Status: **done** — implemented and tested · **gap** — not yet · **no** — not
possible for a third-party app on modern Android (with the reason).

## Alert

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Notify | `send_notification` | direct | POST_NOTIFICATIONS | unit | done |
| Vibrate | `vibrate` | direct | — | unit | done |
| Flash (on-screen text) | `show_toast` | direct | — | unit (`ParityActionsTest`, M61) | done |
| Say (TTS) | companion speaker (`say`) | direct | — | unit | done |
| Torch | `toggle_torch` | direct | CAMERA (flash) | unit | done |

## App

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Launch app | `launch_app` | direct | — | unit | done |
| Kill app | `kill_app` | confirm | — | unit | done |
| List apps | `list_installed_apps` | direct | QUERY_ALL_PACKAGES / manifest queries | unit | done |
| Open URL | `open_url` | direct | — | unit | done |
| Send intent | `send_intent` | confirm | — | unit (`ParityActionsTest`, M61) | done |
| App shortcut | `launch_shortcut` | direct | — | unit (`ParityActionsTest`, M61) | done |

## Audio

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Volume (ring/notification/alarm) | `set_volume` | direct | — | unit | done |
| Media volume | `set_media_volume` | direct | — | unit | done |
| Ringer mode | `set_ringer_mode` | direct | — | unit | done |
| Do not disturb | `toggle_dnd` | confirm | ACCESS_NOTIFICATION_POLICY | unit | done |
| Media control (play/pause/next/previous) | `media_control` | direct | notification listener | unit (`ParityActionsTest`, M61) | done |
| Now playing | `media_now_playing` | direct | notification listener | unit (`ParityActionsTest`, M61) | done |
| Record audio | `record_audio` | approve | RECORD_AUDIO | unit (`ParityActionsTest`, M61) | done |

## Display

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Brightness | `set_brightness` | direct | WRITE_SETTINGS | unit | done |
| Screen on / off | `screen_on` / `screen_off` (accessibility) | direct | accessibility | unit | done |
| Auto-brightness | `set_auto_brightness` | direct | WRITE_SETTINGS | unit (`ParityActionsTest`, M61) | done |
| Rotation lock | `set_rotation_lock` | direct | WRITE_SETTINGS | unit (`ParityActionsTest`, M61) | done |
| Screen timeout | `set_screen_timeout` | direct | WRITE_SETTINGS | unit (`ParityActionsTest`, M61) | done |
| Lock screen | `ui_global_action` with action: lock_screen (the accessibility agent's global action) | confirm | accessibility | unit (`ParityActionsTest`, M61) | done |
| Screenshot | `take_screenshot` (accessibility, API 30+) | confirm | accessibility | unit (`ParityActionsTest`, M61) | done |
| Wallpaper | `set_wallpaper` | confirm | SET_WALLPAPER | unit (`ParityActionsTest`, M61) | done |

## Input and screen

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Click / long click | `ui_click` | confirm | accessibility | unit | done |
| Type | `ui_type` | confirm | accessibility | unit | done |
| Back / Home / Recents | `ui_back` / `ui_home` / `ui_open_recents` | direct | accessibility | unit | done |
| Scroll / swipe | `ui_scroll` / `ui_swipe` | direct | accessibility | unit | done |
| Read screen | `ui_read_screen` | direct (untrusted output) | accessibility | unit | done |
| Wait for | `ui_wait_for` | direct | accessibility | unit | done |
| Global action | `ui_global_action` | confirm | accessibility | unit | done |
| Foreground app | `app_foreground` | direct | accessibility | unit | done |
| Dpad / keyboard keys | `ui_key` | confirm | accessibility | — | no |

## Location

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Get location | `get_location` | direct | ACCESS_FINE_LOCATION | unit | done |
| Navigate | `start_navigation` | direct | — | unit | done |
| Geofence trigger | triggers/LocationTrigger | — | ACCESS_BACKGROUND_LOCATION | unit | done |

## Media

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Take photo | `take_photo` | approve | CAMERA | unit (`CameraPhoneNfcActionsTest`, M61) | done |
| Scan barcode/QR | `scan_code` — through the scanner app that answers the ZXing SCAN intent (Binary Eye, QR Scanner); Jarvis bundles no decoder, and answers unsupported, naming an app, when none is installed | direct (untrusted output) | CAMERA (the scanner app's, not Jarvis's) | unit (`CameraPhoneNfcActionsTest`, M61) | done |
| Play file / music | `play_media` | direct | — | unit (`ParityActionsTest`, M61) | done |

## Net

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| HTTP request | `http_request` | confirm | INTERNET | unit | done |
| Wi-Fi on/off | `open_settings_panel` (Wi-Fi) — a toggle is not possible for third-party apps on Android 10+ | direct | — | unit | done |
| Bluetooth on/off | `set_bluetooth` (API ≤32 direct; 33+ via panel) | confirm | BLUETOOTH_CONNECT | unit (`ParityActionsTest`, M61) | done |
| Hotspot | `open_settings_panel` (hotspot) — not settable by third-party apps | direct | — | unit | no |
| Airplane mode | `open_settings_panel` (airplane) — not settable | direct | — | unit | no |
| Network info (SSID, IP, signal) | `get_network_info` | direct | ACCESS_FINE_LOCATION (SSID) | unit (`ParityActionsTest`, M61) | done |

## Phone

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Dial | `dial` | direct | — | unit | done |
| Place call | `place_call` | approve | CALL_PHONE | unit | done |
| Send SMS | `send_sms` | approve | SEND_SMS | unit | done |
| Read SMS | `read_sms` | approve (untrusted output) | READ_SMS | unit (`CameraPhoneNfcActionsTest`, M61) | done |
| Read contacts | `read_contacts` | confirm (untrusted output) | READ_CONTACTS | unit | done |
| Call log | `read_call_log` | approve (untrusted output) | READ_CALL_LOG | unit (`CameraPhoneNfcActionsTest`, M61) | done |
| End call | `end_call` | approve — hanging up is done to a person, so it confirms every time, like `dial` | ANSWER_PHONE_CALLS | unit (`CameraPhoneNfcActionsTest`, M61) | done |

## Settings and system

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Open settings panel | `open_settings_panel` | direct | — | unit | done |
| Device state (battery, charging, screen, network) | `get_device_state` | direct | — | unit | done |
| Sensors (light, steps, etc.) | `get_sensors` | direct | ACTIVITY_RECOGNITION (steps) | unit | done |
| Run shell | `run_shell` | approve | — | unit | done |
| Reboot / power off | — | — | — | — | no (root only) |
| Clipboard get / set | `read_clipboard` / `write_clipboard` | direct / confirm | — (foreground for read on 10+) | unit | done |
| Share | `share_text` | direct | — | unit | done |
| NFC tag read / write | `nfc_read` / `nfc_write` — reader mode on a one-frame Activity, one tag or a bounded wait | confirm (read: untrusted output) | NFC (normal) | unit (`CameraPhoneNfcActionsTest`, M61) | done |

## Files

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Read / write / list / delete | `read_file` / `write_file` / `list_files` / `delete_file` | confirm; delete approve | scoped storage | unit | done |

## Time, calendar, reminders

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Alarm | `set_alarm` | direct | SET_ALARM | unit | done |
| Timer | `set_timer` | direct | SET_ALARM | unit | done |
| Reminders | `set_reminder` / `list_reminders` / `cancel_reminder` | direct | — | unit | done |
| Calendar read / create | `read_calendar` / `create_calendar_event` | confirm | READ/WRITE_CALENDAR | unit | done |

## Tasker itself: profiles, variables, flow

| Tasker | Jarvis | test | status |
|---|---|---|---|
| Profiles (time, location, app, notification, system event, boot, manual) | `automation/triggers/*` | unit | done |
| Conditions | `tasks/Conditions.kt`, `DeviceConditionProbe.kt` | unit | done |
| Variables and substitution | `tasks/VariableSubstitution.kt` | unit | done |
| Tasks (sequences), run from the hub or the phone | `tasks/TaskEngine.kt` | unit | done |
| If / else / loops / wait | `tasks/TaskRunner.kt` — if, wait, and a repeat step (by count, or while a condition holds) bounded by `TaskLimits` | mirror (`task_repeat_test.py`, M61) | done |
| Ask the user mid-task | `tasks/AskJarvis.kt`, `CompanionAskActivity` | unit | done |
| Policy: tiers, kill switch, audit | `policy/*`, `audit/*` | unit | done |
| A task the house writes (Tasker's import) | `builtin/TaskActions.kt` — `import_tasks` (tier 3, the consent screen once) hands a bundle to `TaskStore.import`, screened like any document; `list_tasks`; the house's side is the `phone-tasks` skill | unit (`TaskActionsTest`), mirror (`phone_tasks_test.py`), M98 gate's fake phone on the house | done (M98) |

M61's work list — media control and now-playing (the most-asked), screenshot
and lock screen (accessibility, no new permission), send_intent and
launch_shortcut, take_photo and scan_code, network info, Bluetooth, read_sms
and call log, NFC, the display settings, show_toast, loops — is done, in that
order. Each landed with its tier in the local table, its permission asked
through the PermissionGateway, a unit test, and its row here flipped to
**done**. No row is **gap**. `ui_key` is the one **no**: an accessibility
service cannot inject key events; Tasker does it with root or ADB, and Jarvis
does not.

## What the last six cost, and what only a handset can prove

The six that waited for a permission or a camera closed with three new
permissions — `READ_SMS`, `READ_CALL_LOG`, `ANSWER_PHONE_CALLS`, each Tier 3
and each asked for at the moment its action runs, after the consent prompt —
the normal `NFC` permission, two more one-frame Activities, and no new
dependency:

- `take_photo` is a headless Camera2 still (`CameraActions.kt`): open the
  lens, let the exposure settle on a few small frames, one JPEG under
  `jarvis_files`, close — each step on its own clock. No CameraX, no preview;
  `docs/TOOLING_DECISIONS.md` records why.
- `scan_code` bundles no decoder. ZXing is not in this host's build cache and
  a QR decoder written on a machine with no camera would be the one part of
  the feature nobody could test, so the action hands off to the scanner app
  the settings screen already uses (Binary Eye, QR Scanner — anything that
  answers `com.google.zxing.client.android.SCAN`) through `ScanCodeActivity`
  and reports `unsupported`, naming an app to install, when there is none.
  The camera permission is the scanner's, not Jarvis's.
- `nfc_read` / `nfc_write` arm NFC reader mode on `NfcTagActivity` and wait a
  bounded time for one tag; the NDEF text and URI encodings are written out
  and unit-tested (`NdefCodec`), not left to the platform.
- `read_sms` and `read_call_log` are provider queries with a bounded
  `limit`; every result — like a scanned code and a tag's records — is marked
  untrusted, because it is somebody else's words.
- The three actions that end in an Activity run through
  `ui/ForegroundResultBridge`: a start the platform refuses (the phone in a
  pocket, the command from the hub) is an error in four seconds naming the
  cause, never a hang and never a stale notification.

A JVM proves the arithmetic (`CameraPhoneNfcActionsTest`); only a phone can
prove a photo is upright, a scanner answers, a tag round-trips or a call
ends — `docs/ANDROID_DEVICE_TESTS.md` ADT-040…046.
