---
name: phone-tasks
description: How to put an automation on the user's phone — the task format the phone runs by itself (triggers, steps), shipped with control_device's import_tasks action.
allowed-tools: [list_my_devices, control_device]
metadata:
  owner: the household
version: "1"
---

# Phone tasks

The phone runs its own small automations — Tasker-style — without the house:
a **task** is what starts it (triggers), what has to hold (conditions) and what
it does (steps, each a phone action). The house writes one and ships it with
`control_device(action="import_tasks", params={"bundle": …})`; the phone
screens it and lists it under Settings → PHONE TASKS, where the person turns it
on or off.

Use this only when the user asked for something to happen **on their phone
when the phone notices something** — "when I plug it in at night, turn the
torch on", "when I get to the office, set it to silent". A one-off action
("turn the torch on") is a plain `control_device` call, not a task.

## What to send

```json
{"bundle": {"version": 1, "tasks": [
  {"id": "torch-on-charge", "name": "Torch on charge",
   "description": "Turn the torch on when the phone is plugged in after dark",
   "enabled": true, "mode": "SINGLE",
   "triggers": [{"type": "power_connected"}],
   "conditions": [],
   "steps": [{"type": "action", "action": "toggle_torch", "params": {"on": true}}]}
]}}
```

- `id`: stable, lower-case, hyphens — sending the same id again **replaces**
  that task; a new id adds one.
- `name`: what the person sees in the list. `description`: one sentence.
- `enabled: true` asks for it to run; the phone decides (see below).
- `mode`: `SINGLE` (a run in progress swallows a new trigger), `QUEUE`
  (runs one after another) or `PARALLEL`.

## Triggers

One or more of: `power_connected`, `power_disconnected`,
`battery_level` (`threshold` 0–100, `direction` `below`|`above`),
`connectivity_changed`, `airplane_mode`, `headset_plugged`,
`headset_unplugged`, `bluetooth_connected`, `bluetooth_disconnected`,
`screen_on`, `screen_off`, `user_present` (unlocked), `ringer_mode_changed`,
`timezone_changed`, `boot_completed`,
`time_schedule` (`at` `"HH:MM"`, `days` e.g. `["mon","tue"]`),
`interval` (`minutes`), `geofence_enter` / `geofence_exit` (`name`, `lat`,
`lon`, `radius_m`), `app_foreground` (`packages`), `notification_posted`
(`packages`), `manual` (a button in the list).

A trigger is `{"type": "screen_on"}` or, with parameters,
`{"type": "battery_level", "threshold": 15, "direction": "below"}`.

## Steps

`{"type": "action", "action": "<phone action id>", "params": {…}}` — any
action the phone listed in `list_my_devices` (`toggle_torch`, `set_volume`,
`set_ringer_mode`, `toggle_dnd`, `set_brightness`, `vibrate`, `show_toast`,
`launch_app`, `open_url`, `notify` …), with that action's own parameters.
Also: `{"type": "wait", "ms": 500}`, `{"type": "notify", "title": …, "body": …}`,
`{"type": "if", "condition": {…}, "then": […], "else": […]}`,
`{"type": "repeat", "count": 3, "steps": […]}`, `{"type": "stop"}`,
`{"type": "set_variable", "name": …, "value": …}`,
`{"type": "wait_for_event", "event": "<a trigger id>", "timeout_ms": 60000}` and
`{"type": "ask_jarvis", "question": …}` (the phone asks the house, mid-task).

## What the phone does with it

- `import_tasks` is a **tier-3** action: the phone shows the person what is
  being installed and they confirm it on the phone, once. If the reply says
  it is waiting on them, say so — do not send it again.
- A task whose steps include a tier-3 action (an SMS, a call, a shell
  command) arrives **switched off**, whatever `enabled` says; the person turns
  it on in PHONE TASKS. Tell them that when the result says `held_for_consent`.
- A step naming an action the phone does not have is refused — check
  `list_my_devices` first, and use only ids it lists.
- Tell the user, in one sentence, what the task will do and what starts it;
  never describe it as running before the result says it was imported.
