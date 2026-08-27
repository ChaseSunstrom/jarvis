# Sky, satellites and radio: what "any sensor" should mean for a house

Asked: the operator wants *"satellites"* and *"any sensor"* as capabilities, local
only — computation on this box; downloads are acceptable when they are cached
and the feature keeps working without them.

Short answers, in the order they earn their place:

1. **Satellites are a 17 MB ephemeris, a 2-hourly CSV and one Python
   package.** `skyfield` (MIT) with CelesTrak OMM data predicts every ISS,
   Starlink and weather-satellite pass for the house's coordinates offline;
   the elements go stale after about two weeks and the tool must say how old
   they are. No account, no key, no radio.
2. **"Any sensor" is already mostly built and switched off.** The `mqtt`
   integration speaks Home Assistant discovery, which is the one protocol every
   radio gateway below publishes; the `sensors` integration is the HTTP door
   for devices that cannot. What is missing is smaller than it looks: the
   `sensors:` key in the deployed config, a birth message on
   `homeassistant/status`, an allowlist for radio auto-registration, a
   statistics table the recorder does not keep, and an LLM tool over history.
3. **Three £25 dongles cover the air, the sea and the 433/868 MHz band**, one
   dongle per band — they cannot be shared. ADS-B is the most rewarding for
   the least work (`aircraft.json` is polled JSON and the `rest:` integration
   can read it today); rtl_433 is where the weather station lives; AIS only
   pays off near water.
4. **Astronomy splits cleanly**: moon, planets, twilight, meteor showers and
   satellite passes compute offline; aurora and tides need a small cached
   download, and for tides in the UK there is no legal offline route.

The rest of this document is the evidence.

---

## What is already here

Read before proposing anything, because the shape of the answer follows from it.

**Location.** `jarvis:` in `jarvis-core/config/configuration.yaml` carries
`latitude`, `longitude`, `elevation`, `time_zone` (placeholder central London,
`51.5072, -0.1276`, 11 m, `Europe/London`) and `unit_system: metric`. Every
integration below reads its position from there, the way `sun` already does.

**`sun`** (`jarvis-core/jarvis/integrations/sun/`). One `sun.sun` entity with
`next_rising / next_setting / next_dawn / next_dusk / next_noon /
next_midnight / elevation / azimuth / rising`, recomputed every
`update_interval` seconds. The maths in `solar.py` is pure functions with no
dependencies. Nothing about the moon, planets or satellites. This is the
template for a `sky` integration: same config shape, same "location from
`jarvis:`", same pure-function core.

**`mqtt`** (`jarvis-core/jarvis/integrations/mqtt/`). Broker client
(`aiomqtt`, paho fallback), YAML entities, and the Home Assistant discovery
protocol: `discovery.py` parses both the single-component topic
(`homeassistant/<component>/[<node>/]<object_id>/config`) and the device
bundle (`homeassistant/device/<id>/config` with `cmps`), expands the
abbreviation table (`abbreviations.py`), and `entity.py` implements
availability topics, `expire_after`, `json_attributes_topic`, templates and
`state_class` (kept as an attribute) for `sensor, binary_sensor, switch, fan,
siren, light, cover, climate, lock, button, number, select, text`.
`IGNORED_COMPONENTS = {device_automation, tag, event, update, scene}`. Birth
and will go to `jarvis/status`. The deployed config has `discovery: true`,
`discovery_prefix: homeassistant`, anonymous loopback broker. The mosquitto
container is behind `--profile mqtt` (loopback-only, `mem_limit: 256m`).

**`sensors`** (`jarvis-core/jarvis/integrations/sensors/`). The HTTP ingest
door (`POST /api/sensor/<id>`, reachable today as
`POST /api/webhook/sensor?sensor_id=<id>`), auto-registration with inference
of domain / device class / unit / area from the id and payload, YAML-declared
sensors, `expire_after`, per-sensor tokens, and a cap on auto-registration.
**Not configured**: there is no `sensors:` key in `configuration.yaml`, and
`async_setup_integrations` only loads integrations whose key is present, so
the door is closed on the deployed box.

**`rest`** and **`command_line`**. Poll a JSON URL or a command into
`sensor`/`binary_sensor` entities, with `value_template` and
`json_attributes`. The shipped example polls the model server. This is the
zero-code path to `aircraft.json` (below).

**`recorder` / `history` / `logbook`.** SQLite under `/config`,
`purge_keep_days: 10`, `history.get` and `history.stats` (min / max / mean /
first / last / changes over a window). No long-term statistics: after ten days
a reading is gone, so "how much rain this month" cannot be answered.

**LLM tools.** `get_state`, `list_entities`, `get_user_context`, the verbs,
and `recent_events` from `narrate`. There is no tool over `history.stats`. The
`briefing` integration already reads `weather.*` entities for its weather
section, and nothing in the tree creates one.

**Compose conventions** (`jarvis-core/docker-compose.yml`): every service
`network_mode: host`, pinned image tags, `restart: unless-stopped`,
`security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `mem_limit`,
`cpus`, a healthcheck, and optional hardware behind a profile (`mqtt`,
`kokoro`, `geocode`). Anything with a USB dongle belongs behind a profile for
the same reason mosquitto does: it must not start on a box that lacks it.

So: the ingestion path, the entity model and the discovery parser exist. The
gaps are in configuration, in history depth, and in the tools the model is
given.

---

## 1. Satellites

### Data: where the orbits come from, and how stale they may be

Every satellite tool is the same three things: an element set per satellite,
a propagator, and a search for the moments a satellite is above the house's
horizon.

**CelesTrak GP data** (free, no account). One URL serves everything:

```
https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=csv
https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=csv     # 100 brightest
https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=csv
https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=csv   # ~11,000 rows
https://celestrak.org/NORAD/elements/gp.php?GROUP=last-30-days&FORMAT=csv
https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=csv      # the ISS
```

Two facts that shape the cache:

- CelesTrak regenerates GP data **every two hours** and says so: *"there is
  no need for you to check more often"*. Repeated downloads inside one cycle
  can get an IP temporarily blocked, and a 403/404/301 means stop and report,
  not retry. A cache that re-downloads when the file is older than the
  configured age (default: 12 hours; never under 2) is the whole policy.
- **The TLE format is finished.** On 2026-07-11 the catalogue passed 100,000
  objects; anything with a six-digit number cannot be written as a TLE, and
  CelesTrak now defaults to CSV. Use `FORMAT=csv` (or `json`) and
  `EarthSatellite.from_omm()`; do not build anything on `load.tle_file()`.

**Space-Track** (the primary source, USSF) needs an account, allows 30
requests/minute and 300/hour, asks for GP queries no more than hourly, and its
user agreement forbids passing the data on. Nothing here needs it; CelesTrak
redistributes the same data without the agreement.

**Staleness.** An element set is *"only valid for a couple of weeks to either
side of that TLE's epoch"* (Skyfield docs); real satellites drift from the
ideal orbit by 1–3 km/day (sgp4 docs), and the ISS reboosts every few weeks.
For "when is the ISS visible tonight" a week-old set is accurate to well under
a minute; a month-old set can be minutes wrong and, after a reboost, simply
wrong. The tool must return the epoch age (`satellite.epoch`) and the reply
must carry it: *"based on orbital elements 9 days old"*. Offline, the cache
keeps serving with that caveat; there is no mode in which it refuses.

### Library: skyfield

| | |
|---|---|
| `skyfield` | 1.55, 2026-08-07, MIT. Depends on `numpy`, `sgp4>=2.13`, `jplephem>=2.13`, `certifi`. Pure Python except numpy. |
| `sgp4` | 2.27, 2026-07-03, MIT. C++ accelerator with a pure-Python fallback; positions agree with the reference to 0.1 mm. |
| Ephemeris | `de421.bsp`, 17 MB, 1900–2050, downloaded once and never updated. (`de440s.bsp`, 32 MB, 1849–2150, if the extra century is wanted.) |
| Time scales | `load.timescale(builtin=True)` uses the leap-second and ΔT tables shipped in the wheel — no network at start-up. |

The image constraint in `jarvis-core/requirements.txt` ("every one of these
installs from a wheel with no compiler") holds: numpy and sgp4 both ship
manylinux wheels for amd64 and arm64. numpy is nonetheless ~30 MB of image, so
this belongs in an optional requirements file that the `sky` integration
imports lazily and reports as *"skyfield not installed"* when absent — the
same degrade-with-a-message the `mqtt` client does without a backend.

The pass search, verbatim from the docs, is short:

```python
from skyfield.api import load, wgs84, EarthSatellite
ts = load.timescale(builtin=True)
eph = load("de421.bsp")                              # cached in /config/sky
house = wgs84.latlon(51.5072, -0.1276, elevation_m=11)

