# Migrating from Home Assistant

Read [What this is not](../README.md#what-this-is-not) before you start. The
short version: your YAML mostly ports, your integrations mostly do not.

The honest way to decide is to open your HA `configuration.yaml` and count.
Every device that arrives over MQTT, or has an HTTP API, or is a Hue bridge, is
covered. Everything reached through a vendor integration — Z-Wave JS, HomeKit,
Matter, a cloud-tied robot vacuum, a manufacturer app — is not, and you would
be writing that yourself.

## What ports over unchanged

### Automations

Same triggers, same conditions, same action steps, same modes. Copy
`automations.yaml` across and it runs.

```yaml
- id: hallway_motion
  alias: Hallway motion light
  mode: restart
  trigger:
    - platform: state
      entity_id: binary_sensor.hall_motion
      to: "on"
  condition:
    - condition: numeric_state
      entity_id: sensor.hall_lux
      below: 20
  action:
    - service: light.turn_on
      target: {entity_id: light.hall}
    - delay: "00:02:00"
    - service: light.turn_off
      target: {entity_id: light.hall}
```

Trigger platforms: `state`, `numeric_state`, `time`, `time_pattern`, `event`,
`mqtt`, `webhook`, `template`, `jarvis_start` (aliased from
`homeassistant_start`). Conditions: `state`, `numeric_state`, `template`,
`time`, `and`/`or`/`not`, `trigger`. Actions: the full set including `choose`,
`if`/`then`/`else`, `repeat`, `wait_template`, `wait_for_trigger`, `parallel`,
`variables`, `stop` with `response_variable`. `triggers:`/`conditions:`/
`actions:` (newer HA spelling) are accepted alongside the singular forms.

### Scripts, scenes, templates, helpers

Same shapes. `script:` is a mapping with `alias`, `description`, `fields`,
`mode`, `sequence`. `scene:` is a list with `entities:`. `template:` takes the
modern list-of-blocks form with `sensor:`/`binary_sensor:`/`switch:` under it.
`input_boolean` / `input_number` / `input_select` / `input_text` /
`input_datetime` all work with the same options.

### Templates

The Jinja environment is deliberately compatible: `states()`, `is_state()`,
`state_attr()`, `is_state_attr()`, `has_value()`, `expand()`, `area_id()`,
`area_name()`, `area_entities()`, `now()`, `utcnow()`, `as_timestamp()`,
`as_datetime()`, `strptime()`, `relative_time()`, `to_json`/`from_json`, `iif`,
`slugify`, `ordinal`, the `regex_*` family, and the forgiving
`float`/`int`/`round`/`min`/`max`/`average` that take defaults. `states.light`
is iterable and `states.light.kitchen` addresses one entity. `value_json` is
present wherever a raw payload is.

### MQTT, including discovery

This is the load-bearing one. Jarvis parses Home Assistant-format discovery
messages under the same `homeassistant/` prefix, so **Zigbee2MQTT, Tasmota,
ESPHome, Shelly and Zwave-JS-UI in MQTT mode need no changes at all**. Point
them at the same broker; the devices reappear with their names, device grouping
and areas.

Hand-written `mqtt:` entity blocks (`state_topic`, `command_topic`,
`value_template`, `payload_on`, `availability_topic`, `device`) port as they
are.

If a device currently reaches HA through a native integration but *also* has an
MQTT path — many do — switching it to MQTT before you migrate is the single
highest-value piece of prep work.

### YAML loader syntax

`!secret`, `!include`, `!include_dir_named`, `!include_dir_merge_named`,
`!include_dir_list`, `!include_dir_merge_list`, and `packages:` with the same
merge semantics (lists concatenate, dicts merge, collisions error). `!env_var
NAME default` is here too.

### The client API

REST is shaped the same: `/api/states`, `/api/states/{entity_id}`,
`/api/services/{domain}/{service}`, `/api/config`, `/api/events`,
`/api/history/period`. Websocket does `auth_required` → `auth` → `auth_ok`,
then `get_states`, `subscribe_events`, `call_service`,
`config/*_registry/list`, `assist_pipeline/pipeline/list` and
`assist_pipeline/run` with the same binary audio framing.

`/api/config` reports `ha_version: "jarvis-0.1.0"`, which is enough for most
HA-aware clients to proceed. Anything that hard-codes a minimum HA version and
parses it strictly will need a nudge.

### HA-flavoured service calls

`homeassistant.turn_on`, `homeassistant.turn_off` and `homeassistant.toggle`
accept mixed targets and fan out to the right per-domain service, so pasted
config that uses the domain-agnostic form keeps working.
`homeassistant.update_entity` forces a poll. A minimal
`persistent_notification.create` / `.dismiss` pair exists.

## What does not port

### Integrations

There is no Z-Wave JS, no HomeKit controller, no Matter, no ONVIF, no Sonos, no
Spotify, no cloud anything. The integration list is: `mqtt`, `rest`,
`template`, `command_line`, `hue`, `wled`, `demo`, plus the platform pieces
(`recorder`, `history`, `logbook`, `sun`, `person`, `input_*`, `automation`,
`script`, `scene`, `voice`, `llm`).

For each HA integration you rely on, the options are:

1. Does the device speak MQTT? Use it. Best outcome by a distance.
2. Does it have a local HTTP API? A `rest:` block, no code.
3. Can you poke it from a shell? `command_line:`.
4. Otherwise, write a small Python integration — a package directory, an
   `async_setup`, one `Entity` subclass per device kind, on the order of 100
   lines. See [integrations.md](integrations.md).

### Lovelace

No dashboards, no cards, no editor, no theme system, no `ui-lovelace.yaml`. The
UI is the voice HUD in the parent repo. If dashboards are how you actually use
your house, this will feel like a downgrade, and you should say so now rather
than after moving.

### The config UI, add-ons, and the supervisor

No device wizards, no integration setup pages, no add-on store, no
Supervisor backups, no one-click updates, no HACS. Configuration is YAML plus a
restart. Updating is `docker compose pull && docker compose up -d`.

The entity, device and area registries *are* editable over the API — renames,
area assignment, aliases — so client UIs can offer that much.

### Everything HA-account-shaped

No users, no groups, no per-user permissions, no admin/non-admin, no Nabu Casa
remote access, no cloud TTS/STT fallback, no mobile app push through the cloud.
Authentication is one long-lived bearer token per device.

### Assorted

`group:`, `zone:`, `timer:`, `counter:`, `schedule:`, `alert:`, `utility_meter:`,
`statistics:`, `trend:`, `history_stats:`, `sql:`, `todo:`, `calendar:` and
`weather:` are not implemented. Some are a short template or automation away;
some are not.

Long-term statistics (the hourly/daily aggregation behind HA's energy
dashboard) does not exist. The recorder keeps raw states for
`purge_keep_days` and `history.stats` computes min/max/mean/first/last over a
window on demand.

## Suggested order

1. **Inventory.** List every device and how it reaches HA today. Sort into
   "MQTT or HTTP" and "everything else". If the second pile is most of your
   house, stop here.
2. **Move what you can to MQTT** *while still on HA*, so you can verify each
   device works before anything else changes. Zigbee2MQTT instead of ZHA is the
   big one.
3. **Stand Jarvis up alongside HA.** Different port, same broker. Both can
   consume MQTT discovery at once; nothing is exclusive. Let it run for a few
   days doing nothing.
4. **Copy `automations.yaml`, `scripts.yaml`, `scenes.yaml`** across. Fix the
   entity ids that differ — Jarvis derives entity_ids from friendly names the
   same way HA does, so most match, but check rather than assume.
5. **Port the leftovers** one at a time: `rest:` and `command_line:` blocks
   first, Python integrations only when nothing else fits.
6. **Move the clients** — HUD, phone, satellites — by changing the URL and
   pasting a new token.
7. **Turn HA off**, having kept a `/config` backup you can go back to.

Do not skip step 3. Running both for a week costs nothing and tells you which
of the things you were sure about were wrong.

## Entity ids

Jarvis derives `entity_id` from the friendly name, slugified, exactly as HA
does — "Kitchen Lights" becomes `light.kitchen_lights`. Registry entries are
keyed by `unique_id`, so a rename keeps the history.

MQTT-discovered devices keep the unique ids their publisher sends, so a
Zigbee2MQTT device that was `light.kitchen_lights` in HA is very likely
`light.kitchen_lights` here. Very likely is not certainly. Diff the two:

```bash
# on HA
curl -s $HA/api/states -H "Authorization: Bearer $HA_TOKEN" | jq -r '.[].entity_id' | sort > ha.txt
# on Jarvis
curl -s localhost:8080/api/states -H "Authorization: Bearer $TOKEN" | jq -r '.[].entity_id' | sort > jarvis.txt
diff ha.txt jarvis.txt
```

That diff, run at step 3, is the most useful ten seconds in this whole document.

## Known differences to watch for

**No `sun` trigger platform.** Watch the entity instead:
`{platform: state, entity_id: sun.sun, to: below_horizon}`.

**MQTT triggers attach before the MQTT client exists.** An `mqtt` trigger
present at boot logs "mqtt trigger … is inert" and never fires. Call
`automation.reload` once after startup and it attaches to a live client. MQTT
entities and discovery are unaffected.

**`input_*` keys log a startup warning.** `No integration named
'input_boolean'` is cosmetic; the helpers are created normally by the
always-on `automation` integration.

**Templates and whitespace.** A folded block (`>-`) starting with `{% set %}`
leaves leading spaces, and a numeric state with spaces around it stops being
numeric. Use `{%- … -%}`. HA is more forgiving here than Jarvis is.

**Service responses are more available.** `history.get`, `history.stats`,
`logbook.get`, `voice.say`, `scene.apply` and any script ending in `stop:` with
a `response_variable:` return data to the caller. A script that carries a
`description:` is offered to the model as `script_<name>` with its `fields:`
as arguments and that response as its result, which is worth exploiting
rather than working around — it is the cheapest way to give the assistant a
verb it does not have.
