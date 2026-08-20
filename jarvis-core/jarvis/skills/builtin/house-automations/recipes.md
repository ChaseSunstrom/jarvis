# Routines worth having

Two kinds of thing live here, and the difference is the whole point of the
file.

**A script** is a named sequence *you* run — "good night", "leaving",
"movie". It has no trigger. It exists so a thing that would be six service
calls becomes one, every time, in the same order, without anybody having to
reason it out again.

**An automation** has a trigger and runs itself. It exists so nobody has to
remember.

If somebody is asking for something to happen *when they say so*, that is a
script. *When something else happens*, that is an automation. Getting this
backwards produces an automation with no trigger, which never runs, or a
script somebody has to remember to call, which nobody does.

There are working versions of most of these in
`config/examples/house/automations.yaml` and `scripts.yaml`.

---

## Scripts — the ones that make repeated work one call

### `goodnight`

Lights off, blinds closed, doors locked, speaker down. The canonical routine
and the one worth building first, because everybody does it every day.

```yaml
goodnight:
  alias: Good night
  description: >-
    Shut the house down for the night: lights off, blinds closed, doors
    locked. Use when the user says they are going to bed.
  mode: single
  fields:
    delay_minutes:
      description: Wait this many minutes first, for a slow walk upstairs.
      example: 5
      default: 0
  sequence:
    - delay: "{{ (delay_minutes | default(0) | float(0)) * 60 }}"
    - parallel:
        - service: light.turn_off
          target: {entity_id: all}
        - service: cover.close_cover
          target: {entity_id: cover.living_room_window}
    - service: lock.lock
      target: {entity_id: lock.front_door}
```

**The trap** `mode: single` and a delay. Called twice, the second call is
dropped rather than queued — which is right here: two goodnights is one
goodnight. Use `mode: restart` only where a second call should replace the
first, like a motion timer.

### `leaving` and `arriving`

The pair. Worth having both even if only one gets used, because "did I leave
the heating on" is answered by the script existing.

Leaving: heating to setback, lights off, everything locked, alarm armed.
Arriving: heating to comfort, hall light on if it is dark, alarm off.

**The trap** Arriving should check whether it is dark rather than assume —
watch `sun.sun` for `below_horizon`, not the clock, which is wrong twice a
year and wrong all year at high latitudes.

### `announce`

Say something out loud. Not glamorous, and it is the one every other routine
ends up calling.

```yaml
announce:
  alias: Announce
  description: >-
    Say something out loud in the house. Use for what the user should hear
    now; prefer a notification when they are out or asleep.
  mode: queued
  max: 10
  fields:
    message:
      description: What to say, in full sentences.
      required: true
  sequence:
    - service: tts.speak
      data: {message: "{{ message }}"}
      continue_on_error: true
    - service: notify.notify
      data: {message: "{{ message }}"}
```

**The trap** `mode: queued`, so two announcements do not talk over each
other, and `continue_on_error` on the speech, so a dead TTS still leaves the
notification. Without the second, an unreachable speaker means nobody hears
anything by any route.

### `house_status`

Ends in `stop:` with a `response_variable:`, so it hands structured data back
to whoever called it — including the model. This is how a question that would
be eight `get_state` calls becomes one.

**The trap** Returning the whole house. Return the six things somebody
actually asks about; a script that answers with forty entities has moved the
problem rather than solved it.

---

## Automations — the ones that mean nobody has to remember

### Motion light with a timer

```yaml
- id: motion_light_hall
  alias: Hall motion light
  mode: restart          # <- the whole recipe is this line
  trigger:
    - platform: state
      entity_id: binary_sensor.hall_motion
      to: "on"
  condition:
    - condition: state
      entity_id: sun.sun
      state: below_horizon
  action:
    - service: light.turn_on
      target: {entity_id: light.hall}
    - wait_for_trigger:
        - platform: state
          entity_id: binary_sensor.hall_motion
          to: "off"
          for: "00:02:00"
    - service: light.turn_off
      target: {entity_id: light.hall}
```

**The trap** `mode: restart`. It is what makes the timer *extend* on new
movement rather than stack up runs that each turn the light off two minutes
after their own start. With `single`, standing in the hall for five minutes
puts you in the dark.

### Something left on

A door open too long, a freezer above temperature, a tap running. One shape:

```yaml
  trigger:
    - platform: state
      entity_id: binary_sensor.back_door
      to: "on"
      for: "00:10:00"
```

**The trap** Re-notifying every ten minutes forever. Notify once, and add a
second automation that notifies when it closes — "the back door has been open
ten minutes" followed by silence is worse than followed by "and it is shut
now".

### Away, and something moved

```yaml
  trigger:
    - platform: state
      entity_id: binary_sensor.front_door
      to: "on"
  condition:
    - condition: state
      entity_id: person.you
      state: not_home
```

**The trap** The condition is the whole automation. Without it this fires
every time anybody comes home, and the notification becomes noise inside a
week.

### An appliance finished

Watch the power draw, not a timer.

```yaml
  trigger:
    - platform: numeric_state
      entity_id: sensor.washing_machine_power
      below: 5
      for: "00:03:00"
```

**The trap** The `for:`. A washing machine drops below five watts several
times mid-cycle; three minutes below is finished, one second below is a pause
between spins.

### Evening wind-down

Dim things as the sun goes down.

**The trap** `sun` is not a trigger platform here — watch the `sun.sun`
entity going `below_horizon`. An automation with `platform: sun` loads and
never fires.

### A guest-mode flag that expires

An `input_boolean` somebody switches on, plus an automation that switches it
off overnight. This is the pattern for every temporary override, and the
expiry is the part people forget — a guest mode that stays on forever is a
security setting somebody turned off in March.

### Say something when the house starts

```yaml
  trigger:
    - platform: jarvis_start
```

**The trap** Nothing, but it is the automation that tells you the others
survived a restart, which is worth one line.

---

## Three rules for all of them

**Prefer a script or scene that already exists.** If `script.goodnight` is
there, run it — do not compose six service calls that do nearly the same
thing. The script is what somebody tuned; your composition is a guess that
looks identical until the day it is not.

Every script in this file carries a `description:`, which is what makes it a
tool of its own — `script_goodnight`, `script_announce` — with its `fields:`
as arguments. That is not decoration: a script without a description is a
routine somebody has to remember to ask for, and a script with one is a verb
the assistant has.

**Offer to save a routine the second time.** If the same sequence has been
asked for twice, say so and offer to make it a script. That is the whole
"consistent and fast" argument: one call, same order, every time, and a name
the household can say.

**Say what will happen before it does.** An automation is a program that runs
in somebody's home while they are asleep. Read back the trigger, the
condition and the action in one sentence before it is created — and
`check_automation` first, which is free.