sats = [EarthSatellite.from_omm(ts, row) for row in csv.DictReader(open("stations.csv"))]
iss = next(s for s in sats if s.name == "ISS (ZARYA)")

t0, t1 = ts.now(), ts.now() + 1                      # next 24 h
t, events = iss.find_events(house, t0, t1, altitude_degrees=10.0)
# events: 0 = rises above 10°, 1 = culminates, 2 = sets below 10°
for ti, ev in zip(t, events):
    sunlit = iss.at(ti).is_sunlit(eph)               # the satellite is lit
    sun_alt = (eph["earth"] + house).at(ti).observe(eph["sun"]).apparent().altaz()[0]
    visible = sunlit and sun_alt.degrees < -6        # and the observer is in twilight or dark
    alt, az, _ = (iss - house).at(ti).altaz()
```

A "visible pass" is the conjunction of those three: above the horizon, lit by
the sun, observer in darkness. That is what Heavens-Above and the ISS apps
compute; it is a few lines and a 17 MB file.

**Cost.** One satellite, one night: milliseconds. The whole `visual` group
(100 satellites): well under a second. Starlink (11,000 satellites, 67 % of
the active catalogue) at one-minute steps for a night is ~16 million
propagations — tens of seconds on one core with the C accelerator, and
`sgp4.api.SatrecArray` batches it. Do that once at dusk in a thread, not on
demand, and keep Starlink opt-in: what people want from it is the "train" in
the week after a launch, which is the `last-30-days` group, not all 11,000.

**Data on disk.** `stations.csv` and `visual.csv` are a few tens of KB;
`starlink.csv` ~2 MB; `de421.bsp` 17 MB once. `/config/sky/` with an age
check, downloaded through `httpx` (already a dependency), not through
skyfield's own `load.download()` — Jarvis should own its cache policy and its
error messages.

### What Jarvis says, and the tool that says it

- *"The ISS comes over at 21:47 tonight — low in the west, up to 63° in the
  south-west, gone by 21:53. Bright, the sky will be dark by then."*
- *"Nothing visible tonight; the next visible pass is Thursday 05:12. Based on
  elements 3 days old."*
- Automations: `binary_sensor.iss_pass_soon` (ten minutes before a visible
  pass, for a `narrate` line and a lamp), `sensor.iss_next_visible_pass`
  (timestamp, with `max_altitude`, `direction`, `duration_s`, `elements_age_days`
  attributes).

One LLM tool, `sky_passes(satellite="ISS", hours=24, visible_only=true)`,
resolved by name against the cached groups (so *"Tiangong"*, *"NOAA 20"*,
*"Meteor-M 2-4"* all work), returning the list above. Not one tool per
satellite: every tool costs context every turn (`tests/test_prompt_budget.py`).

### Configuration shape

Modelled on `sun:`, location inherited from `jarvis:`:

```yaml
sky:
  cache_dir: /config/sky
  refresh_hours: 12            # never below CelesTrak's 2-hour cycle
  groups: [stations, visual]   # starlink and last-30-days are opt-in
  track:
    - ISS (ZARYA)
    - CSS (TIANHE)
  min_altitude: 10
