# Devices and protocols — sensing and controlling the whole house, locally

Research note, 26 August 2026. Scope: every realistic way a device in a house
can reach Jarvis without a cloud, what each way gives, what it costs, and how
it plugs into what `jarvis-core` already has. Versions and licences were read
from the projects' own repositories on the day of writing; anything marked
*(unverified)* is from memory of the protocol and should be checked against the
device before it is relied on. Sources are at the end.

## 0. What the repo already has (the baseline everything below plugs into)

| Piece | Where | What it does today | Gap that matters for "anything" |
|---|---|---|---|
| MQTT + HA-style discovery | `jarvis/integrations/mqtt/` | Speaks the Home Assistant discovery protocol: `<prefix>/<component>/[<node_id>/]<object_id>/config` and the newer device bundle `<prefix>/device/<object_id>/config` with a `cmps` map, abbreviations and `~` expansion. 13 components: `binary_sensor button climate cover fan light lock number select sensor siren switch text`. | `device_tracker` is **not** a component (Wi-Fi/BLE presence bridges publish it). `event` and `device_automation` are in `IGNORED_COMPONENTS`, which is where Zigbee2MQTT 2.x puts button presses. Birth topic is `jarvis/status`; every bridge in this note listens on `homeassistant/status` by default. |
| Broker | `jarvis-core/docker-compose.yml --profile mqtt`, `mosquitto/mosquitto.conf` | Mosquitto 2, `listener 1883 127.0.0.1`, anonymous, persistence on. | Loopback-only means *bridges on this host* (Zigbee2MQTT, Z-Wave JS UI, evcc, ebusd) can use it unchanged because the stack is `network_mode: host`; *Wi-Fi devices* (Tasmota, ESPHome-over-MQTT, Shelly, ESPresense) cannot until the listener is widened with a password file, exactly as the conf's comment prescribes. |
| Hue | `integrations/hue/` | Bridge v1/v2 over HTTP, polled every 15 s, rooms as groups. | Polling; v2 has a push `/eventstream/clip/v2` SSE feed. |
| WLED | `integrations/wled/` | JSON API, light + effect select, polled every 10 s. | Polling; WLED also has a `/ws` push socket. |
| Sensor ingest | `integrations/sensors/` | `POST /api/sensor/<id>` auto-registers a sensor with an inferred class/unit/area; `expire_after` honesty. | Any ESP/P1/Modbus script can push here with zero server config — the cheapest ingestion path in the tree. |
| Presence | `integrations/person/` | `person.*` aggregated from `device_tracker.*`; `device_tracker.see` service with GPS→home radius. | Nothing *produces* device trackers yet (the Android app has no location reporting; grep finds only the manifest permission). |
| REST / command_line | `integrations/rest/`, `integrations/command_line/` | Polled HTTP JSON and shell sensors with `value_template`. | Enough for HomeWizard, Fronius, Roku status, Shelly Gen1 without writing code. |
| Domain services + targets | `integrations/domains/`, `homeassistant_compat/` | `light/switch/cover/climate/lock/media_player/vacuum/...` services with `entity_id`/`area_id`/`device_id` targets; `homeassistant.turn_off` fans out per domain. `lock` deliberately excluded from "turn off the house". | `media_player` has a domain but **no provider** — no Sonos/Cast/TV integration exists. |
| Containers | both compose files | Everything is `network_mode: host`; `jarvis-core` maps no serial devices (`devices:` is a comment). | mDNS/multicast (Chromecast, Govee, Kasa, ESPHome, Matter) work from the host namespace; USB radios should be attached to a bridge container, not to core. |

## 1. Radio protocols: Zigbee, Z-Wave, Thread/Matter

### 1.1 Zigbee via Zigbee2MQTT (recommended)

**What it gives.** One process owns the coordinator and publishes every device
as HA discovery over MQTT — Jarvis already consumes that. The supported-devices
list is over 5,000 devices from 550+ vendors: bulbs, plugs with power metering,
contact/motion/mmWave/temperature/humidity sensors, TRVs, blinds, locks, remotes,
sirens. Groups (lights/switches/covers/locks) are discovered too.

**What it costs.** Zigbee2MQTT 2.13.0 (1 Aug 2026), GPL-3.0, Node.js 24/26 on
bare metal or the `koenkk/zigbee2mqtt` image (a separate process, so the
licence does not touch Jarvis). ~150 MB RAM. One coordinator dongle, €20–40:

