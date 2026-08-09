# Sensors, and Jarvis talking about them

Two integrations. `sensors` gets readings *in* from anything that can make an
HTTP request or publish MQTT. `narrate` turns the interesting ones into
sentences and delivers them to whichever device you are actually at.

The target is a house where adding a sensor is a hardware job, not a software
one. Stick an ESP32 on the front door, point it at Jarvis, and you get

> Motion detected at the front door

without writing a line of per-sensor configuration.

| | |
|---|---|
| [`sensors`](#sensors) | three ways in, auto-registration, `expire_after` |
| [inference](#inference) | how an id and a payload become a typed entity |
| [`narrate`](#narrate) | rules, generated sentences, history |
| [anti-firehose](#the-anti-firehose-guarantees) | why a flapping sensor cannot bury you |

---

## sensors

### Way 1: MQTT discovery (nothing to configure)

If the device speaks the Home Assistant MQTT discovery protocol — Zigbee2MQTT,
Tasmota, ESPHome, Shelly all do — the `mqtt` integration already creates the
entity, with its device class, unit and device grouping, the moment the config
topic is published. There is nothing for `sensors` to do and nothing for you to
write. This is the best path when the hardware supports it.

### Way 2: HTTP POST, with auto-registration

For homemade firmware, a script, a Raspberry Pi, a shell one-liner:

```
POST /api/sensor/<sensor_id>
Authorization: Bearer <token>

{"state": true}
```

The body may be `{"state": ...}`, `{"value": ...}`, a bare value (`21.5`,
`true`, `"open"`), or a single-key object naming the reading after the sensor
(`{"temperature": 21.5}` to `/api/sensor/garage_temp`).

An id nobody has ever posted before **auto-registers**: the entity is created
there and then, typed by [inference](#inference), and it exists from that
moment on. No server-side config, no restart.

An ESP32 that does this needs about four lines:

```bash
curl -X POST https://jarvis.lan/api/sensor/front_door_motion \
     -H "Authorization: Bearer $JARVIS_SENSOR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"state": true}'
```

```
{"ok": true, "status": 201, "sensor_id": "front_door_motion",
 "entity_id": "binary_sensor.front_door_motion", "state": "on",
 "created": true, "domain": "binary_sensor", "device_class": "motion",
 "area_id": "front_door"}
```

A temperature probe is the same shape:

```bash
curl -X POST https://jarvis.lan/api/sensor/garage_temp \
     -H "Authorization: Bearer $JARVIS_SENSOR_TOKEN" \
     -d '21.5'
```

Extra fields ride along as attributes, and a few are read as hints:

```bash
curl -X POST https://jarvis.lan/api/sensor/esp32_a1b2 \
     -H "Authorization: Bearer $JARVIS_SENSOR_TOKEN" \
     -d '{"state": 19.5,
          "name": "Wine Cellar Temperature",
          "device_class": "temperature",
          "unit": "°C",
          "area": "Cellar",
          "attributes": {"rssi": -63, "firmware": "1.4.2"}}'
```

Hints: `name`, `domain`, `device_class`, `unit` / `unit_of_measurement`,
`area` / `area_id`, `icon`. Everything else under `attributes` is stored as an
attribute.

#### Authentication

Every post needs a credential. Any one of these opens the door:

* a long-lived bearer token (the same one the app and the web HUD use);
* the shared `sensors.token` from `configuration.yaml` — give this to
  microcontrollers instead of the master token;
* that one sensor's own `token:`, which opens only that sensor.

There is no unauthenticated path. With nothing configured, everything is
refused — the failure mode is a locked door, not an open one.

#### Wiring the route

`sensors` owns no HTTP routes; the API layer does. This integration publishes
its handler at `jarvis.data["sensor_ingest"]`:

```python
ingest = jarvis.data["sensor_ingest"]          # callable + .path + .methods
result = await ingest(sensor_id, payload, token)
# -> {"ok": bool, "status": <http status>, ...}
```

so mounting it is three lines wherever `/api` routes are declared:

```python
@api_router.post("/sensor/{sensor_id}")
async def sensor_post(request, sensor_id):
    ingest = get_jarvis(request).data.get("sensor_ingest")
    result = await ingest(sensor_id, await request.json(), request.headers.get("authorization"))
    return JSONResponse(result, status_code=result["status"])
```

**Until that route exists**, the same handler is reachable through the webhook
door that is already open:

```bash
curl -X POST "https://jarvis.lan/api/webhook/sensor?sensor_id=front_door_motion" \
     -H "Authorization: Bearer $JARVIS_SENSOR_TOKEN" \
     -d '{"state": true}'
```

The webhook route itself is unauthenticated by design (its id is the secret),
so the credential check happens inside the handler either way: a post without a
valid token writes nothing and reports `{"delivered": 0}`.

### Way 3: YAML, for sensors you want named and typed up front

```yaml
sensors:
  token: !secret sensor_ingest_token   # shared ingest token
  allow_auto_register: true            # default; false locks the set
  expire_after: 0                      # default for every sensor, seconds
  max_sensors: 500                     # cap on auto-registration
  sensors:
    - id: front_door_motion
      name: Front Door Motion
      domain: binary_sensor
      device_class: motion
      area: Front Porch
      narrate: "Motion detected at the front door"
      expire_after: 120
      token: !secret front_door_token
```

A bare list is accepted as shorthand when you need no options:

```yaml
sensors:
  - id: front_door_motion
    device_class: motion
  - id: garage_temp
```

`area:` here *creates* the area if it does not exist — you asked for it by
name. Inference never creates areas; it only matches ones you already have.

`narrate:` attaches a sentence to the sensor itself, which is enough to make it
speak with no `narrate:` rule at all. Use `narrate: true` to opt in and let the
sentence be generated. On a binary sensor it fires on the *on* edge — "Motion
detected at the front door" is a sentence about motion starting — and
`narrate_on: off` moves it to the other edge.

### expire_after

A sensor that stops reporting should say so rather than keep showing yesterday's
number. With `expire_after: 120`, a sensor silent for two minutes goes
`unavailable`, and comes straight back on its next post.

The sweep runs every `expire_check_interval` seconds (default 10; set it to `0`
to turn the background loop off) and `sensors.check_expired` runs it on demand.

### Services

| Service | What it does |
|---|---|
| `sensors.list` | every sensor: entity, state, freshness, and how it was typed |
| `sensors.set` | push a reading from an automation or script (auto-registers) |
| `sensors.forget` | drop a sensor so a changed device can register again |
| `sensors.check_expired` | run the staleness sweep now |

```yaml
automation:
  - alias: Publish the boiler pressure
    trigger: {platform: time_pattern, minutes: "/5"}
    action:
      - service: sensors.set
        data: {sensor_id: boiler_pressure, state: "{{ states('sensor.raw_boiler') }}"}
```

---

## Inference

`jarvis/integrations/sensors/infer.py` is pure: an id, a payload and the list of
areas go in; a domain, device class, unit, friendly name and area come out. No
clock, no network, no model. It is exhaustively tested in `tests/test_sensors.py`.

Two rules keep the guessing honest.

**The payload picks the domain; the name picks the class.** A boolean is a
`binary_sensor` whatever it is called. A number is a `sensor`. Only `0`/`1` is
genuinely ambiguous, and there the name breaks the tie — `hall_pir` posting `1`
is motion, `nas_cpu` posting `1` is a percentage.

**The domain is only ever `sensor` or `binary_sensor`.** A payload may hint a
domain, and a hint of `light`, `lock` or `switch` is ignored. Inference must
never be a way for a device to mint something the assistant can then go and
*operate*.

### The table

Matched against the **longest trailing fragment** of the id, so `garage_door`
beats `door`, and `shed_battery_level` beats `level`.

| Name ends in | Boolean payload | Numeric payload |
|---|---|---|
| `motion`, `pir`, `movement` | `binary_sensor` / motion | — |
| `occupancy`, `occupied` | occupancy | — |
| `presence` | presence | — |
| `door` | door | — |
| `garage_door` | garage_door | — |
| `window` | window | — |
| `opening`, `contact` | opening | — |
| `smoke` | smoke | — |
| `co`, `carbon_monoxide` | carbon_monoxide | — |
| `leak`, `water_leak`, `flood`, `damp` | moisture | — |
| `vibration` | vibration | — |
| `tamper` | tamper | — |
| `lock` | lock | — |
| `plug` | plug | — |
| `running` | running | — |
| `safety` | safety | — |
| `cold` / `heat` | cold / heat | — |
| `connectivity`, `online`, `link` | connectivity | — |
| `problem`, `fault`, `error` | problem | — |
| `charging`, `battery_charging` | battery_charging | — |
| `battery`, `battery_level` | battery (low) | battery, `%` |
| `gas` | gas (alarm) | gas, `m³` |
| `moisture`, `wet` | moisture | moisture, `%` |
| `water` | moisture | water, `L` |
| `rain` | moisture | precipitation, `mm` |
| `sound`, `noise` | sound | sound_pressure, `dB` |
| `power` | power | power, `W` |
| `light` | light | illuminance, `lx` |
| `temp`, `temperature`, `dew_point` | — | temperature, `°C` |
| `humidity`, `humid`, `hum`, `rh` | — | humidity, `%` |
| `soil`, `soil_moisture` | — | moisture, `%` |
| `illuminance`, `lux`, `light_level`, `brightness` | — | illuminance, `lx` |
| `pressure`, `baro`, `barometer` | — | pressure, `hPa` |
| `co2`, `carbon_dioxide` | — | carbon_dioxide, `ppm` |
| `pm1` / `pm25` (or `pm2_5`) / `pm10` | — | pm1 / pm25 / pm10, `µg/m³` |
| `voc`, `tvoc` | — | volatile_organic_compounds, `ppb` |
| `ozone` / `no2` | — | ozone / nitrogen_dioxide, `µg/m³` |
| `aqi`, `air_quality` | — | aqi |
| `energy`, `kwh`, `consumption` | — | energy, `kWh` |
| `current` / `voltage` / `frequency` | — | current `A` / voltage `V` / frequency `Hz` |
| `signal`, `rssi`, `signal_strength` | — | signal_strength, `dBm` |
| `distance` | — | distance, `cm` |
| `speed` / `wind_speed` | — | speed / wind_speed, `km/h` |
| `weight`, `mass` | — | weight, `kg` |
| `uptime`, `duration` | — | duration, `s` |
| `ph` | — | ph |
| `cpu`, `ram`, `memory`, `disk`, `level`, `percent` | — | (no class), `%` |
| anything else | `binary_sensor`, no class | `sensor`, no class |

A payload that is neither — `"full"`, `"idle"` — makes a `sensor` with a text
state and no unit.

### Names

`front_door_motion` becomes **Front Door Motion**. Words are title-cased,
except the ones that read badly that way:

| | | | |
|---|---|---|---|
| `temp` → Temperature | `humid`, `rh` → Humidity | `batt` → Battery | `pir` → Motion |
| `co2` → CO2 | `pm25` → PM2.5 | `tvoc` → TVOC | `aqi` → AQI |
| `rssi` → RSSI | `ph` → pH | `cpu` → CPU | `uv` → UV |

### Areas

Matched against the area registry by **longest leading run** of the id, then by
trailing run, using area names, ids and aliases. Exact matches only.

* `front_door_motion` + an area "Front Door" → that area.
* `front_door_motion` + only an area "Front Porch" → **no area**. A sensor put
  in the wrong room is worse than a sensor in no room, so inference declines to
  guess. Say `area: Front Porch` in YAML, or set it in the entity registry.
* `living_room_temp` + an area "Lounge" aliased "living room" → Lounge.

### Once typed, a sensor stays typed

The first payload decides the domain and the entity id; later posts only set
the value. If a device changes what it reports, `sensors.forget` it and let it
register again.

---

## narrate

```yaml
narrate:
  enabled: true
  quiet_hours: ["23:00", "07:00"]   # default for every rule
  min_interval: 300                 # default debounce, per rule per entity
  max_per_hour: 20                  # global ceiling
  max_burst: 5                      # global ceiling within burst_window
  burst_window: 60
  history: 200
  rules:
    - entities: [binary_sensor.front_door_motion]
      on_state: "on"
      message: "Motion detected at the front door"
      importance: normal
      quiet_hours: ["23:00", "07:00"]
      min_interval: 300
    - device_class: door
      on_state: "on"
```

Delivery goes through `companion.notify`, so presence routing decides speak vs
notify vs queue — narration is never a second, private notification path.

### Rules

A rule selects entities by any combination of `entities`, `domains`,
`device_class` and `areas`; a rule that selects nothing at all is dropped with a
warning rather than matching the whole house. The first matching rule wins.

| Field | Meaning |
|---|---|
| `on_state` | only narrate when the new state is this (string or list) |
| `from_state` | and the old state was this |
| `message` | the sentence; omit it and one is generated |
| `importance` | `low`, `normal`, `high`, `critical` — passed to `companion.notify` |
| `min_interval` | seconds before this rule may speak about the same entity again |
| `max_per_hour` | ceiling for this rule across all its entities |
| `quiet_hours` | `["23:00", "07:00"]`, or `false` to opt out of the global window |
| `min_change` | for numbers: ignore a change smaller than this |
| `on_startup` | narrate the first state an entity ever has (default: no) |

A message may contain `{name}`, `{lower_name}`, `{area}`, `{state}`,
`{old_state}`, `{unit}`, `{device_class}` and `{entity_id}`.

Precedence for the sentence: the rule's `message`, then the sensor's YAML
`narrate:`, then a generated one.

### Generated sentences

No model call — the common cases are a table, so a doorbell is not a token
spend. Two shapes:

* **Place** — things that happen somewhere: motion, smoke, water, sound. The
  place is the entity's area, or its name with the class word taken off.
  `binary_sensor.front_door_motion` → *"Motion detected at the front door"*.
* **Subject** — things that happen to something: doors, batteries,
  connectivity. `binary_sensor.garage_door` → *"The garage door has opened"*.

Numbers read the unit as a word: *"Kitchen temperature is now 24 degrees"*,
*"Kitchen humidity is now 55 percent"*, *"Office CO2 is now 812 parts per
million"*. An entity going `unavailable` — which is what `expire_after` does —
gives *"The front door motion has stopped reporting"*.

### History

Every matched change is recorded whether or not it was delivered, so
"what happened while I was out?" is answerable even for the hours narration was
muted or asleep.

```yaml
service: narrate.history
data: {minutes: 240, limit: 20}
```

Repeats of the same sentence are folded into one line with a count, newest
first, so forty trips of the same doorway do not bury the one line that
mattered. Pass `collapse: false` for the raw entries.

The same view is an LLM tool, `recent_events`, so "has anything moved since I
left?" works out loud.

### Services

| Service | What it does |
|---|---|
| `narrate.history` | what was said, and what was held back |
| `narrate.mute` | silence everything, optionally `minutes: 60` |
| `narrate.unmute` | start again |
| `narrate.status` | rules, mute state, quiet hours, how much has been said |

---

## The anti-firehose guarantees

This is the feature's real failure mode. One PIR with a loose connection, or one
contact sensor on a door in a draught, and a system that narrates state changes
will say four hundred things before breakfast. Nobody tunes that — they turn the
feature off, and then they miss the message that mattered.

So delivery passes four independent ceilings, and **all of them are counted on
delivery rather than on matching**, which is what makes them hold when a rule is
misconfigured:

1. **Debounce** — the same rule, about the same entity, at most once per
   `min_interval` (default 300s).
2. **Per-rule ceiling** — `max_per_hour` on the rule, across all its entities.
3. **Burst ceiling** — at most `max_burst` narrations (default 5) in any
   `burst_window` (default 60s), across everything.
4. **Global ceiling** — at most `max_per_hour` (default 20) in any hour, across
   everything.

A sensor flapping a hundred times a second produces **at most `max_burst`**
notifications, whatever the rules say. There is a test that does exactly that.

On top of the ceilings:

* **quiet hours** suppress delivery without losing the event;
* **`narrate.mute`** silences everything, with an optional timer;
* only **`critical`** messages pass mute and quiet hours — a smoke, gas,
  carbon-monoxide or safety sensor is `critical` by default, because a smoke
  alarm swallowed by quiet hours is not a quiet house, it is a broken one;
* a **suppressed event is still recorded**, so nothing is silently lost.

---

## Security notes

* **Ingest fails closed.** No credential, no write. The bearer token, the
  shared `sensors.token` and a per-sensor `token:` are the three keys, compared
  with `hmac.compare_digest`.
* **Inference cannot mint an actuator.** Only `sensor` and `binary_sensor` come
  out, whatever the payload asks for.
* **Ids are validated before they are used.** An id posted over HTTP must
  already be `[a-z0-9_]` (optionally `domain.object_id`); it is not quietly
  slugified into something else.
* **Auto-registration is capped** (`max_sensors`, default 500) and can be turned
  off, so a broken or hostile poster cannot fill the entity registry.
* **Device-supplied text is data.** Sensor names arrive from firmware and end up
  in a notification and in `recent_events` output. They are stripped of control
  characters, collapsed, capped, and anything shaped like a fence marker is
  neutralised. Attribute bags are bounded in count and size.
* Narration is delivery only. It reaches `companion.notify` and nothing else —
  there is no path from a sensor payload to an action dispatcher.

See [`security.md`](security.md) for the whole model.