```

### Weather satellites, the receiving kind

Two different requests hide under "weather satellites": *predicting* a pass
(the code above, `GROUP=weather`) and *receiving the pictures*. The second
changed in 2025: NOAA-18 was decommissioned on 2025-06-06, NOAA-19 on
2025-08-13 and NOAA-15 on 2025-08-19, ending 137 MHz APT after 47 years. The
hobbyist target is now **Meteor-M N2-4 LRPT** (digital, 137.9 MHz; N2-3 was
put in storage in 2024), decoded by **SatDump** (GPL-3.0; CLI `satdump live`
/ `record`, autotrack and scheduler; Docker builds exist but the docs
recommend building from source on Linux). It needs a V-dipole or QFH antenna
outdoors, an RTL-SDR of its own, and a few minutes of one core per pass. The
output is an image file. Worth doing only after the pass predictor exists —
which it would then drive — and then only as an extension that drops images
into a folder the console can show. Not in the first cut.

### Alternatives considered

- **`astral`** (3.2, 2022-11-05, Apache-2.0, pure Python): sun, twilight,
  golden/blue hour, moon phase, moonrise/set. No planets, no satellites. Good
  enough for moon phase alone; not a satellite answer.
- **`sgp4` alone**: propagates but has no rise/set search, no sunlit test,
  no ephemeris. Rebuilding those is rebuilding skyfield.
- **`pyephem`**: C extension, LGPL, its author maintains skyfield as the
  successor.
- **Public pass APIs** (N2YO, Heavens-Above): a key and a round trip per
  question, and nothing offline. Ruled out by the brief.

Verdict: skyfield, an optional requirement, a `sky` integration shaped like
`sun`.

---

## 2. Aircraft and ships overhead

### Hardware: one dongle per band

An RTL-SDR is a £25 USB stick that tunes 500 kHz – 1.7 GHz, but it tunes
**one** band at a time, and each of these services holds its dongle open
permanently. ADS-B (1090 MHz), AIS (162 MHz) and rtl_433 (433/868 MHz)
therefore need three dongles, ~£75, plus antennas. The RTL-SDR Blog V4 is
£24.90 at The Pi Hut with a dipole kit; the V4 is end-of-line (the tuner chips
ran out) but the V3 stays in production and a V4L is announced, and any
RTL2832U stick works for all three. A 1090 MHz stick antenna indoors on a
window sill sees 50–100 km of traffic; a proper outdoor antenna sees 300 km.
With three identical dongles, pin each service to a dongle **by serial**
(`rtl_eeprom -s 1090`, then `-d :1090` / `ADSB_SDR_SERIAL=1090`), or a reboot
swaps them.

CPU: readsb, AIS-catcher and rtl_433 each idle at a fraction of one core; all
three run on a Pi 3. USB bandwidth is the real ceiling on a Pi — one dongle
per USB controller if it stutters.

### ADS-B: aircraft

**What it gives Jarvis.** Every aircraft within radio range, once a second:
ICAO hex, callsign, position, barometric altitude, ground speed, track,
vertical rate, squawk, emergency flag, and — with the aircraft database —
registration, type code, type description and operator. Enough to say:

> *"A Ryanair 737-800 is passing 4 km to the north-west at 32,000 ft, heading
> south-west at 450 knots."*

and, from the emergency and squawk fields, the rarer *"an aircraft overhead is
squawking 7700"*. Altitude stays in feet for aircraft even though the house is
metric; that is the unit the operator asked in and the only one anyone uses
for aircraft.

**What it costs.** One dongle (~£25) and an antenna; ~150 MB image; a fraction
of a core; ~30 MB for the aircraft database; no data download at run time.

**How it plugs in.** `ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder`
(GPL-3.0) bundles readsb (the decoder), tar1090 (the map), graphs1090 and
mlat-hub. Tags are build-numbered — pin `latest-build-955` (2026-08-25), not
`latest`. Compose, in the house style:

```yaml
  adsb:
    image: ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder:latest-build-955
    container_name: jarvis-adsb
    restart: unless-stopped
    profiles: [sdr]
    network_mode: host                 # tar1090 on :8080, aircraft.json on the same port
    device_cgroup_rules: ["c 189:* rwm"]
    volumes:
      - /dev/bus/usb:/dev/bus/usb:rw
      - adsb-globe:/var/globe_history  # optional: tar1090's track history
    environment:
      - TZ=${TZ:-Europe/London}
      - READSB_DEVICE_TYPE=rtlsdr
      - ADSB_SDR_SERIAL=1090           # the dongle pinned to this band
      - LAT=${JARVIS_LATITUDE}
      - LONG=${JARVIS_LONGITUDE}
      - ALT=11m
      - READSB_RX_LOCATION_ACCURACY=2
      - TAR1090_DEFAULTCENTERLAT=${JARVIS_LATITUDE}
      - TAR1090_DEFAULTCENTERLON=${JARVIS_LONGITUDE}
      - READSB_NET_ENABLE=true         # Beast/SBS on 30003–30005 if something else wants a stream
    security_opt: [no-new-privileges:true]
    mem_limit: 512m
    cpus: 1.0