- Silicon Labs EmberZNet — Sonoff ZBDongle-E, SMLIGHT SLZB-06M (Ethernet
  coordinator; the docs warn that Wi-Fi-attached coordinators lose the serial
  protocol's fault tolerance, so use USB or Ethernet, never Wi-Fi).
- Texas Instruments zStack (CC2652P) — Sonoff ZBDongle-P and similar.
- deCONZ ConBee II/III.
- ZBOSS (Nordic) is listed as experimental; ZiGate as not maintained.

Put the dongle on a USB 2 extension away from USB 3 ports and the Wi-Fi AP;
this is the single most common cause of a flaky mesh and is in every Z2M FAQ.

**How it plugs in.**

```yaml
# jarvis-core/docker-compose.yml (proposed)
  zigbee2mqtt:
    image: koenkk/zigbee2mqtt:2.13.0
    profiles: [zigbee]
    network_mode: host
    restart: unless-stopped
    volumes: [./zigbee2mqtt:/app/data, /run/udev:/run/udev:ro]
    devices: ["/dev/serial/by-id/usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_XXXX-if00-port0:/dev/ttyUSB0"]
    depends_on: [mosquitto]
```

```yaml
# zigbee2mqtt/configuration.yaml
mqtt: { base_topic: zigbee2mqtt, server: mqtt://127.0.0.1:1883 }
serial: { port: /dev/ttyUSB0, adapter: ember }      # ember | zstack | deconz
homeassistant:
  enabled: true
  discovery_topic: homeassistant
  status_topic: homeassistant/status               # Z2M re-announces when it sees "online" here
  legacy_action_sensor: true                        # see below
frontend: { port: 8080 }
advanced: { network_key: GENERATE }
```

Topics Jarvis will see: state on `zigbee2mqtt/<friendly_name>` (JSON), commands
on `zigbee2mqtt/<friendly_name>/set`, device list on `zigbee2mqtt/bridge/devices`,
pairing via `zigbee2mqtt/bridge/request/permit_join` `{"time": 254}` answered on
`bridge/response/permit_join` `{"status":"ok",...}`. Discovery payloads use the
device-bundle form with `origin`, extended device identifiers, `availability`
pointing at `zigbee2mqtt/bridge/state`.

Three things to change on the Jarvis side, all small:

1. **Birth topic.** Z2M (default `homeassistant/status`) and Z-Wave JS UI
   (same, since 4.0.0) republish discovery when they see `online` there. Either
   set `birth_topic: homeassistant/status` in the `mqtt:` block or teach the
   integration to publish to both.
2. **Buttons and remotes.** Z2M 2.0 stopped discovering the `sensor.<x>_action`
   entity by default; actions arrive as MQTT device triggers
   (`device_automation`) and, with `experimental_event_entities: true`, as
   `event` entities. Both are in `IGNORED_COMPONENTS`. Quickest fix is
   `legacy_action_sensor: true` in Z2M (the sensor reappears, with an empty
   reset publish after each press); the right fix is an `event` component in
   `mqtt/entity.py` that fires a Jarvis event per message.
3. **Availability.** Z2M's per-device availability is off unless
   `availability: { enabled: true }` is set; without it a dead battery sensor
   keeps its last value forever. Jarvis's `expire_after` is the same idea for
   the HTTP path.

### 1.2 Zigbee in-process (ZHA-style) — not recommended

`zigpy` 2.1.0 (GPL-3.0) plus a radio library (`bellows`, `zigpy-znp`,
`zigpy-deconz`) can run inside a Python process; that is what HA's ZHA does.
The `zha` library that HA split out is also GPL-3.0. Doing this in `jarvis-core`
would (a) pull GPL code into the hub, (b) require the serial device and its
quirk database (`zha-device-handlers`) in core, and (c) reinvent the Z2M
converter set. The only benefit — no broker — is not worth it when the broker
is already a profile in the stack.

### 1.3 Z-Wave via Z-Wave JS UI

**What it gives.** Locks, thermostats, dimmers and the older mains-powered
sensor fleet that never moved to Zigbee; S2 security; Z-Wave Long Range on
800-series sticks. Z-Wave JS UI v11.22.3 (14 Aug 2026), MIT, image
`zwavejs/zwave-js-ui`, UI on 8091, Z-Wave JS websocket server on 3000. Sticks:
Zooz ZST39 LR (800 series, LR + SmartStart), Aeotec Z-Stick 7/10.

**Two ways in, pick one:**

- *MQTT discovery* (zero Jarvis code). Enable "MQTT Discovery" in ZUI with
  retain on; ZUI publishes `homeassistant/<component>/.../config` and values on
  `zwave/<node>/<commandclass>/<endpoint>/<property>`. The ZUI docs warn that
  "Home Assistant updates often break Z-Wave JS UI device discovery" — Jarvis's
  parser is pinned by its own tests, so that churn does not reach it.
- *Websocket* (a `zwave` integration, ~300 lines). Client library
  `zwave-js-server-python` 0.73.1 (Apache-2.0), `ws://127.0.0.1:3000`. The
  protocol: server sends `{"type":"version",...,"minSchemaVersion","maxSchemaVersion"}`,
  client sends `initialize` with a `schemaVersion`, then `start_listening`
  returns the full node/value dump and streams `node.value_updated` events.
  Richer (device metadata, S2 inclusion flow, firmware updates) but a second
  code path to maintain. Start with discovery; add the socket when someone
  needs inclusion from the Jarvis UI.

```yaml
  zwave-js-ui:
    image: zwavejs/zwave-js-ui:11.22.3
    profiles: [zwave]
    network_mode: host
    restart: unless-stopped
    tty: true
    stop_signal: SIGINT
    devices: ["/dev/serial/by-id/usb-Zooz_800_Z-Wave_Stick_XXXX-if00:/dev/zwave"]
    volumes: [./zwave-js-ui:/usr/src/app/store]
```

### 1.4 Thread / Matter

**What it gives.** Matter is the only local API that Apple/Google/Amazon-first
devices (Eve, Nanoleaf, Aqara's newer hubs, IKEA Dirigera-bridged devices, most
2024+ Wi-Fi plugs) expose. Thread devices additionally need a border router.

**What it costs.**

- *Controller.* `python-matter-server` is finished: 8.1.2 is its final release
  ("will no longer receive updates or support"). Its replacement is
  `matterjs-server` v1.4.0 (7 Aug 2026), Apache-2.0,
  `ghcr.io/matter-js/matterjs-server:stable`, described as "a drop-in
  replacement for the Python Matter Server" with the same websocket interface on
  `localhost:5580/ws`; the README still calls it beta and not yet re-certified
  by the CSA. The websocket commands Jarvis would use: `start_listening` (dumps
  all nodes, then streams attribute changes), `commission_with_code`,
  `read_attribute`, `device_command`. The python client package in the old
  repo (`matter_server.client`) still speaks this API.
- *Network.* Matter is IPv6 link-local + mDNS. The controller must run in the
  host network namespace (already true here), IPv6 must be enabled on the
  host interface, and controller and devices must be on the same L2 (no VLAN
  hop, no mDNS reflector, multicast snooping off on the switch). Commissioning
  most devices needs BLE: a Bluetooth adapter on the Jarvis box (`--ble`) or
  the `--ble-proxy` path.
- *Border router.* OpenThread `ot-br-posix` v2026.08.0 (BSD-3-Clause),
  image `openthread/border-router`, run with `--network=host --cap-add=NET_ADMIN
  --device=/dev/ttyACM0 --device=/dev/net/tun` and sysctls
  `net.ipv6.conf.all.forwarding=1`. Radio: Home Assistant Connect ZBT-1 (ex
  SkyConnect) or any Silicon Labs EFR32 with RCP firmware. Use a *second*
  dongle for Thread; sharing one radio between Zigbee and Thread
  ("multiprotocol") is deprecated by both projects.

**Verdict.** Worth a `matter` integration (websocket client, map clusters
OnOff/LevelControl/ColorControl/Thermostat/DoorLock to Jarvis domains), but it
is the most operationally fragile path in this note; do it after MQTT and
ESPHome are solid.

## 2. Wi-Fi devices

### 2.1 ESPHome — the DIY sensor platform

Two transports, both local:

- **Native API** (port 6053, protobuf over TCP, `Noise_NNpsk0_25519_ChaChaPoly_SHA256`
  with a 32-byte PSK; password auth was removed in ESPHome 2026.1). Client:
  `aioesphomeapi` v46.2.0, MIT. `APIClient(host, 6053, noise_psk=...)`,
  `list_entities_services()`, `subscribe_states(cb)`. The device is the server,
  so no broker is needed and messages are ~1/10 the size of MQTT JSON. Two
  things only the native API gives: the **Bluetooth proxy**
  (`subscribe_bluetooth_le_advertisements` / `_raw_advertisements` — every
  ESP32 in the house becomes a BLE receiver for §3) and **voice satellites**.
  Caveat: a device with `api:` configured and *no* client connected reboots
  every 15 min by default (`reboot_timeout`) — set it to `0s` if Jarvis is not
  the client yet.
- **MQTT** (`mqtt:` component publishes HA discovery to `<prefix>/<component>/<node>/<object>/config`).
  Zero Jarvis code, works today once the broker is reachable from the LAN.

**Plug-in plan.** MQTT now; an `esphome` integration (aioesphomeapi + mDNS
`_esphomelib._tcp` discovery + `ReconnectLogic`) as the first native bridge,
because it unlocks BLE presence and satellites in one dependency.

### 2.2 Tasmota

Tasmota removed the `homeassistant/`-prefixed discovery ("SetOption19 1") from
all builds; the only discovery left is native: retained JSON on
`tasmota/discovery/<MAC>/config` (device name `dn`, friendly names `fn`, host
`hn`, `ip`, module `md`, LWT payloads `ofln`/`onln`, `state` strings, topic `t`,
full-topic `ft`, prefixes `tp`, relays `rl`, light subtype `lt_st`, version
`sw`, setoptions `so`) and `tasmota/discovery/<MAC>/sensors` (`sn` block). State
is on `tele/<topic>/STATE` and `SENSOR`, commands on `cmnd/<topic>/POWER1`.
**Cost:** a ~200-line translator in `mqtt/` that turns those two topics into
the switch/light/sensor entities the discovery layer already builds — or the
hand-written `mqtt.switch` YAML the deployed config already shows commented
out. The discovery docstring's claim that "Tasmota publishes HA discovery" is
out of date.

### 2.3 Shelly

- **Gen1** (Shelly 1/1PM/2.5/EM/Plug S, pre-2021): CoAP/CoIoT push + HTTP
  (`/status`, `/relay/0?turn=on`), MQTT on `shellies/<id>/relay/0[/command]`,
  `.../relay/0/power`. No discovery; `rest:` or YAML `mqtt.switch` covers them.
- **Gen2+** (Plus/Pro/Gen3/Gen4): JSON-RPC 2.0 on `GET /rpc/<Method>?params`,
  `POST /rpc`, websocket `ws://<ip>/rpc` (a client with a `src` field receives
  `NotifyStatus`), an *outbound* websocket the device opens to a server, and
  MQTT: requests `<id>/rpc`, notifications `<id>/events/rpc`, per-component
  status `<id>/status/<component>` when "Generic status update over MQTT"
  (`status_ntf`) is on, LWT `<id>/online`. Status JSON for a PM relay:
  `{"id":0,"output":false,"apower":0,"voltage":225.9,"current":0,"aenergy":{"total":11.679,...}}`.
  Digest auth optional; at most 6 concurrent non-persistent channels. **Shelly
  does not publish HA discovery**; HA users run `ha-shellies-discovery-gen2`
  (a python_script) or an on-device Shelly script to synthesise it.
- **Library:** `aioshelly` 13.32.0, Apache-2.0 — Gen1 CoAP + Gen2 RPC/ws in one
  package, used by HA.

**Plug-in plan.** A `shelly` integration on the MQTT side is cheapest: subscribe
`+/status/#` and `+/events/rpc`, publish `Switch.Set` to `<id>/rpc`; every field
name is stable and documented. Power metering (`apower`, `aenergy.total`,
`em:0` on the 3EM) comes for free, which matters for §4.

### 2.4 Tuya (local)

`tinytuya` v1.20.0, MIT, protocols 3.1–3.5 (3.5 = AES-128-GCM, session key
negotiated from the local key). The **local key must be fetched once from the
Tuya IoT cloud** with the wizard (`python -m tinytuya wizard`, needs a Tuya
developer account) and changes if the device is re-paired. DP numbers per
model are the other half of the work; `make-all/tuya-local` (MIT, 2026.8.0)
is HA-only as an integration but its `custom_components/tuya_local/devices/*.yaml`
DP database is reusable data. Cost: a real integration (device polling, DP
maps, key management UI). Benefit: the cheap plugs/bulbs/heaters that are
otherwise cloud-only. Lower priority than flashing them to Tasmota/ESPHome
where possible.

### 2.5 WLED / Hue (exist)

Keep. Optional upgrades: Hue v2 `GET /eventstream/clip/v2` with
`hue-application-key` (SSE, HTTP/2 recommended, 1 s event batching) replaces
the 15 s poll; `aiohue` 4.9.0 (Apache-2.0) does that if a dependency is
preferred over `httpx` streaming. WLED `/ws` gives push state.

### 2.6 TP-Link Kasa / Tapo

`python-kasa` 0.10.2. **GPL-3.0** — the one library in this note whose licence
would bind `jarvis-core` if imported; run it as a subprocess bridge or skip.
UDP broadcast discovery (9999 legacy IOT, 20002 SMART), KLAP transport; Tapo
and newer Kasa require the TP-Link account e-mail/password for the *local*
handshake. Energy monitoring on HS110/KP115/P110.

### 2.7 Govee (LAN API)

UDP: scan `{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}` to
multicast `239.255.255.250:4001`, devices answer to the sender's 4002, commands
(`turn`, `brightness`, `colorwc`, `devStatus`) to device port 4003 *(command
names unverified against the current Govee doc)*. "LAN Control" must be toggled
per device in the Govee app; only a published model list supports it;
multicast is unreliable over many Wi-Fi APs, so unicast to known IPs is the
robust mode. Easiest: `wez/govee2mqtt` (MIT) as a compose service — it speaks
the LAN API and publishes HA discovery, so Jarvis needs nothing.

### 2.8 Media: Sonos, Chromecast, TVs, AVRs

Jarvis has a `media_player` domain with no provider. In order of value:

| Device | Library | Licence / ver | Transport | Push? | Notes |
|---|---|---|---|---|---|
| Sonos | `soco` | MIT 0.31.2 | UPnP/SOAP on 1400, SSDP discovery | UPnP event subscriptions — needs an HTTP callback listener on the Jarvis host (fine under host networking) | Grouping, TTS playback of a local URL (Jarvis's Piper output) |
| Chromecast / Nest speakers / Android TV | `pychromecast` | MIT 14.0.10 | mDNS `_googlecast._tcp`, protobuf over TLS 8009 | yes, status listeners | Also a TTS sink |
| Roku | plain HTTP (ECP on 8060: `/query/device-info`, `/keypress/Home`, `/launch/<appid>`) | `python-roku` BSD-3 is unmaintained (2019) — use `httpx` | HTTP | no (poll `/query/media-player`) | |
| LG webOS | `aiowebostv` | Apache-2.0 0.9.2 | wss 3001 (pairing prompt → client key) | yes | Power-on needs Wake-on-LAN |
| Samsung Tizen | `samsungtvws` | LGPL-3.0 3.0.5 | wss 8002 with token, REST 8001 | limited | Power-on needs WoL; LGPL is import-safe |
| Denon/Marantz AVR | `denonavr` | MIT 1.3.3 | HTTP + telnet 23 for events | yes (telnet) | |
| Yamaha MusicCast | `aiomusiccast` | MIT | YXC HTTP on 80 (`/YamahaExtendedControl/v1/...`, no auth) + UDP events | yes | older RX-V: `rxv` (YNCA) |

## 3. Presence and people

Answering "is anyone home" and "which room am I in" locally is three layers,
each cheap on its own; the fusion is the Jarvis-side work.

### 3.1 Home / away: Wi-Fi presence (phones)

- **UniFi**: `aiounifi` v93 (MIT). Local endpoints on the gateway:
  `GET /proxy/network/api/s/<site>/stat/sta` (connected clients, needs a local
  admin account) and the events websocket `/proxy/network/wss/s/<site>/events`
  for join/leave pushes. The newer official Integration API uses an
  `X-API-KEY` header on `https://<gateway>/proxy/network/integration/v1/...`
  (sites, devices, clients) *(endpoint names unverified — the developer page
  is behind a 403)*.
- **OpenWrt**: `ubus call iwinfo assoclist '{"device":"wlan0"}'` or
  `ubus call hostapd.wlan0 get_clients` over SSH, or JSON-RPC via
  `uhttpd-mod-ubus` with an ACL that grants only `iwinfo.assoclist`.
- **Any router**: `arp-scan --localnet` / `nmap -sn` / `ip neigh` from the
  Jarvis box; needs `CAP_NET_RAW` and is blind to a phone in deep sleep.

All three end the same way: a poller calls `device_tracker.see(dev_id,
location_name="home")` and `person.*` does the rest (already implemented).
Caveats to encode: phones drop Wi-Fi in Doze, so keep a "consider home" grace
of 3–5 min; iOS/Android per-SSID private MACs are stable per network by
default but "rotating" mode will break tracking; the phone's *own* companion
socket to Jarvis (`device_control`) is also a presence signal and costs nothing.

### 3.2 Room: BLE

- **ESPresense** (firmware v4.0.6, AGPL-3.0 — irrelevant to Jarvis, it is on
  the ESP). Standalone, MQTT-only, no HA required. Publishes
  `espresense/rooms/<room>/status|telemetry`, and with `pub_devices=ON` a
  non-retained JSON per sighting on `espresense/devices/<device-id>/<room>`
  (`id`, `name`, `rssi`, `distance`, `mac`, ...). Settings are written to
  `espresense/rooms/<room>/<key>/set` (`max_distance`, `known_irks`,
  `enroll`). It fingerprints iBeacon/Eddystone, Apple devices via IRK,
  Xiaomi, Tile, and does inter-node distance with per-node iBeacons. HA
  discovery is published too, but only for the node's count sensor — the
  per-device room data needs a small subscriber in Jarvis that picks the
  nearest room per id.
- **Bermuda** (v0.8.7, MIT) is better tuned but is a Home Assistant *custom
  integration* on top of HA's Bluetooth stack — not reusable outside HA. Its
  input, though, is exactly what the ESPHome native API gives
  (`subscribe_bluetooth_le_advertisements` from every `bluetooth_proxy:` node),
  so a Jarvis `esphome` integration plus ~200 lines of "nearest node by
  filtered RSSI" reproduces it without dedicated hardware.
- **ESPHome-only**: `ble_presence` (by `mac_address`, `irk`, `service_uuid` or
  `ibeacon_uuid`) and `ble_rssi` per node — no server logic, one binary sensor
  per person per room, exported over MQTT discovery. Crude but zero code.

The phone side: Android can advertise an iBeacon or expose its IRK; the Jarvis
app does neither today. Until it does, a €10 tag or a smartwatch is the beacon.

### 3.3 Occupancy: mmWave and PIR

- ESPHome `ld2410` (UART 256000 baud; presence + still + moving, 6 m, ±60°)
  and `ld2450` (up to 3 tracked targets with x/y/speed, 8 m; firmware
  ≥ V2.02.23090617). Zone → room occupancy without any personal identifier.
- Zigbee mmWave (Aqara FP1E, Tuya ZY-M100 and clones) via Z2M, discovered
  as `binary_sensor` `occupancy`.
- Both feed the same `binary_sensor.<room>_occupancy` shape; combined with
  §3.2 they turn "someone is in the study" into "Chris is in the study".

### 3.4 The fused answer

`person.chris = home` (Wi-Fi/companion socket) ∧ `sensor.chris_room = study`
(ESPresense nearest-room, 30 s hysteresis) ∧ `binary_sensor.study_occupancy =
on` (mmWave). Each is an ordinary entity; a template or a 50-line
`presence_fusion` helper keeps `person.<x>.attributes.room` current. Nothing
leaves the LAN.

## 4. Energy and utilities

What is realistic locally, most to least common:

| Source | Local path | Library / bridge | Effort |
|---|---|---|---|
| Zigbee plugs with metering | Z2M discovery (`power`, `energy`, `voltage`) | none | 0 |
| Shelly Plus PM / EM / Pro 3EM | MQTT `<id>/status/switch:0` (`apower`, `aenergy.total`), `<id>/status/em:0` (`a_act_power`…`total_act_power`), `emdata:0` totals | §2.3 shelly bridge, or `rest:` polling `/rpc/EM.GetStatus?id=0` | small |
| P1 smart meter (NL/BE/SE/…) | HomeWizard P1: `GET http://<ip>/api/v1/data` → `active_power_w`, import/export kWh, gas m³; 1 s updates on DSMR5; enable "local API" in the app. ESPHome `dsmr` component on a €10 board. USB P1 cable + `dsmr_parser` 1.11.2 (MIT) → `POST /api/sensor/...` | `rest:` block, or the sensor ingest | 0–small |
| Tibber Pulse IR (SML meters) | bridge `http://<bridge>/data.json?node_id=1` with Basic auth, SML payload; `tibber-local-lib` decodes | script → sensor ingest | small |
| S0 pulse output / optical pulse | ESPHome `pulse_meter` (50–100 ms filter) with `total` → kWh | none | 0 |
| Solar inverters | SunSpec Modbus TCP 502 (SolarEdge, Fronius, SMA, Huawei, Solis, GoodWe…): `pysunspec2` 1.3.6 (Apache-2.0) or `pymodbus` 3.15.0 (BSD-3). Fronius also `GET /solar_api/v1/GetPowerFlowRealtimeData.fcgi` — **disabled by default since GEN24 firmware 1.14.1**, enable under Communication → Solar API | a `modbus` integration (generic register → sensor, like HA's) covers inverters, meters and heat pumps at once | medium |
| EV charger | OCPP 1.6J: Jarvis (or evcc) is the *central system* — a websocket server the charger dials, subprotocol `ocpp1.6`; `mobilityhouse/ocpp` 2.1.0 (MIT) implements 1.6 and 2.0.1. Wallbox Pulsar Plus, ABB Terra, go-e, Easee all speak it. go-e also has a local HTTP API v2 | OCPP server ~400 lines, or evcc | medium |
| Whole-house energy manager | **evcc** 0.314.3 (MIT, Go): hundreds of chargers/meters/inverters/batteries/heat pumps over Modbus/HTTP/MQTT/OCPP; publishes `evcc/site/pvPower`, `evcc/site/grid/power`, `evcc/site/battery/soc`, `evcc/site/homePower`, `evcc/loadpoints/1/chargePower`; writable `.../mode/set`, `minSoc/set`. No HA discovery documented → a YAML `mqtt.sensor` block | compose service + YAML | small |
| Heat pumps / boilers | Nibe S-series: built-in Modbus TCP; F-series via MODBUS40 (`nibe-mqtt`). Daikin Altherma via EKRHH Modbus. Vaillant/Saunier Duval: **ebusd** 26.1 (GPL-3.0, separate process) with `--mqttint=/etc/ebusd/mqtt-hassio.cfg --mqttjson` → HA discovery, zero Jarvis code; needs an eBUS adapter (Adapter 3 / Stick C6). Solarfocus: `pysolarfocus` | compose service, or the `modbus` integration | small–medium |

The honest limits: proprietary-cloud-only heat pumps (Daikin Onecta,
Mitsubishi MELCloud) and most inverters' *battery control* stay cloud or
Modbus-write-with-risk; dynamic tariffs are inherently remote data. Everything
in the table above is read, and mostly written, without leaving the LAN.

## 5. Recommendation for this repo

### 5.1 Ingestion paths, minimal set, in build order

1. **MQTT discovery (exists) — harden it.** Add `device_tracker` and `event`
   components; publish the birth on `homeassistant/status` as well; widen
   Mosquitto to the LAN behind a password file and ufw (the conf already
   spells out the commit). This single path then covers Zigbee2MQTT, Z-Wave JS
   UI, ESPHome-over-MQTT, ESPresense, govee2mqtt, ebusd, evcc, and the
   Shelly/Tasmota translators below. Roughly 80 % of what a house contains.
2. **Bridges as compose profiles.** `zigbee` (Z2M), `zwave` (ZUI), `thread`
   (OTBR + matterjs-server), `energy` (evcc), `ebus` (ebusd), `govee`
   (govee2mqtt). Each is one container, host network, one volume, one
   `devices:` line. Test the way `test_packaging.py` pins the others: profile
   names, image tags, no `ports:` on host network.
3. **Two MQTT translators in `mqtt/`**: Tasmota native discovery
   (`tasmota/discovery/#`) and Shelly Gen2 status (`+/status/#`, `+/events/rpc`,
   `<id>/rpc`). No new dependencies; both are pure topic→entity mappings the
   existing `MqttEntity` classes can host.
4. **`esphome` native integration** (`aioesphomeapi`, MIT): entities without a
   broker, BLE proxy for room presence, voice satellites later.
5. **`presence` pollers**: UniFi (`aiounifi`), OpenWrt (`ubus` over SSH), ARP
   — all ending in `device_tracker.see`; plus an ESPresense/BLE-proxy nearest-room
   reducer that writes `person.<x>` room attributes.
6. **`modbus` integration** (`pymodbus`, BSD-3): generic register maps in YAML,
   with SunSpec (`pysunspec2`) as a model-aware layer — inverters, meters, heat
   pumps in one dependency.
7. **`media` providers**: `soco` and `pychromecast` first (both MIT, both
   push, both double as TTS sinks for `companion.notify`), then TVs/AVRs.
8. **`matter` integration** (websocket client to matterjs-server on 5580) —
   last, because of the IPv6/mDNS/BLE operational surface.

Not recommended in-process: `zigpy`/`zha` (GPL-3.0), `python-kasa` (GPL-3.0),
`localtuya`/`tuya-local`/Bermuda (HA-only). Tuya via `tinytuya` is fine but
low value per hour.

### 5.2 Coverage after step 3 vs. after step 8

| Device class | After 1–3 (MQTT only) | After 4–8 |
|---|---|---|
| Zigbee / Z-Wave everything | yes | yes |
| ESPHome sensors, mmWave, P1, S0 | yes (MQTT firmware) | yes, plus BLE proxy + satellites |
| Tasmota, Shelly, Govee, ebusd, evcc | yes | yes |
| Hue, WLED | yes (exists) | push instead of poll |
| Home/away presence | no | yes (router pollers, companion socket) |
| Room presence | ESPresense only | ESPresense or BLE-proxy fusion |
| Inverters, meters, heat pumps on Modbus | via evcc only | native `modbus` |
| Sonos, Cast, TVs, AVRs | no | yes |
| Matter/Thread | no | yes |
| Kasa/Tapo, Tuya | no | optional bridges |

### 5.3 LLM tools and verify scenarios the house needs

Tools are the existing domain services plus a few read-side aggregations; the
value is in the scenario table, which is what `tests/` and the eval should
pin. Each row is testable offline with `FakeMqttClient` and the mock console
backend (`tests/web/mock-ha.mjs`) once the entity shapes exist.

| Utterance | Tool | Data path | Pass condition |
|---|---|---|---|
| "What's the power draw right now?" | `house_power(scope=house\|area\|entity)` | grid meter (P1/3EM/evcc `site/homePower`) if present, else Σ `device_class: power` sensors; report source and staleness | answer names the source; a sensor older than `expire_after` is excluded and said so |
| "How much energy did we use today / what did the heat pump cost?" | `energy_summary(period, entity?)` | `recorder` deltas on `device_class: energy` `state_class: total_increasing` | matches recorder sum ±1 %; no tariff → say so rather than invent |
| "Who is home?" | `who_is_home()` | `person.*` state + `attributes.room` + last-seen | lists persons with state and room; "unknown" when no tracker is configured, never "nobody" |
| "Which room am I in?" | `where_am_i(person)` | ESPresense / BLE-proxy nearest room, mmWave corroboration | returns room + confidence + age; refuses to guess when no beacon |
| "Turn off everything upstairs" | `homeassistant.turn_off(area_id=[upstairs areas])` | `resolve_targets` over areas; `lock` excluded by design | every `light/switch/fan/media_player/cover` in those areas off; locks untouched; reply lists what changed and what failed |
| "Is the back door locked / lock it" | `lock.lock` at CONFIRM tier | Z-Wave/Zigbee lock via discovery | state verified from the lock's own state topic, not assumed |
| "Pair the new sensor" | `zigbee_permit_join(seconds)` | `zigbee2mqtt/bridge/request/permit_join` → response `status:ok`; new `bridge/event` `device_interview` | announces the device when it joins, times out honestly |
| "Set the study to 21°" | `climate.set_temperature` | Z2M TRV / Modbus heat pump register | read-back equals set-point within device precision |
| "Is anything still on in the garage?" | `list_on(area)` | entity registry filter | correct with unavailable entities flagged |
| "Play the news in the kitchen" | `media_player.play_media` | Sonos/Cast provider | speaker reports `playing` within 5 s |
| "Is the car charging / stop charging" | `charger_status`, `loadpoint_mode(off)` | OCPP `MeterValues` or `evcc/loadpoints/1/...` + `/mode/set` | state read from the charger after the command |
| "Alert me if the freezer goes above −15°" | `automation.create` from tool | Zigbee temp sensor + `companion.notify` | fires once per excursion; survives restart |

Two rules already in the tree carry over unchanged: anything a device *reports*
is data, not an instruction (the untrusted-content bar in `device_control`
applies to sensor names and MQTT payloads too, since anyone on the broker can
publish them), and no tool lowers a tier — a lock or a charger command asks.

### 5.4 Costs at a glance

| Item | Hardware | RAM / CPU (idle) | New Python dependency |
|---|---|---|---|
| Zigbee2MQTT | €20–40 dongle | ~150 MB | none |
| Z-Wave JS UI | €30–50 stick | ~200 MB | none (`zwave-js-server-python` optional) |
| OTBR + matterjs-server | €25 ZBT-1 + host BLE | ~50 MB + ~300 MB | none (ws client) |
| ESPHome nodes | €5–15 each | — | `aioesphomeapi` (MIT) |
| ESPresense nodes | €8–12 each | — | none |
| evcc | — | ~50 MB | none |
| ebusd | €30–60 adapter | ~20 MB | none |
| Modbus | RS-485 adapter if RTU | — | `pymodbus` (BSD-3), `pysunspec2` (Apache-2.0) |
| Media | — | — | `soco`, `pychromecast` (MIT) |

## Sources

Zigbee
- Zigbee2MQTT releases (2.13.0, 1 Aug 2026; Node 24/26): https://github.com/Koenkk/zigbee2mqtt/releases
- Zigbee2MQTT 2.0.0 breaking changes (status_topic default, HA ≥ 2024.9, extended identifiers): https://github.com/Koenkk/zigbee2mqtt/releases/tag/2.0.0
- Zigbee2MQTT Home Assistant integration settings (`legacy_action_sensor`, `experimental_event_entities`, `status_topic`): https://www.zigbee2mqtt.io/guide/configuration/homeassistant.html and https://www.zigbee2mqtt.io/guide/usage/integrations/home_assistant.html
- Zigbee2MQTT adapters (EmberZNet, zStack, deCONZ; Wi-Fi warning): https://www.zigbee2mqtt.io/guide/adapters/
- Zigbee2MQTT MQTT topics, permit_join: https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html, https://www.zigbee2mqtt.io/guide/usage/pairing_devices.html
- Supported devices count: https://www.zigbee2mqtt.io/supported-devices/
- zigpy (GPL-3.0, 2.1.0): https://github.com/zigpy/zigpy; zha library: https://github.com/zigpy/zha

Z-Wave
- Z-Wave JS UI (MIT, v11.22.3): https://github.com/zwave-js/zwave-js-ui; MQTT discovery doc: https://github.com/zwave-js/zwave-js-ui/blob/master/docs/homeassistant/homeassistant-mqtt.md
- zwave-js-server protocol (`version`, `initialize`, `start_listening`): https://github.com/zwave-js/zwave-js-server/blob/master/README.md
- zwave-js-server-python (Apache-2.0, 0.73.1): https://github.com/home-assistant-libs/zwave-js-server-python
- Zooz ZST39 LR: https://www.getzooz.com/zooz-zst39-z-wave-long-range-usb-stick/

Thread / Matter
- python-matter-server final release notice (8.1.2): https://github.com/matter-js/python-matter-server
- python-matter-server websocket API (`start_listening`, `commission_with_code`, `read_attribute`, `device_command`): https://github.com/matter-js/python-matter-server/blob/main/docs/websockets_api.md
- matterjs-server (Apache-2.0, v1.4.0, port 5580, drop-in): https://github.com/matter-js/matterjs-server
- Matter networking requirements (IPv6, mDNS, same LAN): https://www.home-assistant.io/integrations/matter/
- OpenThread border router docker: https://openthread.io/guides/border-router/build-docker; ot-br-posix (BSD-3, v2026.08.0): https://github.com/openthread/ot-br-posix

MQTT discovery format
- Home Assistant MQTT discovery (single-component and device discovery, `o` required, `cmps`, birth `homeassistant/status`): https://www.home-assistant.io/integrations/mqtt/

Wi-Fi devices
- ESPHome native API (port 6053, encryption key, `reboot_timeout` 15 min): https://esphome.io/components/api/; MQTT component: https://esphome.io/components/mqtt/
- aioesphomeapi (MIT, v46.2.0; `subscribe_states`, `subscribe_bluetooth_le_advertisements`): https://github.com/esphome/aioesphomeapi
- Tasmota Home Assistant page (HA MQTT discovery removed; `tasmota/discovery`): https://tasmota.github.io/docs/Home-Assistant/
- Shelly Gen2 RPC channels (HTTP/ws/MQTT topics, digest auth, 6-channel limit): https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/; MQTT component: https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Mqtt/; Switch status JSON: https://shelly-api-docs.shelly.cloud/gen2/0.14/ComponentsAndServices/Switch/
- aioshelly (Apache-2.0, 13.32.0): https://github.com/home-assistant-libs/aioshelly
- ha-shellies-discovery-gen2 (HA-side discovery script): https://github.com/bieniu/ha-shellies-discovery-gen2
- tinytuya (MIT, v1.20.0, protocol 3.5): https://github.com/jasonacox/tinytuya, https://github.com/jasonacox/tinytuya/blob/master/PROTOCOL.md
- tuya-local (MIT, 2026.8.0): https://github.com/make-all/tuya-local
- python-kasa (GPL-3.0, 0.10.2): https://github.com/python-kasa/python-kasa
- Govee LAN API (ports 4001/4002/4003, multicast 239.255.255.250): https://github.com/wez/govee2mqtt/blob/main/docs/LAN.md, https://www.openhab.org/addons/bindings/govee/
- Hue v2 eventstream: https://iotech.blog/posts/philips-http2/; aiohue (Apache-2.0, 4.9.0): https://github.com/home-assistant-libs/aiohue

Media
- SoCo (MIT, 0.31.2): https://github.com/SoCo/SoCo
- pychromecast (MIT, 14.0.10): https://github.com/home-assistant-libs/pychromecast
- Roku ECP: https://developer.roku.com/dev/docs/external-control-api; python-roku (BSD-3, 2019): https://github.com/jcarbaugh/python-roku
- aiowebostv (Apache-2.0, 0.9.2): https://github.com/home-assistant-libs/aiowebostv
- samsungtvws (LGPL-3.0, 3.0.5): https://github.com/xchwarze/samsung-tv-ws-api
- denonavr (MIT, 1.3.3): https://github.com/ol-iver/denonavr
- Yamaha Extended Control (port 80, no auth): https://musiccast2mqtt.readthedocs.io/en/latest/musiccast_doc.html

Presence
- ESPresense MQTT topics and settings (AGPL-3.0 firmware, v4.0.6): https://espresense.com/configuration/mqtt, https://espresense.com/configuration/settings, https://github.com/ESPresense/ESPresense
- Bermuda (MIT, v0.8.7, HA custom integration): https://github.com/agittins/bermuda
- ESPHome `ble_presence`, `esp32_ble_tracker`: https://esphome.io/components/binary_sensor/ble_presence/, https://esphome.io/components/esp32_ble_tracker/
- ESPHome LD2410 / LD2450: https://esphome.io/components/sensor/ld2410/, https://esphome.io/components/sensor/ld2450/
- aiounifi (MIT, v93): https://github.com/Kane610/aiounifi; UniFi official API: https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API
- OpenWrt ubus presence: https://github.com/FUjr/homeassistant-openwrt-ubus

Energy
- HomeWizard local API v1 (`/api/v1/data`): https://api-documentation.homewizard.com/docs/v1/measurement/
- ESPHome DSMR: https://esphome.io/components/sensor/dsmr/; dsmr_parser (MIT, 1.11.2): https://github.com/ndokter/dsmr_parser
- Tibber Pulse local (`data.json`, Basic auth): https://github.com/marq24/ha-tibber-pulse-local, https://pypi.org/project/tibber-local-lib/
- ESPHome pulse counter / power meter cookbook: https://esphome.io/components/sensor/pulse_counter/, https://esphome.io/cookbook/power_meter/
- Shelly Pro 3EM: https://shelly-api-docs.shelly.cloud/gen2/0.14/Devices/ShellyPro3EM/
- pysunspec2 (Apache-2.0, 1.3.6): https://github.com/sunspec/pysunspec2; pymodbus (BSD-3, 3.15.0): https://github.com/pymodbus-dev/pymodbus
- Fronius Solar API disabled by default ≥ 1.14.1: https://www.home-assistant.io/integrations/fronius/; Fronius Solar API v1 spec: https://www.fronius.com/~/downloads/Solar%20Energy/Operating%20Instructions/42,0410,2012.pdf
- SolarEdge SunSpec technical note: https://knowledge-center.solaredge.com/sites/kc/files/sunspec-implementation-technical-note.pdf
- mobilityhouse/ocpp (MIT, 2.1.0; OCPP 1.6 + 2.0.1): https://github.com/mobilityhouse/ocpp; Wallbox OCPP: https://support.wallbox.com/en/knowledge-base/ocpp-activation-and-setup-guide/
- evcc (MIT, 0.314.3) and its MQTT API: https://github.com/evcc-io/evcc, https://docs.evcc.io/en/integrations/mqtt-api
- ebusd (GPL-3.0, 26.1) HA discovery via `mqtt-hassio.cfg`: https://github.com/john30/ebusd, https://github.com/john30/ebusd/discussions/518
- Nibe S-series Modbus TCP: https://www.home-assistant.io/integrations/nibe_heatpump/; nibe-mqtt: https://pypi.org/project/nibe-mqtt/
- Daikin EKRHH Modbus: https://community.home-assistant.io/t/daikin-ekrhh-home-hub-local-modbus-integration/800962
