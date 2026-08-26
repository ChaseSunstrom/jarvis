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
| Now playing | `media_now_playing` | direct | notification listener | unit | gap |
| Record audio | `record_audio` | approve | RECORD_AUDIO | unit | gap |

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
| Wallpaper | `set_wallpaper` | confirm | SET_WALLPAPER | unit | gap |

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
| Dpad / keyboard keys | `ui_key` | confirm | accessibility | unit | gap |

## Location

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Get location | `get_location` | direct | ACCESS_FINE_LOCATION | unit | done |
| Navigate | `start_navigation` | direct | — | unit | done |
| Geofence trigger | triggers/LocationTrigger | — | ACCESS_BACKGROUND_LOCATION | unit | done |

## Media

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Take photo | `take_photo` | approve | CAMERA | unit | gap |
| Scan barcode/QR | `scan_code` | direct | CAMERA | unit | gap |
| Play file / music | `play_media` | direct | — | unit | gap |

## Net

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| HTTP request | `http_request` | confirm | INTERNET | unit | done |
| Wi-Fi on/off | `open_settings_panel` (Wi-Fi) — a toggle is not possible for third-party apps on Android 10+ | direct | — | unit | done |
| Bluetooth on/off | `set_bluetooth` (API ≤32 direct; 33+ via panel) | confirm | BLUETOOTH_CONNECT | unit | gap |
| Hotspot | `open_settings_panel` (hotspot) — not settable by third-party apps | direct | — | unit | no |
| Airplane mode | `open_settings_panel` (airplane) — not settable | direct | — | unit | no |
| Network info (SSID, IP, signal) | `get_network_info` | direct | ACCESS_FINE_LOCATION (SSID) | unit (`ParityActionsTest`, M61) | done |

## Phone

| Tasker | Jarvis action | tier | permission | test | status |
|---|---|---|---|---|---|
| Dial | `dial` | direct | — | unit | done |
| Place call | `place_call` | approve | CALL_PHONE | unit | done |
| Send SMS | `send_sms` | approve | SEND_SMS | unit | done |
| Read SMS | `read_sms` | approve (untrusted output) | READ_SMS | unit | gap |
| Read contacts | `read_contacts` | confirm (untrusted output) | READ_CONTACTS | unit | done |
| Call log | `read_call_log` | approve | READ_CALL_LOG | unit | gap |
| End call | `end_call` | confirm | ANSWER_PHONE_CALLS | unit | gap |

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
| NFC tag read / write | `nfc_read` / `nfc_write` | confirm | NFC | unit | gap |

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
| If / else / loops / wait | `tasks/TaskEngine.kt` (if/wait exist; loops gap) | unit | gap |
| Ask the user mid-task | `tasks/AskJarvis.kt`, `CompanionAskActivity` | unit | done |
| Policy: tiers, kill switch, audit | `policy/*`, `audit/*` | unit | done |

Rows marked **gap** are M61's work list, in this order: media control and
now-playing (the most-asked), screenshot and lock screen (accessibility, no
new permission), send_intent and launch_shortcut, take_photo and scan_code,
network info, Bluetooth, read_sms and call log, NFC, the display settings,
show_toast, ui_key, loops. Each lands with its tier in the local table, its
permission asked through the PermissionGateway, a unit test, and a row here
flipped to **done**.

## What is still a gap, and why

The rows above still marked **gap** need a permission this app does not yet
request (camera, SMS, call log, NFC, `ANSWER_PHONE_CALLS`, `SET_WALLPAPER`),
a listener it does not run (now-playing needs the notification listener), or
a real handset to prove (`play_media`, `set_bluetooth` on API 33+, `ui_key`,
loops in the task engine). Each is one action in `ParityActions.kt` and one
row here; none should be written on a host that cannot compile it (this one —
CLAUDE.md — has no Android SDK), and M61 stays open until they are and
`docs/ANDROID_DEVICE_TESTS.md` ADT-039 has run.