```

(`LAT`/`LONG` duplicate `jarvis:`'s coordinates; `tests/test_packaging.py`
should pin the two together the way it pins `TZ`.)

Data comes out of `http://127.0.0.1:8080/data/aircraft.json`, regenerated
every second, with `now`, `messages` and an `aircraft` array whose fields
(from readsb's `README-json.md`) are: `hex`, `flight` (callsign), `r`
(registration), `t` (type code), `desc` (type name), `ownOp` (operator, when
the database has it), `alt_baro` (ft, or `"ground"`), `alt_geom`, `gs` (kn),
`track` (°), `baro_rate` (ft/min), `lat`, `lon`, `seen_pos` (s), `seen`,
`rssi`, `squawk`, `emergency`, `category`, `type` (`adsb_icao` / `mlat` /
`mode_s` — the last has no position), `nic`/`rc` (position quality).

Two static tables turn hex codes into English:

- **Aircraft database**: `aircraft.csv.gz` from the `csv` branch of
  `wiedehopf/tar1090-db` (Mictronics' database), passed to readsb as
  `--db-file`, which then fills `r`, `t`, `desc`, `ownOp` and `dbFlags`
  (military, interesting). Ultrafeeder ships and refreshes it.
- **Operators**: Mictronics' `operators.json` maps the ICAO three-letter
  callsign prefix to operator name, country and telephony callsign (from FAA
  JO 7340.2), so `RYR1234` → Ryanair. Alternative with a clear licence:
  OpenFlights `airlines.dat` (ODbL; name, IATA, ICAO, callsign, country;
  ~5,900 rows), which `npow/airline-codes` mirrors as JSON weekly.

Zero code, today: the `rest:` integration can poll the JSON —

```yaml
rest:
  - resource: http://127.0.0.1:8080/data/aircraft.json
    scan_interval: 5
    sensor:
      - name: Aircraft Overhead
        value_template: "{{ value_json.aircraft | selectattr('lat', 'defined') | list | length }}"
```

— but do **not** add `json_attributes: [aircraft]`: a hundred aircraft in
the attributes of a sensor recorded every five seconds is exactly the
database growth the recorder's `exclude` block exists to prevent.

Properly: an `adsb:` integration (~300 lines) that polls the JSON, computes
distance and bearing from `jarvis:` (haversine, no dependency), and exposes
`sensor.aircraft_overhead` (count within `radius_km`),
`sensor.nearest_aircraft` (state = one English line, attributes = the raw
fields), `binary_sensor.aircraft_low_overhead` (inside 3 km and under
5,000 ft — the thing worth a `narrate` line), and one LLM tool
`aircraft_overhead(radius_km=10)` returning the top N by distance with the
sentence already composed, so the model reads rather than arithmetics.

### AIS: ships

**What it gives Jarvis.** Vessels within line of sight of the antenna on
161.975/162.025 MHz: MMSI, name, callsign, position, speed, course,
navigation status, ship type, destination. *"The Thames Clipper 'Jupiter' is
passing downstream at 8 knots."*

**What it costs.** One dongle, and an honest look at the map: VHF is line of
sight, so this is a feature for a house near a river, estuary or coast. On
the placeholder central-London coordinates it would hear the Thames and
little else; ten miles inland it hears nothing. The others in this document
work anywhere.

**How it plugs in.** AIS-catcher v0.70 (2026-06-19, GPL-3.0),
`ghcr.io/jvde-github/ais-catcher` (`latest` / `edge`; pin a version tag from
the package page). It publishes straight to MQTT —

```yaml
  ais:
    image: ghcr.io/jvde-github/ais-catcher:v0.70
    container_name: jarvis-ais
    restart: unless-stopped
    profiles: [sdr]
    network_mode: host
    device_cgroup_rules: ["c 189:* rwm"]
    volumes: ["/dev/bus/usb:/dev/bus/usb:rw"]
    command: >-
      -d :00000162 -gr tuner auto rtlagc on
      -N 8100
      -Q mqtt://127.0.0.1:1883 topic jarvis/ais/%mmsi% msgformat JSON_FULL qos 0 client_id jarvis-ais
    security_opt: [no-new-privileges:true]
    mem_limit: 256m
    cpus: 0.5
```

— `JSON_FULL` messages carry `mmsi`, `shipname`, `callsign`, `lat`, `lon`,
`speed`, `course`, `type`, `status`, `destination`, `rxtime`, `signalpower`.
Those are not discovery payloads (AIS-catcher does not speak HA discovery),
so the consumer is either a hand-declared `mqtt: sensor:` entry with a
`value_template`, or the same small integration pattern as `adsb:` with an
MQTT subscription instead of a poll. `-N 8100` gives a local map.

---

## 3. Radio sensors

Everything in this section ends up as an MQTT discovery payload, which the
`mqtt` integration already turns into entities. The per-source questions are
only hardware, image, and which topics.

### rtl_433: the 433/868 MHz band

**What it gives Jarvis.** Whatever is already transmitting around the house.
rtl_433 25.12 (2025-12-12, GPL-2.0) decodes 384 protocols: weather stations
(Fine Offset / Ecowitt WH65 and WS85, Bresser 5-in-1 / 7-in-1, Oregon
Scientific, Acurite, LaCrosse, Davis), indoor thermometers, soil-moisture
probes, TPMS from passing cars, doorbells, PIR and door sensors, the
**Watchman / Kingspan oil-tank monitors** UK houses have, and the US
900 MHz ERT utility meters (UK SMETS smart meters use a Zigbee HAN and DLMS;
rtl_433 cannot read them, and nothing here should promise to). Every packet
becomes JSON like:

```json
{"time":"2026-08-26T19:02:11","model":"Bresser-7in1","id":19871,
 "temperature_C":17.4,"humidity":71,"wind_avg_km_h":9.4,"wind_dir_deg":230,
 "rain_mm":214.6,"uvi":0.3,"light_lux":3200,"battery_ok":1,"rssi":-8.1}
```

**What it costs.** One dongle; ~80 MB image; a fraction of a core. In the
UK/EU most current weather stations are on 868 MHz, so listen to both with
`-H` hopping — at the price that each band is unwatched half the time (a
station transmitting every 12 s on 868 MHz is heard within a minute).

**How it plugs in.** `hertzg/rtl_433` (also on ghcr), tags
`<version>-<base>`, e.g. `hertzg/rtl_433:25.12-alpine`; amd64, arm64, armv7.

```yaml
  rtl433:
    image: hertzg/rtl_433:25.12-alpine
    container_name: jarvis-rtl433
    restart: unless-stopped
    profiles: [sdr]
    network_mode: host
    device_cgroup_rules: ["c 189:* rwm"]
    volumes: ["/dev/bus/usb:/dev/bus/usb:rw"]
    command: >-
      -d :00000433
      -f 433.92M -f 868.3M -H 60
      -C si -M time:iso -M protocol -M level
      -F mqtt://127.0.0.1:1883,retain=0,events=rtl_433/events,devices=rtl_433/devices[/model][/id]
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    mem_limit: 128m
    cpus: 0.5
```

`-F mqtt` publishes each packet whole to `rtl_433/events` and each field
to `rtl_433/devices/<model>/<id>/<field>`. `-F http` is the alternative
without a broker: a server on `:8433` with `/events` (server-sent events) and
`/stream`, buffering the last 100 packets. 25.12 added an MQTT LWT
availability option.

**Discovery.** rtl_433 itself does not announce devices; the reference
`examples/rtl_433_mqtt_hass.py` does. It subscribes to `rtl_433/+/events`,
and for every field it knows (80+: `temperature_C`, `humidity`,
`battery_ok`, `wind_avg_km_h`, `rain_mm`, `pressure_hPa`, `pressure_kPa`
for tyres, …) publishes
`homeassistant/sensor/<model>-<id>/<field>/config` with `device_class`,
`unit_of_measurement`, `state_class`, `value_template`, `unique_id`,
`device{identifiers, name, model, manufacturer}` and `expire_after`,
re-announcing every `--interval` (600 s) and honouring an `--ids` allowlist.

The allowlist is the important part. A 433 MHz receiver hears the street:
every car's tyre pressures, the neighbours' thermometers, a passing van's
doorbell. Auto-registering all of it is how a registry fills with junk.
Two ways to run the mapping:

1. The reference script as a sidecar (`python:3.12-alpine` + `paho-mqtt`,
   `--ids 19871,4402 --retain --expire-after 600`). Works, adds a container.
2. **Jarvis does the mapping** — an `rtl433:` block in the `mqtt` integration
   (or a 150-line `rtl433` integration) that subscribes to `rtl_433/events`,
   holds the same field table, creates entities only for `ids:` in the
   allowlist, and surfaces the unknown ones as *"heard a Bresser-7in1 id
   19871 that is not on the list"* in the console, where the operator clicks
   to adopt it. This is the `sensors` integration's auto-register cap, made
   visible. Recommended: it keeps the allowlist in `configuration.yaml`
   next to everything else, and it is where the *"is that ours?"* question
   belongs.

### Zigbee2MQTT

**What it gives Jarvis.** The cheapest reliable indoor sensors there are:
Aqara temperature/humidity/pressure (~£12), door contacts, motion, vibration,
water-leak, illuminance, and smart plugs with power and energy (the latter
`state_class: total_increasing`). Zigbee2MQTT 2.13.0 (2026-08-01, GPL-3.0).

**What it costs.** A coordinator — Sonoff ZBDongle-E, ~£19 (EFR32MG21;
the Zigbee2MQTT-recommended class) — and a Node.js container (~300 MB
image, ~150 MB RAM). Zigbee is a mesh; mains-powered devices (plugs) extend
it. Keep the dongle on a USB extension away from USB 3 ports (2.4 GHz
interference).

**How it plugs in.** `ghcr.io/koenkk/zigbee2mqtt:2.13.0`,
`./data:/app/data`, the coordinator mapped by `/dev/serial/by-id/…`, port
8080 for the frontend. In its `configuration.yaml`:

```yaml
homeassistant:
  enabled: true                      # discovery to homeassistant/…
  status_topic: homeassistant/status # it re-announces when it sees "online" here
mqtt:
  server: mqtt://127.0.0.1:1883
  base_topic: zigbee2mqtt
```

State is one JSON per device on `zigbee2mqtt/<friendly_name>`, availability
on `zigbee2mqtt/<friendly_name>/availability`, the bridge on
`zigbee2mqtt/bridge/state`. Discovery is retained, so a Jarvis restart
rebuilds everything from the broker.

**Two things to check in the `mqtt` integration first.**

- Jarvis's birth message goes to `jarvis/status`. Zigbee2MQTT, ESPHome and
  Theengs all listen on `homeassistant/status` for `online` and re-send
  discovery. Setting `birth_topic: homeassistant/status` (payload `online`)
  makes every gateway re-announce on a Jarvis restart, even where a payload
  was not retained. One line of config.
- `IGNORED_COMPONENTS` includes `event` and `device_automation`. Since
  Zigbee2MQTT 2.0 the legacy `action` sensor is off by default and button
  presses, remotes and doorbells arrive as `event` components. As things
  stand a Zigbee button is invisible to Jarvis; that is worth verifying
  against a real dongle before promising doorbells.

### ESPHome

**What it gives Jarvis.** Any sensor with a datasheet, on a £5 ESP32, in
YAML: the indoor air-quality build (Sensirion SCD41 for CO₂, SEN55 for
PM1/2.5/4/10 + VOC + NOx, BME280 for pressure) is ~£60 in parts or £90 as
the ready-made Airlytix ES1; soil moisture, water meters via pulse counters,
a radar presence sensor (LD2410). ESPHome 2026.8.1 (2026-08-23, MIT).

**What it costs.** A build toolchain once (the ESPHome dashboard container,
or `pip install esphome` on a laptop); Wi-Fi devices each holding a
connection; a few mA.

**How it plugs in.** Over MQTT — the native API is Home Assistant's
protocol, not Jarvis's:

```yaml
mqtt:
  broker: 192.168.1.10        # the Jarvis box; ESP32s are not on loopback
  discovery: true             # default; prefix homeassistant, retained
  topic_prefix: esphome/study-air
# and no `api:` block, or `api: { reboot_timeout: 0s }` — otherwise the device
# reboots every 15 minutes waiting for a Home Assistant that never connects.
```

State lands on `esphome/study-air/sensor/co2/state`; discovery on
`homeassistant/sensor/study-air/co2/config`. Note the broker address: the
mosquitto profile is **loopback-only by design** (`mosquitto/mosquitto.conf`
header). ESP32s and BLE gateways on other hosts need a LAN listener with
credentials, which is a deliberate widening — do it as its own change, with
the `.env` password, not by editing the listener in passing.

### BLE: Theengs Gateway / OpenMQTTGateway

**What it gives Jarvis.** Bluetooth beacons that already fill drawers:
Xiaomi LYWSD03MMC thermometers (£3, especially with ATC/PVVX firmware),
Govee and Inkbird thermometers, RuuviTag, SwitchBot meters and contacts,
Qingping, **Aranet4** (CO₂), Mopeka gas-bottle level, Xiaomi scales, Tilt
hydrometers — 133 decoded models. Also presence: which phones and watches
are in range.

**What it costs.** Nothing if the box has Bluetooth; a £5 ESP32 running
OpenMQTTGateway per far room otherwise (same decoder, same topics). Theengs
Gateway 1.7.5 (2026-06-07, GPL-3.0; `bleak`, `paho-mqtt`,
`TheengsDecoder`); OpenMQTTGateway GPL-3.0, flashed from a browser.

**How it plugs in.** `pip install TheengsGateway` in a venv on the host, or
the `theengs/gateway` image with `--net host` and `-v /var/run/dbus:/var/run/dbus`
(BlueZ over D-Bus; BLE does not virtualise). `-H 127.0.0.1 -D 1 -pr 1`:
decoded readings go to `home/TheengsGateway/BTtoMQTT/<MAC>` as

```json
{"id":"A4:C1:38:5B:12:0E","model":"LYWSD03MMC ATC","model_id":"LYWSD03MMC_ATC",
 "tempc":21.6,"hum":48,"batt":87,"volt":2.93,"rssi":-71}
```

with a discovery payload per field under `homeassistant/`. `-pr 1` adds
presence topics (a device seen / not seen), which `person` could consume.
An ESP32 with OpenMQTTGateway publishes to `home/OMG_ESP32_BLE/BTtoMQTT/<MAC>`
in the same shape.

### What a discovery payload looks like, and what Jarvis does with it

Single component:

```
topic:   homeassistant/sensor/bresser7in1-19871/temperature_C/config   (retained)
payload: {"name":"Temperature","state_topic":"rtl_433/devices/Bresser-7in1/19871/temperature_C",
          "unique_id":"Bresser-7in1-19871-T","device_class":"temperature",
          "unit_of_measurement":"°C","state_class":"measurement","expire_after":600,
          "availability_topic":"rtl_433/status",
          "device":{"identifiers":["Bresser-7in1-19871"],"name":"Garden weather station",
                    "manufacturer":"Bresser","model":"7-in-1","suggested_area":"Garden"},
          "origin":{"name":"rtl_433","sw":"25.12"}}
```

Device bundle (one message, many entities; abbreviated keys):

```
topic:   homeassistant/device/study-air/config
payload: {"dev":{"ids":"study-air","name":"Study air","mf":"Airlytix","mdl":"ES1","sa":"Study"},
          "o":{"name":"esphome","sw":"2026.8.1"},
          "avty_t":"esphome/study-air/status",
          "cmps":{"co2":{"p":"sensor","stat_t":"esphome/study-air/sensor/co2/state",
                         "uniq_id":"study-air-co2","dev_cla":"carbon_dioxide",
                         "unit_of_meas":"ppm","stat_cla":"measurement"},
                  "pm25":{"p":"sensor","stat_t":"esphome/study-air/sensor/pm25/state",
                          "uniq_id":"study-air-pm25","dev_cla":"pm25","unit_of_meas":"µg/m³",
                          "stat_cla":"measurement"}}}
```

`discovery.py` handles both (`_async_handle_device_bundle`), expands the
abbreviations (`stat_t` → `state_topic`, `dev_cla` → `device_class`, …),
`entity.py` subscribes to state and availability, applies `value_template`,
`expire_after`, and keeps `state_class` as an attribute. The recorder then
stores every state change. Nothing else is needed for the entity to exist,
be exposed to `get_state` and appear on the dashboard.

---

## 4. Weather, environment, and what "any sensor" should mean

### A local weather station

Three ways in, best first:

1. **A 433/868 MHz station read by rtl_433** (Bresser 7-in-1 ~£120, Ecowitt
   WS69/WH65 array ~£100, or the Fine Offset WS85 the 25.12 release added):
   temperature, humidity, wind, gust, direction, rain, UV, lux, every
   12–60 s, no gateway, no cloud, nothing to configure on the station. The
   rtl_433 path above, and an allowlisted id.
2. **An Ecowitt gateway** (GW1100 ~£30, GW2000) with *Customized upload*
   pointed at the Jarvis box: it POSTs its readings as form-encoded
   `tempf=…&humidity=…&baromrelin=…&windspeedmph=…&rainratein=…` to a path
   of your choosing every 30 s (imperial, always), and also serves
   `/get_livedata_info` as JSON for polling. No radio hardware, works
   without internet. Jarvis's `POST /api/sensor/<id>` takes one value per
   call, so this needs a 100-line `ecowitt` webhook that maps the Ecowitt
   field names to device classes, converts units, and fans out into the
   `sensors` registry. Cheaper than a dongle if the station is Ecowitt.
3. **Polling `/get_livedata_info` with `rest:`** — zero code, but the JSON is
   a list of `{id, val}` pairs with unit strings in the values, so every
   sensor is a fiddly `value_template`. A stop-gap.

### A local *forecast*

The `briefing` reads `weather.*` and nothing provides it. Three tiers:

- **Zambretti from the barometer** (offline, no data). Sea-level pressure,
  its three-hour trend (±1.6 hPa is the threshold) and wind direction pick one
  of 26 statements ("Fine, becoming less settled"); it has been ~90 %
  right for 12-hour forecasts since 1915 and is a page of Python. Honest
  scope: it says *settled / unsettled / rain*, not *14 °C at 3 pm*.
- **Cached Open-Meteo** (download, cached). Free for non-commercial use
  without a key (< 10,000 calls/day), data CC BY 4.0 with attribution. One
  fetch an hour, kept on disk; `weather.home` serves the last forecast with
  an `age` attribute when offline. This is the "download is fine if cached"
  case exactly, and it is what makes the briefing's weather section exist.
- **Self-hosted Open-Meteo** (`ghcr.io/open-meteo/open-meteo`; the API
  server syncs ICON/GFS/IFS model output from AWS open data). Fully local
  forecasts, at 8–16 GB RAM and 32–150 GB disk with hourly syncs. Real, but
  a different order of cost from everything else here; a note for the
  future, not a recommendation.

### Indoor air quality

- ESPHome SCD41 + SEN55 (CO₂, PM, VOC/NOx indices, temperature, humidity),
  the Airlytix ES1 if soldering is unwelcome — MQTT discovery, above.
- Aranet4 (~£170; CO₂ by NDIR, BLE broadcast) through Theengs.
- Qingping CGS1/CGP22C: BLE through Theengs, or Wi-Fi with a local MQTT
  option.

What Jarvis does with it is a `narrate` rule (*"CO₂ in the study is 1,400
ppm — open a window"*) and a history question (*"what was the bedroom CO₂
overnight?"*), which brings the architecture.

### "Any sensor", architecturally

**One ingestion path: MQTT discovery**, with the `sensors` HTTP door for
things that cannot speak it (Ecowitt, a bare ESP8266, a shell script). Every
source in this document already publishes discovery or is one small adapter
away from it. Do not add a third path per source.

**One entity model**: `sensor` / `binary_sensor` with `device_class`,
`unit_of_measurement`, `state_class`, `expire_after`, and a `device`
(identifiers, name, manufacturer, model, `suggested_area`) so entities land
in areas and dashboards group by device. This is the model `mqtt` and
`sensors` already share. Two additions it needs:

- **Canonical units at ingest.** rtl_433 with `-C si` is metric, Ecowitt is
  imperial, aircraft are feet, Theengs is metric. A per-device-class
  canonical unit (°C, hPa, km/h, mm, ppm, µg/m³, lx) applied when the entity
  is created — with the original kept as an attribute — is what stops
  `history.stats` averaging °F with °C.
- **An allowlist for radio.** Auto-registration is right for a device you
  installed (an ESP32 posting to `/api/sensor/study_co2`) and wrong for a
  receiver that hears the street. Radio sources register only ids in the
  allowlist and report the rest as candidates.

**History with depth.** The recorder keeps raw rows for 10 days and
`history.stats` computes min/max/mean over them. Sensors need what Home
Assistant calls long-term statistics: per entity, per hour, `min / max /
mean` for `state_class: measurement` and `sum` for `total_increasing`
(rain, energy), kept indefinitely — a few hundred bytes an hour per sensor.
Computed at the recorder's commit interval, it answers *"how much rain this
month"* and *"how does this August compare"* without keeping the raw rows.

**Tools the model gets.** Two, not twenty:

- `sensor_history(entity | area | device_class, period="24h"|"7d"|"30d", aggregate="min|max|mean|sum|series")`
  over `history.stats` and the new statistics table, returning numbers with
  units and a one-line summary.
- `sensor_summary(area | device_class)` — current value, trend over the last
  three hours (rising / falling / steady with the delta), last-seen age,
  and `unavailable` called out — so *"is anything wrong with the sensors?"*
  has one answer.

`get_state` already covers *now*; these cover *then* and *how it is going*.

---

## 5. Astronomy for a house

What is computable offline versus what needs a download, and how stale each
may be:

| Question | Offline? | Needs | Staleness |
|---|---|---|---|
| Sunrise, sunset, dawn, dusk, solar noon | yes — `sun` today | nothing | never |
| Twilight level (civil / nautical / astronomical / dark) | yes | skyfield `dark_twilight_day`, de421 | never |
| Moon phase, illumination, moonrise / moonset, next full / new moon | yes | skyfield `moon_phase` / `moon_phases` / `find_risings`; or `astral` without any ephemeris | never |
| "Is Jupiter up, where?" planet rise / set / altitude / azimuth | yes | skyfield `find_risings` / `altaz`, de421 | never |
| Conjunctions, oppositions, seasons, lunar eclipses | yes | skyfield `oppositions_conjunctions`, `seasons`, `eclipselib.lunar_eclipses` | never |
| Meteor showers: which are active tonight, radiant altitude, moon interference | yes | a static table (below) + skyfield for radiant and moon | yearly refresh of the table |
| ISS / satellite passes | yes, from cache | CelesTrak CSV | ~2 weeks |
| Aurora likelihood | no | NOAA SWPC Kp forecast JSON (10 days, 3-hourly); OVATION grid | hours |
| Tides | UK: no; US: yes | Admiralty API (UK) / NOAA harmonic constituents + `pytides` (US) | a week (UK cache) / never (US) |
| Weather forecast | no (see §4) | Open-Meteo cache | hours |

**Meteor showers.** The IMO Working List of Visual Meteor Showers (Table 5
of the annual calendar, PDF; the IAU Meteor Data Center keeps the machine-
readable shower database) is small enough to ship as a YAML table with a
yearly refresh — maximum dates move by a day between years. The major
showers, from the 2026 calendar:

| Shower | Active | Max 2026 | λ☉ | Radiant α / δ | V km/s | ZHR |
|---|---|---|---|---|---|---|
| Quadrantids (QUA) | Dec 28 – Jan 12 | Jan 03 | 283.15° | 230° / +49° | 41 | 80 |
| April Lyrids (LYR) | Apr 14 – Apr 30 | Apr 22 | 32.32° | 271° / +34° | 49 | 18 |
| η-Aquariids (ETA) | Apr 19 – May 28 | May 06 | 45.5° | 338° / −01° | 66 | 50 |
| S. δ-Aquariids (SDA) | Jul 12 – Aug 23 | Jul 31 | 128° | 340° / −16° | 41 | 25 |
| Perseids (PER) | Jul 17 – Aug 24 | Aug 13 | 140.0° | 48° / +58° | 59 | 100 |
| Orionids (ORI) | Oct 02 – Nov 07 | Oct 21 | 208° | 95° / +16° | 66 | 20 |
| Leonids (LEO) | Nov 06 – Nov 30 | Nov 17 | 235.27° | 152° / +22° | 71 | 15 |
| Geminids (GEM) | Dec 04 – Dec 20 | Dec 14 | 262.2° | 112° / +33° | 35 | 150 |
| Ursids (URS) | Dec 17 – Dec 26 | Dec 22 | 270.7° | 217° / +76° | 33 | 10 |

The solar longitude of maximum (λ☉) is what to key on — skyfield gives the
sun's apparent ecliptic longitude for any instant, so *"the Perseids peak
tonight"* is computed, not looked up by date; the radiant's altitude at the
house and the moon's phase and altitude decide whether it is worth going
outside (the 2026 calendar notes the Quadrantid peak falls on a full moon,
the Perseid and Geminid peaks on moon-free nights).

**Aurora.** NOAA's rule of thumb: at Kp 0 the aurora sits at ~66° geomagnetic
latitude and moves ~2° equatorward per Kp step, reaching ~48° at Kp 9.
Geomagnetic latitude follows from the dipole pole (80.85° N, 72.76° W for
2025.0, WMM2025) with one spherical-trig line; central London comes out at
~53°, so a display needs Kp 6–7 (a G2–G3 storm) and a dark north horizon —
which is what the Kp forecast says three days out. Two cached files:

- `https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json`
  — rows of `[time_tag, kp, observed|estimated|predicted, noaa_scale]`,
  3-hourly, ~10 days ahead; refresh hourly, a few KB.
- `https://services.swpc.noaa.gov/json/ovation_aurora_latest.json` — the
  OVATION model, a 1° grid of `[lon, lat, probability]`, ~1.3 MB, forecast
  30–90 minutes ahead; the cell at the house's coordinates is the "likely
  tonight?" number with no geomagnetic maths at all. Refresh every 30 min
  while the Kp forecast is ≥ 5, otherwise not at all.

Entities: `sensor.aurora_kp_forecast_max_24h`, `binary_sensor.aurora_possible`
(Kp forecast ≥ house threshold **and** dark **and** the OVATION cell ≥ 10 %).
SWPC's own viewing guidance — within an hour or two of local midnight, away
from lights, spring and autumn favoured — is a `narrate` line, not code.

**Tides.** The honest split:

- **UK**: the UKHO asserts copyright over harmonic constants, had them
  removed from XTide in 2001, and the free XTide UK set stopped being
  maintained in 2012. There is no legal offline computation. The Admiralty
  **UK Tidal API – Discovery** tier is free: high and low water for 607
  stations, today + 6 days, 10,000 calls/month. One call a day per station,
  cached, gives a week of tide times offline — `sensor.next_high_water`
  with `height_m` and a `source_age` attribute. (The NOC's NTSLF pages
  publish 28-day tables for ~45 ports without an API.)
- **US** (and anywhere NOAA publishes constituents): CO-OPS publishes 37
  harmonic constituents per station; `pytides` (MIT; numpy + scipy)
  reproduces NOAA's predictions to 5 mm from them, forever, offline. scipy
  is a 40 MB dependency for a feature the placeholder location cannot use;
  optional, like skyfield.

**Moon without skyfield.** If satellites are not wanted at first, `astral`
(Apache-2.0, pure Python, no data files) covers moon phase and rise/set and
the twilight levels `sun` lacks, and its `Observer` takes the same
lat/lon/elevation. But it is a 2022 release with no planets; once skyfield
is in for satellites it does the moon too, and one dependency beats two.

---

## Recommendation and order

1. **Config, not code, first**: add `sensors:` to `configuration.yaml`
   (`allow_auto_register: true`, a shared ingest token in `secrets.yaml`),
   set `birth_topic: homeassistant/status`, and verify with an ESPHome or
   Theengs device that discovery round-trips. This is what makes "any
   sensor" true for everything with a datasheet.
2. **`sky` integration** shaped like `sun`: skyfield as an optional
   requirement, CelesTrak CSV + de421 cached under `/config/sky`, ISS and
   `visual` passes, moon phase/rise/set, planet visibility, twilight, the
   meteor table, `sky_passes` and a `sky_tonight` tool. No hardware; the
   most asked-for feature ("when can I see the ISS") for the least cost.
3. **ADS-B** behind `--profile sdr`: ultrafeeder pinned, an `adsb:`
   integration with distance/bearing and the `aircraft_overhead` tool. The
   most rewarding radio for one dongle.
4. **rtl_433** on the same profile with Jarvis-side discovery and an id
   allowlist — this is where the weather station arrives, and Zambretti on
   its barometer is the first forecast.
5. **History depth**: hourly statistics in the recorder and the
   `sensor_history` / `sensor_summary` tools. Without this, sensors answer
   "now" and nothing else.
6. **Cached Open-Meteo** for `weather.home` so the briefing's weather section
   stops being empty; aurora from SWPC; tides via the Admiralty Discovery
   tier for a UK house. All three are small cached downloads with an `age`
   attribute and offline behaviour that degrades to "as of …".
7. **AIS** only if the house can see water; **Meteor-M imagery** only after
   the pass predictor exists to drive it.

Nothing above adds a listener the threat model does not already have: the
SDR containers publish to the loopback broker; the ESP32/BLE gateways are
the one case that needs a LAN MQTT listener, and that is a separate,
credentialed change to `mosquitto.conf`.

---

## Sources

Satellites
- Skyfield — Earth satellites: https://rhodesmill.org/skyfield/earth-satellites.html
- Skyfield — almanac: https://rhodesmill.org/skyfield/almanac.html
- Skyfield — planets and ephemeris files: https://rhodesmill.org/skyfield/planets.html
- skyfield on PyPI (1.55, MIT): https://pypi.org/project/skyfield/
- sgp4 on PyPI (2.27, MIT): https://pypi.org/project/sgp4/
- CelesTrak GP data formats and query API: https://celestrak.org/NORAD/documentation/gp-data-formats.php
- CelesTrak current GP element sets (6-digit catalogue notice, groups): https://celestrak.org/NORAD/elements/
- Space-Track documentation (account, rate limits, user agreement): https://www.space-track.org/documentation
- NOAA decommissions the POES constellation: https://www.nesdis.noaa.gov/news/legacy-orbit-noaa-decommissions-the-poes-satellite-constellation
- NOAA-15 and NOAA-19 decommissioning (RTL-SDR.com): https://www.rtl-sdr.com/noaa-15-and-19-to-be-decommissioned-within-the-next-two-weeks/
- Meteor-M N2-4 status (Wikipedia): https://en.wikipedia.org/wiki/Meteor-M_No.2-4
- SatDump documentation: https://docs.satdump.org/index.html
- SatDump repository (GPL-3.0): https://github.com/SatDump/SatDump

Aircraft and ships
- docker-adsb-ultrafeeder: https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder
- ultrafeeder image tags: https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder/pkgs/container/docker-adsb-ultrafeeder
- readsb `aircraft.json` field reference: https://github.com/wiedehopf/readsb/blob/dev/README-json.md
- tar1090-db (aircraft database, csv branch): https://github.com/wiedehopf/tar1090-db
- Mictronics readsb database files (operators.json, types.json): https://github.com/Mictronics/readsb/tree/dev/webapp/src/db
- OpenFlights data (airlines.dat, ODbL): https://openflights.org/data
- npow/airline-codes (weekly JSON mirror): https://github.com/npow/airline-codes
- AIS-catcher: https://github.com/jvde-github/AIS-catcher
- AIS-catcher releases (v0.70): https://github.com/jvde-github/AIS-catcher/releases
- AIS-catcher Docker: https://jvde-github.github.io/AIS-catcher-docs/installation/docker/
- AIS-catcher MQTT output: https://jvde-github.github.io/AIS-catcher-docs/configuration/output/MQTT/
- RTL-SDR Blog V4 at The Pi Hut (£24.90): https://thepihut.com/products/rtl-sdr-blog-v4-usb-dongle-with-dipole-antenna-kit
- RTL-SDR Blog V4 end of line: https://www.rtl-sdr.com/rtl-sdr-blog-v4-end-of-line/

Radio sensors
- rtl_433 README (frequencies, hopping, outputs, 384 protocols): https://github.com/merbanan/rtl_433/blob/master/README.md
- rtl_433 integration guide (MQTT topics): https://github.com/merbanan/rtl_433/blob/master/docs/INTEGRATION.md
- rtl_433 releases (25.12): https://github.com/merbanan/rtl_433/releases
- rtl_433_mqtt_hass.py (HA discovery bridge): https://github.com/merbanan/rtl_433/blob/master/examples/rtl_433_mqtt_hass.py
- rtl_433 HTTP server (`-F http`, :8433): https://github.com/merbanan/rtl_433/blob/master/src/http_server.c
- hertzg/rtl_433 Docker image: https://github.com/hertzg/rtl_433_docker
- Zigbee2MQTT Docker: https://www.zigbee2mqtt.io/guide/installation/02_docker.html
- Zigbee2MQTT Home Assistant integration: https://www.zigbee2mqtt.io/guide/usage/integrations/home_assistant.html
- Zigbee2MQTT releases (2.13.0): https://github.com/Koenkk/zigbee2mqtt/releases
- Sonoff ZBDongle-E: https://sonoff.tech/en-us/products/sonoff-zigbee-3-0-usb-dongle-plus-zbdongle-e
- ESPHome MQTT component: https://esphome.io/components/mqtt.html
- ESPHome on PyPI (2026.8.1, MIT): https://pypi.org/project/esphome/
- Theengs Gateway: https://gateway.theengs.io/
- Theengs Gateway — use (options, topics): https://gateway.theengs.io/use/use.html
- Theengs Gateway — install: https://gateway.theengs.io/install/install.html
- TheengsGateway on PyPI (1.7.5, GPL-3.0): https://pypi.org/project/TheengsGateway/
- Theengs Decoder supported devices (133): https://decoder.theengs.io/devices/devices.html
- OpenMQTTGateway (GPL-3.0): https://github.com/1technophile/OpenMQTTGateway
- OpenMQTTGateway BLE setup: https://docs.openmqttgateway.com/setitup/ble.html
- Home Assistant MQTT discovery protocol: https://www.home-assistant.io/integrations/mqtt/
- Home Assistant sensor entity (state classes, device classes): https://developers.home-assistant.io/docs/core/entity/sensor/
- Airlytix ES1 (ESPHome SCD41 + SEN55): https://www.tindie.com/products/airlytix/airlytix-es1-esphome-smart-air-quality-sensor/
- SparkFun SCD41 + SEN55 board: https://www.sparkfun.com/sparkfun-indoor-air-quality-combo-sensor-scd41-sen55-qwiic.html

Weather and environment
- Ecowitt gateway local API and custom upload: https://blog.meteodrenthe.nl/2023/02/03/how-to-use-the-ecowitt-gateway-gw1000-gw1100-local-api/
- Ecowitt gateways compared: https://smartout.net/ecowitt-gateways-compared-gw1100-gw1200-gw2000-gw3000/
- Zambretti forecaster (algorithm): https://integritext.net/DrKFS/zambretti.htm
- Zambretti forecaster (background, accuracy): https://w4krl.com/zambretti-forecaster/
- Open-Meteo terms (free non-commercial, CC BY 4.0): https://open-meteo.com/en/terms
- Open-Meteo self-hosting: https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md

Astronomy
- astral on PyPI (3.2, Apache-2.0): https://pypi.org/pypi/astral/json
- astral repository: https://github.com/sffjunkie/astral
- IMO 2026 Meteor Shower Calendar (Table 5): https://www.imo.net/files/meteor-shower/cal2026.pdf
- IMO calendar page: https://www.imo.net/resources/calendar/
- IAU Meteor Data Center shower database: https://www.ta3.sk/IAUC22DB/MDC2007
- NOAA SWPC — tips on viewing the aurora (Kp vs latitude): https://www.spaceweather.gov/content/tips-viewing-aurora
- NOAA SWPC planetary K-index forecast (JSON): https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json
- NOAA SWPC OVATION aurora grid (JSON): https://services.swpc.noaa.gov/json/ovation_aurora_latest.json
- NCEI — wandering of the geomagnetic poles (WMM2025 dipole pole): https://www.ncei.noaa.gov/products/wandering-geomagnetic-poles
- XTide — about harmonic constants (UKHO copyright history): https://flaterco.com/xtide/harmonics.html
- Admiralty UK Tidal API — Discovery tier: https://admiraltyapi.portal.azure-api.net/products/uk-tidal-api
- UK Tidal API on api.gov.uk: https://api.gov.uk/ukho/uk-tidal-api-discovery/
- pytides (MIT): https://github.com/sam-cox/pytides
- pytides with NOAA constituents: https://github.com/sam-cox/pytides/wiki/How-to-use-the-NOAA's-published-Harmonic-Constituents-in-Python-with-Pytides
