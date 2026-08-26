# Vision and cameras — local options for Jarvis

*Research note, 26 August 2026. Nothing in this document was deployed; the
"measured" figures are this checkout's host and the running containers, the
rest is upstream documentation, read on the date above and listed under
Sources. Versions and licences are as published on that date.*

The ask: Jarvis should have "vision through cameras", entirely on the
operator's own hardware, no cloud. This note covers (1) how frames get from a
camera to Jarvis, (2) which vision-language models the existing model server
can serve and what they cost, (3) detection and face recognition without a
language model, and (4) how the pieces fit a house assistant — with what the
repository already has, what it lacks, and a recommended shape at the end.

---

## 0. The short version

- **Most of the assistant side already exists.** `jarvis-core/jarvis/integrations/vision/`
  is a complete, tested (106 tests) "look through a camera and answer a
  question" integration with consent, fencing, audit and rate limiting. It is
  not enabled in the deployed config, and it can only talk to **Ollama**, which
  this deployment does not run.
- **The one code change that unlocks everything** is teaching `vision` the
  OpenAI wire (`/v1/chat/completions` with an `image_url` content part), so it
  can use the same `LLM_URL` → LiteLLM → llama-swap path as the chat model, and
  putting `vision`'s model URL under the same `local_only` guard as `llm`.
- **Ingest: go2rtc first, Frigate second.** go2rtc (MIT, one small binary)
  turns any RTSP/ONVIF/USB camera into a `frame.jpeg` URL the existing `still`
  platform reads with no code change. Frigate (MIT) adds the thing `vision`
  cannot do — *events* — at the cost of a real NVR container that this 4-vCPU
  box can carry for one to three cameras on its substreams.
- **VLM: 4B-class on the 3090 host, sub-1B on this CPU.** Qwen3-VL-4B
  (Apache-2.0, ~3.3 GB at Q4 + F16 projector) on llama-swap is the quality
  choice; SmolVLM-500M (Apache-2.0, 0.55 GB) or LFM2-VL-1.6B (LFM licence,
  1.5 GB) are the only ones that answer in seconds on four cores.
- **Do not build a detector or face service in this repo.** The good detectors
  are either AGPL (Ultralytics) or need binary wheels the pure-Python image
  rule forbids; Frigate already runs OpenVINO on this CPU and has face
  recognition built in. InsightFace's models are non-commercial-only.

---

## 1. What the repository already has

### 1.1 The `vision` integration

`jarvis-core/jarvis/integrations/vision/` (2 783 lines across five modules;
`jarvis-core/docs/vision.md` is the operator document and is accurate).

| piece | where | what it does |
|---|---|---|
| four camera sources | `camera.py` | `still` (one HTTP GET), `mjpeg` (read one JPEG off a stream and hang up), `rtsp` (shell out to `ffmpeg -frames:v 1` over TCP), `mqtt` (last base64 frame on a topic). Every path has a whole-fetch deadline and a byte cap; frames live in memory for `frame_ttl` (30 s) and hit disk only via `camera.snapshot` with an explicit filename inside the config dir. |
| the model call | `analyze.py` | Downscale to `max_edge` (needs Pillow), JPEG-re-encode, base64, **one POST to Ollama's `/api/chat`** with the image in the message's `images` list. System prompt tells the model to quote text in shot rather than obey it. |
| consent | `consent.py` | `always` / `ask` / `never` per camera; `ask` goes through `companion.ask` and only an explicit yes fetches a frame; `never` makes no request and takes no rate-limit slot. |
| fencing | `fence.py` | Every description comes back inside `<untrusted_camera_content>`; content cannot close its own fence; a question that *arrives* fenced is refused before any fetch. `mark_untrusted_result` raises the turn's tier so a later device action needs confirmation (`test_device_control.py::test_every_integration_that_fences_content_also_raises_the_tier`). |
| audit + events | `__init__.py` | `vision.audit` records every look and refusal (no frames, no descriptions); `vision_look_started/finished/denied` go on the bus. The console already renders them: `jarvis-web/src/lib/activity.svelte.ts:133-159` draws a `camera` activity row and `lookingCaption()` puts "looking" under the reactor. |
| rate limiting | `__init__.py` | `min_interval` 10 s and `max_per_hour` 60 per camera, `max_concurrent` 2; the *attempt* spends the slot, including refused ones. |
| services / tools | `__init__.py` | `vision.look` → `look_at_camera`, `vision.describe_change` → `describe_camera_change`, `vision.list_cameras` → `list_cameras`, `vision.audit`, `camera.snapshot` (deliberately **not** a tool). Entities `camera.<name>` with `idle`/`streaming`/`unavailable`. |

Neighbouring pieces the rest of this note leans on:

- `automation` has an `mqtt` trigger platform (`jarvis-core/jarvis/automation/triggers.py:941`; keys `topic`, `payload`, `value_template`) and an `event` trigger, so an NVR's MQTT event can start a Jarvis automation today.
- `companion.notify` / `companion.ask` deliver to whichever device the user is at; `notifications.add` keeps the record the console and phone show afterwards.
- `mqtt.publish` exists; the broker profile (`eclipse-mosquitto:2`) is **loopback-only and anonymous by design** (`jarvis-core/mosquitto/mosquitto.conf`), which is fine for an NVR in `network_mode: host` on the same box and rules out one on another box without adding a password file.
- `llm.local_only: true` refuses a model URL that resolves off-LAN (`configuration.yaml:177-187`).

### 1.2 What is missing

1. **Not enabled.** `jarvis-core/config/configuration.yaml` has no `vision:` block, and the example house config has none either. Zero cameras are configured.
2. **Wrong wire for this deployment.** `VisionModel` speaks only Ollama's native `/api/chat`. The deployed stack has no Ollama: `jarvis-core/.env` sets `LLM_URL=http://<host>:4000/v1` (the LiteLLM `jarvis-gateway` container) and `GATEWAY_UPSTREAM_URL=https://<host>/v1` (llama-swap on the 3090 box over the tailnet — `docs/TOOLING_DECISIONS.md`, `BLOCKERS.md` §2). `jarvis/llm/openai_compat.py` has no multimodal content-part support (`grep image_url` finds nothing), so there is no OpenAI-wire path for an image anywhere in `jarvis-core` yet.
3. **`local_only` does not cover vision.** `vision.ollama_url` is a separate setting and nothing in `vision/` consults `llm.local_only` (`grep local_only vision/*.py` is empty). Pointing it at a cloud vision endpoint would not be refused.
4. **The shipped container cannot do RTSP or downscale.** `jarvis-core/Dockerfile` installs only `tzdata` and `curl`; verified in the running container: `which ffmpeg` → none, `import PIL` → `ModuleNotFoundError`. So `platform: rtsp` returns its "ffmpeg is not installed" error and every frame goes to the model at full camera resolution. `requirements.txt` is deliberately pure-Python (Pi builds, `test_packaging.py`), so Pillow stays an operator extra.
5. **Pull-only.** Nothing produces "a person just appeared at the door". `vision` answers questions; it does not watch. That is the gap Frigate fills.
6. **`mqtt` cameras want base64 text.** `camera.py` decodes a base64/`data:` payload; Frigate's `frigate/<camera>/<object>/snapshot` topic publishes raw JPEG bytes, which the `mqtt` integration hands over as text and mangles. Either the platform learns bytes or Frigate frames come by HTTP (they should anyway — see §3.2).
7. No go2rtc, no Frigate, no detector, no face recognition anywhere in either compose file.

### 1.3 The box this would run on

Measured on this checkout's host on 26 August 2026: 4 vCPUs of an AMD Ryzen 7
5800X (AVX2, **no AVX-512** — Zen 3), 16 GB RAM with ~10 GB available while
the stack runs, **no `/dev/dri`** (so no hardware video decode inside a
container), no GPU. `wyoming-whisper` may take three of the four cores during
a spoken turn (`docs/TOOLING_DECISIONS.md`). The chat model is `qwen3.8-27b` on
llama-swap on a separate host with 3090s, and that host is bound by the
**VRAM justification rule**: nothing gets GPU residency without a paragraph
saying what it evicts and what number it improves.

Every "can this run here" judgement below is against that box, not a
recommended NVR appliance.

---

## 2. Camera ingest

### 2.1 go2rtc — v1.9.14 (19 January 2026), MIT

**What it gives Jarvis.** One process that speaks to every camera protocol
(RTSP, RTMP, ONVIF, HTTP JPEG/MJPEG, HomeKit, V4L2/USB, vendor P2P for Tapo,
Reolink, Xiaomi, Wyze, Ring, Nest…) and republishes each stream as RTSP,
WebRTC, HLS, MP4, MJPEG and — the one that matters here — **a JPEG snapshot
URL**:

```
GET http://127.0.0.1:1984/api/frame.jpeg?src=front_door&w=1280
GET http://127.0.0.1:1984/api/stream.mjpeg?src=front_door
GET http://127.0.0.1:1984/api/streams                # list / manage
rtsp://127.0.0.1:8554/front_door                     # restream for an NVR
```

`frame.jpeg` accepts `w`/`h`, `rotate`, `hardware` and `cache=10s` (returns a
snapshot at most that old instead of decoding a new one). It exists **only
when the stream carries an MJPEG codec**; an H.264/H.265 camera needs a second
source that transcodes on demand — go2rtc starts ffmpeg when a client asks and
stops it when nobody is watching:

```yaml
streams:
  front_door:
    - rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102   # substream
    - ffmpeg:front_door#video=mjpeg                              # for frame.jpeg
  garden:      onvif://user:pass@192.168.1.71                    # ONVIF: it finds the RTSP URL itself
  workshop:    v4l2:device?video=/dev/video0&input_format=mjpeg&video_size=1280x720&framerate=10
```

ONVIF works as a client (`onvif://user:pass@host[:port]`, autodiscovery from
the WebUI needs `network_mode: host`) and as a server (Home Assistant's ONVIF
integration can consume go2rtc). USB cameras that emit MJPEG or H.264 cost
"no CPU"; RAW (YUYV) webcams get transcoded, which does.

**What it costs.** A ~20 MB Go binary; the Docker image (`alexxit/go2rtc`,
also `ghcr.io/alexxit/go2rtc`) carries ffmpeg. Per snapshot of an H.264
camera: one ffmpeg decode of the substream until a keyframe arrives, a few
hundred ms of one core; nothing while idle. Ports 1984 (API/UI), 8554 (RTSP),
8555 (WebRTC). **Its API skips authentication for localhost requests even
when a password is set**, and the README says so in bold — so it must listen
on loopback and nothing else on this box may be treated as untrusted.

Tag to pin: `alexxit/go2rtc:1.9.14` (`:latest` floats; `:master` is the
development build).

**How it plugs in.** No code change: the existing `still` platform reads the
snapshot URL. This is the whole integration —

```yaml
# jarvis-core/config/configuration.yaml
vision:
  # (see §3.1 for the model keys once the OpenAI wire exists)
  cameras:
    - name: Front Door
      platform: still
      url: http://127.0.0.1:1984/api/frame.jpeg?src=front_door&w=1280
      area: Front Porch
      consent: always
    - name: Kitchen
      platform: still
      url: http://127.0.0.1:1984/api/frame.jpeg?src=kitchen&w=1280
      area: Kitchen
      consent: ask
```

— and the `w=1280` does on the ingest side what Pillow would have done in
`analyze.py`, which matters because the container has no Pillow (§1.2).

Compose (a new profile in `jarvis-core/docker-compose.yml`, same shape as
`mosquitto` and `searxng`):

```yaml
  go2rtc:
    image: alexxit/go2rtc:1.9.14
    container_name: go2rtc
    restart: unless-stopped
    profiles: [cameras]
    network_mode: host            # ONVIF discovery and loopback-only listen both need it
    environment:
      - TZ=${TZ:-Europe/London}
    volumes:
      - ./go2rtc/go2rtc.yaml:/config/go2rtc.yaml:ro
    # devices:                    # only for a USB camera
    #   - /dev/video0:/dev/video0
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:1984/api/streams > /dev/null || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
    mem_limit: 512m
    cpus: 1.5
```

with `go2rtc.yaml` binding to loopback:

```yaml
api:    { listen: "127.0.0.1:1984" }
rtsp:   { listen: "127.0.0.1:8554" }
webrtc: { listen: "" }            # off: nothing in this stack watches live video
streams: { ... }
```

`test_packaging.py` pins compose ↔ configuration ↔ `.env.example` agreement;
a new profile with no env vars touches none of the pinned names, but the
`TZ` line must match the others.

### 2.2 Frigate — v0.17.2 (28 June 2026), MIT

**What it gives Jarvis.** A real NVR: it decodes each camera's detect stream,
runs motion detection on the CPU, sends moving regions to an object detector,
tracks objects across frames, and turns "a person entered the porch zone at
14:02 and left at 14:04" into a durable *event* with a best-shot snapshot and a
clip — published over MQTT as it happens and queryable over HTTP afterwards.
On top of that, all optional: recording with retention rules, review items
(alerts vs detections), face recognition (0.16+), licence-plate recognition,
audio events, semantic search over thumbnails (Jina CLIP), and **GenAI
descriptions and review summaries via a local model** — 0.17 has a native
`provider: llamacpp` with a `base_url`, alongside `ollama` and any
OpenAI-compatible server. It bundles its own go2rtc for restreaming.

**The HTTP API** (paths from the OpenAPI spec `docs/static/frigate-api.yaml`,
all under `/api`; the two `servers` entries confirm the prefix):

| need | endpoint |
|---|---|
| a frame right now | `GET /api/{camera}/latest.jpg?height=720&quality=70` (also `bbox=1`, `timestamp=1`, `zones=1`, `motion=1`, `regions=1`; `latest.webp` too) |
| recent events | `GET /api/events?camera=front_door&label=person&after=<epoch>&limit=5&has_snapshot=1&in_progress=1&zone=porch` (also `sub_label`, `min_score`, `time_range`, `sort`) |
| one event | `GET /api/events/{id}` |
| the event's best frame, cropped to the object | `GET /api/events/{id}/snapshot.jpg?crop=1&bbox=0&height=480&quality=70` |
| thumbnail / clip / animated preview | `GET /api/events/{id}/thumbnail.jpg`, `/api/events/{id}/clip.mp4?padding=5`, `/api/events/{id}/preview.gif` |
| review items | `GET /api/review?cameras=front_door&severity=alert&after=…`, `GET /api/review/{id}/clip.mp4` |
| a frame from the archive | `GET /api/{camera}/recordings/{frame_time}/snapshot.jpg` |
| write back | `POST /api/events/{id}/sub_label`, `/description`, `/api/events/{camera}/{label}/create` (a manual event), `PUT /api/events/{id}/end` |
| health | `GET /api/stats`, `/api/version`, `/api/config`, `/api/metrics` |
| faces | `GET /api/faces`, `POST /api/faces/recognize`, `/api/faces/{name}/register` … |

Two listeners: **8971** is authenticated (JWT — `POST /api/login`, cookie or
`Authorization: Bearer`, `session_length` default 86 400 s; roles admin /
viewer / custom with per-camera access) and **5000** is "internal
unauthenticated UI and API access. Access to this port should be limited."
Jarvis on the same box talks to 5000 on loopback; humans use 8971.

**MQTT** (`integrations/mqtt` doc; prefix `frigate` by default):

| topic | payload |
|---|---|
| `frigate/available` | `online` / `offline` |
| `frigate/events` | JSON `{type: new\|update\|end, before: {...}, after: {...}}`; each side has `id, camera, label, sub_label, zones, entered_zones, current_zones, start_time, end_time, has_clip, has_snapshot, score, top_score, false_positive, attributes` |
| `frigate/reviews` | review item changes with `severity: detection\|alert` |
| `frigate/tracked_object_update` | descriptions, face / plate recognition results, classifications (untrusted text — see §5.3) |
| `frigate/<camera>/<object>` and `…/active` | current and active object counts — a state, not an event |
| `frigate/<camera>/<object>/snapshot` | raw JPEG bytes of the best frame (does not fit Jarvis's base64 `mqtt` camera, §1.2 item 6) |
| `frigate/<camera>/motion` | `ON` / `OFF` |
| `frigate/<camera>/detect/set`, `…/recordings/set`, `…/snapshots/set`, `…/object_descriptions/set`, `…/review_descriptions/set` | `ON` / `OFF` — Jarvis can *switch things off* from an automation |
| `frigate/stats` | the `/api/stats` document |

**What it costs.** This is where honesty matters for this box.

- *Decode.* Every camera's detect stream is decoded continuously by ffmpeg.
  With no `/dev/dri` that is software H.264 decode: the docs' guidance is a
  **substream at ~1280×720 or lower at 5 fps** for detection (10 fps maximum
  "for very fast moving objects") and a separate full-resolution stream only
  if you record. Budget roughly a quarter to half a core per 720p/5 fps
  substream on this CPU; more for 1080p.
- *Detect.* The docs' CPU detector (TensorFlow Lite, `/cpu_model.tflite`) is
  marked "not recommended for general use"; their advice for a box without a
  GPU or Coral is the **OpenVINO detector in CPU mode**, which "runs on AMD and
  Intel CPUs" and "will also run on AMD CPUs despite having no official
  support for it" (needs AVX2 — this CPU has it). Community numbers on Ryzen
  CPUs: ~3 ms per inference with the default `ssdlite_mobilenet_v2`, 25–35 ms
  with YOLOv8n (discussion #9417; `device: CPU`, never `AUTO`). Detection is
  only run on motion regions, so one detector serves several cameras.
- *Memory.* `shm_size` = width × height × 1.5 × 20 + 270 480 bytes per camera
  plus 40 MB (eight 720p cameras ≈ 253 MB); a 1 GB `tmpfs` at `/tmp/cache`;
  the process itself around 1–2 GB. **Semantic search needs 8 GB minimum and
  maxes the CPU while indexing** — leave every enrichment off here.
- *Disk.* Recordings and snapshots go where you point `/media/frigate`;
  retention is per `record.alerts.retain.days`, `record.detections.retain.days`,
  `record.continuous.days`, `record.motion.days` (decimals allowed) and
  `snapshots.retain`.
- *Ports.* Its bundled go2rtc binds 1984/8554/8555 — it **conflicts with a
  standalone go2rtc** on host networking. Run one or the other: with Frigate
  in, configure its `go2rtc:` block and read frames from `latest.jpg`.

Verdict for this box: one to three cameras on 720p/5 fps substreams with the
OpenVINO CPU detector and `ssdlite_mobilenet_v2`, no recording or short
motion-only recording, no enrichments — plausible, and it will contend with
whisper during spoken turns. Anything larger wants the appliance the Frigate
docs actually recommend (an N100-class mini PC with an iGPU, or a Coral).

Tag to pin: `ghcr.io/blakeblackshear/frigate:0.17.2` (the docs' `stable`
floats; `-tensorrt`/`-rocm`/`-standard-arm64` variants are irrelevant here).
0.17.2 is a security-fix release (WebSocket bypasses, credential leaks on
exposed instances) — one more reason 8971 stays on the LAN and 5000 on
loopback.

**Compose.** The docs' block, minus what this host lacks (`privileged: true`
and the `/dev/bus/usb` and `/dev/dri` devices exist for Coral and iGPU; drop
them), on host networking so it reaches mosquitto on 127.0.0.1:

```yaml
  frigate:
    image: ghcr.io/blakeblackshear/frigate:0.17.2
    container_name: frigate
    restart: unless-stopped
    profiles: [nvr]
    stop_grace_period: 30s
    network_mode: host            # mosquitto is loopback-only; ufw must then deny 5000 and 8971 from the LAN as it does 11434
    shm_size: "128mb"             # 2 × 1280×720 cameras + 40 MB, rounded up
    environment:
      - TZ=${TZ:-Europe/London}
      - FRIGATE_RTSP_PASSWORD=${FRIGATE_RTSP_PASSWORD:-}
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./frigate/config:/config
      - ./frigate/media:/media/frigate
      - type: tmpfs
        target: /tmp/cache
        tmpfs:
          size: 1000000000
    security_opt: [no-new-privileges:true]
    healthcheck:
      test: ["CMD-SHELL", "python3 -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:5000/api/version', timeout=4)\""]
      interval: 30s
      timeout: 6s
      retries: 3
      start_period: 60s
    mem_limit: 3g
    cpus: 2.5
```

Minimal `frigate/config/config.yml` (the OpenVINO model block is the
documented SSDLite one from the 0.16 docs; 0.17 shows the same values in a
model dropdown — confirm before deploying):

```yaml
mqtt:
  host: 127.0.0.1
  port: 1883
  topic_prefix: frigate
detectors:
  ov:
    type: openvino
    device: CPU
model:
  path: /openvino-model/ssdlite_mobilenet_v2.xml
  labelmap_path: /openvino-model/coco_91cl_bkgr.txt
  width: 300
  height: 300
  input_tensor: nhwc
  input_pixel_format: bgr
objects:
  track: [person, dog, cat, car, package]
snapshots:
  enabled: true
  retain: { default: 3 }          # days
record:
  enabled: true
  alerts:     { retain: { days: 7, mode: motion } }
  detections: { retain: { days: 2, mode: motion } }
semantic_search: { enabled: false }
face_recognition: { enabled: false }
cameras:
  front_door:
    ffmpeg:
      inputs:
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@192.168.1.64:554/Streaming/Channels/102
          roles: [detect, record]
    detect: { width: 1280, height: 720, fps: 5 }
    zones:
      porch:
        coordinates: 0.1,0.9,0.6,0.9,0.6,0.5,0.1,0.5
# optional, 0.17: descriptions from the model host, no cloud
# genai:
#   house:
#     provider: llamacpp
#     base_url: http://<model-host>/upstream/qwen3-vl-4b   # llama-swap's per-model passthrough
#     model: qwen3-vl-4b
#     roles: [descriptions]
```

**How Jarvis talks to it.** Three layers, each usable without the next:

1. *Today, no code:* `platform: still` with
   `url: http://127.0.0.1:5000/api/front_door/latest.jpg?height=720`. Consent,
   fencing and audit apply exactly as for any other camera.
2. *Today, config only:* an automation with an `mqtt` trigger on
   `frigate/events` (§5.1 has the YAML) — Frigate decides *when* to look,
   `vision.look` decides *whether* it may and fences what comes back.
3. *Later, a `frigate` integration* (the shape `hue`/`wled` already take):
   subscribe to `frigate/events` and `frigate/<camera>/<object>`, expose
   `binary_sensor.front_door_person` and `sensor.garden_person_count`, register
   each Frigate camera as a `vision` source whose frame is `latest.jpg` and
   whose *event* frame is `/api/events/{id}/snapshot.jpg?crop=1`, and mark
   every `description`/`sub_label` string it ingests untrusted.

### 2.3 Plain RTSP with ffmpeg or OpenCV

**What exists.** `platform: rtsp` already does the ffmpeg one-shot
(`camera.py:718`, `-rtsp_transport tcp -frames:v 1`, killed at the camera's
timeout). It needs `ffmpeg` in the image — one word added to the Dockerfile's
apt line (`tzdata curl ffmpeg`; Debian's ffmpeg is ~+100 MB of layers, and
that apt step is best-effort by design, so a build without a mirror would
silently produce an image without it).

**OpenCV** (`opencv-python-headless` 5.0.0.93, Apache-2.0 wrapper; the wheel
bundles LGPL FFmpeg) gives `cv2.VideoCapture("rtsp://…")` plus `cv2.dnn` for
ONNX/OpenVINO models. It is a 50 MB binary wheel, so under the pure-Python
rule (`requirements.txt`, `test_packaging.py`) it cannot go in `jarvis-core`;
it belongs in a side service like `jarvis-browser`.

**Cost comparison** for one frame from an H.264 camera: `still` from a camera
that has a snapshot URL ≈ 100–300 ms; go2rtc `frame.jpeg` with the stream
already open ≈ 200–500 ms (decode to next keyframe); a cold `ffmpeg` one-shot
1–3 s (connect, DESCRIBE/SETUP, wait for a keyframe, decode); Frigate
`latest.jpg` ≈ tens of ms (it is already decoding).

**Verdict.** Keep `rtsp` as the fallback it is. Do not grow the `jarvis-core`
image for it: both go2rtc and Frigate carry ffmpeg, and either turns RTSP into
a `still` URL.

---

## 3. Local vision-language models on llama.cpp / llama-swap

### 3.1 The wire

llama.cpp (release **v0.3.0**, 25 August 2026, MIT) serves multimodal models
through `libmtmd`; `llama-server` exposes it on the OpenAI-compatible
`/v1/chat/completions`. The README still calls the feature experimental
(added in #12898). What the request looks like:

```json
{
  "model": "qwen3-vl-4b",
  "temperature": 0.1,
  "messages": [
    {"role": "system", "content": "<the SYSTEM_PROMPT in analyze.py>"},
    {"role": "user", "content": [
      {"type": "text", "text": "Is there a parcel on the step?"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
    ]}
  ]
}
```

Facts that shape the Jarvis side:

- `image_url.url` "can be a remote URL, base64 (raw or URI-encoded via
  `data:image/...;base64`) or path to local file". **Jarvis must only ever
  send base64.** Handing the model server a URL makes the model host fetch it
  (SSRF from a box that can see the tailnet), and a path would read the model
  host's disk.
- The server needs the projector: `-m model.gguf --mmproj mmproj.gguf`, or
  `-hf org/repo` which downloads both. `--no-mmproj-offload` keeps the
  projector on CPU when VRAM is tight; `--image-min-tokens` /
  `--image-max-tokens` cap the cost of dynamic-resolution models (Qwen-VL,
  LFM2-VL). Clients "should check `/models` or `/v1/models` for the
  `multimodal` capability before a multimodal request".
- A model whose GGUF is one text file plus one `mmproj-*.gguf` needs both in
  the same directory when llama-swap or `--models-dir` loads it.

**llama-swap** (release **v251**, 23 August 2026, MIT) picks the upstream by
the request's `model` field, starts the right `llama-server`, and unloads it
after `ttl`. A vision entry is an ordinary entry whose `cmd` carries
`--mmproj`:

```yaml
models:
  qwen3-vl-4b:
    cmd: >
      llama-server --port ${PORT}
        -m /models/Qwen3-VL-4B-Instruct-Q4_K_M.gguf
        --mmproj /models/qwen3-vl-4b/mmproj-F16.gguf
        -c 8192 --image-max-tokens 1024 -ngl 99
    ttl: 300                       # seconds idle before it is unloaded and the VRAM comes back
    aliases: [house-vision]
```

Images: `ghcr.io/mostlygeek/llama-swap:{cpu,cuda,vulkan,intel,musa}` (nightly)
or pinned `v<swap>-<platform>-<llama build>`; a `unified-cuda`/`unified-vulkan`
image bundles llama-server. Endpoints proxied: `/v1/chat/completions`,
`/v1/models`, `/v1/completions`, `/v1/embeddings`, `/v1/rerank`, `/v1/audio/*`,
`/v1/images/*`, plus `/upstream/<model>/…` for direct access (which is what
Frigate's `llamacpp` provider would point at).

**The gateway.** Jarvis dials LiteLLM, not llama-swap. OpenAI-format content
parts pass through an `openai/` upstream unchanged, and the request would be
tagged the same way the chat path is, so the privacy guard's "local-only"
refusal covers frames as well — worth a test rather than an assumption when
this is built.

**What changes in Jarvis.** `analyze.py` grows an OpenAI-wire branch
(content-part message, `model` from config, same downscale/base64 step), the
`vision:` block gains `url:`/`backend:` mirroring `llm:` (default: inherit
`LLM_URL`), `local_only` is checked on that URL at setup, and `test_vision.py`
gets the mirrored cases. Frame pre-scaling can come from go2rtc's `w=` or
Frigate's `height=` until Pillow is in the image.

### 3.2 The candidates

Sizes are the actual GGUF files on Hugging Face on 26 August 2026 (Q4 text
model + the F16 projector, which is what llama.cpp loads); "tokens per frame"
is what a 1280×720 frame costs in the prompt, from each model card.
"Projector" names the `libmtmd` projector type in `tools/mtmd/clip-impl.h`,
i.e. proof that mainline llama.cpp loads it.

| model | licence | GGUF (text + mmproj) | tokens / 1280×720 frame | projector | what it is good for |
|---|---|---|---|---|---|
| **SmolVLM-256M / 500M-Instruct** (HuggingFaceTB, via `ggml-org/…-GGUF`) | Apache-2.0 | 0.18+0.19 GB / 0.44+0.20 GB | 64 per 512×512 tile → 6 tiles + a global view ≈ 450 (64 if the frame is sent at 512 px) | `idefics3` | Yes/no about a scene, "is anyone there", real-time on CPU (ngxson's webcam demo runs `llama-server -hf ggml-org/SmolVLM-500M-Instruct-GGUF` without a GPU). Weak at reading text and counting. |
| **SmolVLM2-2.2B** | Apache-2.0 | ~1.5+0.6 GB | as above | `idefics3` | Better descriptions; video-trained. |
| **LFM2-VL-450M / 1.6B / 3B** (LiquidAI) | LFM Open License v1.0 — free below **US$10 M annual revenue**, not OSI-open | 0.22+0.19 / 0.70+0.83 / 1.56+0.86 GB | dynamic: 256×384 → 96, 512×512 tiles; "1000×3000 → 1 020"; user-tunable cap | `lfm2` | Purpose-built for CPU/edge; native 512 px tiles with a thumbnail for global context (1.6B+). Likely the best quality-per-second on four cores. |
| **Moondream2** (2025-04-14, `ggml-org/moondream2-20250414-GGUF`) | Apache-2.0 | 2.84 (F16 only) + 0.91 GB | fixed SigLIP crop | `mlp` (LLaVA-style) | Good at "is there a person/package"; only an F16 text GGUF is published, so 3.8 GB resident. **Moondream 3 (preview)** is Business Source License 1.1 and has no llama.cpp support. |
| **InternVL3-1B / 2B** (`ggml-org`) | Apache-2.0 (Qwen2.5 text side) | 1.12+0.63 GB (2B) | dynamic tiles | `internvl` | Compact, decent OCR for its size. |
| **Qwen2.5-VL-3B / 7B** (`ggml-org`) | Apache-2.0 | 1.93+1.34 / 4.68+1.35 GB | 28 px per token → ~1 170 at 1280×720; capped with `--image-max-tokens` (card: 256–1 280 range) | `qwen2.5vl_merger` | What `vision` defaults to under Ollama today (`qwen2.5vl:7b`). Reads signs and labels well. |
| **Qwen3-VL-2B / 4B / 8B** (Apache-2.0; `unsloth/Qwen3-VL-*-Instruct-GGUF` with `mmproj-F16.gguf`; the `ggml-org` repos returned 401 anonymously) | Apache-2.0 | 1.11+0.82 / 2.50+0.84 / 5.03+1.16 GB | 32 px per token → ~900 at 1280×720 | `qwen3vl_merger` | Current best small VLMs; Frigate's own model table recommends `qwen3-vl` ("enhanced ability to identify smaller objects and interactions"). **4B is the GPU pick.** |
| **Gemma 3 4B-it** (`ggml-org/gemma-3-4b-it-GGUF`) | Gemma Terms of Use (not open-source; the HF base repo is gated, the ggml-org GGUF is not) | 2.49+0.85 GB | fixed: 896×896 → **256 tokens** | `gemma3` | Cheap in tokens, expensive in encoder (SigLIP-400M at 896²); strong prose; 12B/27B for GPU only. |
| **Gemma 4 E2B / E4B** (`ggml-org`) | Gemma terms | 4.59+0.99 GB (E4B) | — | `gemma4v` | Newer; audio+vision; same licence question. Frigate notes it "sometimes resorts to more vague terms". |
| **MiniCPM-V 4.5** (`openbmb/MiniCPM-V-4_5-gguf`; base repo tagged Apache-2.0) | Apache-2.0 | 5.03+1.10 GB | 64 tokens per slice (resampler), up to 1.8 MP | `resampler` (4.5 via legacy guide; 4.6 native) | 8B-class OCR specialist; GPU only. |
| LLaVA 1.5/1.6, Pixtral 12B, Mistral Small 3.1 24B | Apache-2.0 | 4–15 GB | — | `mlp` / `pixtral` | Superseded (LLaVA) or GPU-only. Not recommended. |

### 3.3 What a look costs on CPU — and where to run it

A look is (a) the vision encoder over the frame, (b) prefill of the image
tokens plus prompt through the language model, (c) decoding ~100 tokens of
answer. On four Zen 3 cores, prefill of a 2B Q4 model runs on the order of
50–100 tokens/s and of a 0.5B model several hundred; the encoder is a
SigLIP-class ViT whose cost scales with tiles. That gives, as *estimates, not
measurements* (nothing was run on this host):

- SmolVLM-500M / LFM2-VL-450M at 512 px: **~1–3 s** per look.
- LFM2-VL-1.6B, Qwen3-VL-2B at 640 px with `--image-max-tokens 512`: **~5–15 s**.
- Qwen3-VL-4B, Gemma 3 4B: **20–60 s** — and three of the four cores are
  whisper's during a spoken turn, so a look during a conversation lands in the
  same budget that already misses the 2 s round-trip threshold (`BLOCKERS.md` §2).

Measure before choosing: `llama-server`'s response carries
`timings.prompt_ms` and `timings.predicted_ms`; one curl with a real doorbell
frame at the intended `w=` settles it in a minute. (Known regression to be
aware of when measuring: llama.cpp issue #22582 reports the server running
the encoder on CPU per slice even with GPU layers — check the build.)

**Where.** This deployment has 3090s behind llama-swap. Under the VRAM
justification rule the paragraph for a VLM reads: *Qwen3-VL-4B at Q4_K_M with
an F16 projector is ≈3.4 GB of weights plus a ≤8k KV cache; with `ttl: 300` it
holds that only for five minutes after a look; if the cards cannot hold it
beside `qwen3.8-27b`, llama-swap swaps — so a look costs a model reload (tens
of seconds) and evicts the voice path's KV cache. What it buys: a description
that reads labels and counts people, instead of the yes/no a 500M model gives.*
Whether that trade is acceptable is the operator's call; the two-tier shape in
§5.2 is designed so that most questions never need the swap.

### 3.4 Best CPU-only choice

For this box, in order:

1. **LFM2-VL-1.6B** (Q4_0 text + Q8_0 projector, 1.3 GB) — the best quality
   that still answers within a spoken turn, *if* the LFM Open License
   (household use is far under the US$10 M threshold) is acceptable.
2. **SmolVLM-500M-Instruct** (Apache-2.0, 0.55 GB) — the licence-clean
   yes/no model; the right one for "is anyone at the door" triage.
3. **Qwen3-VL-2B** (Apache-2.0, 1.9 GB) with `--image-max-tokens 512` — the
   smallest model that reads a delivery label; slower.

A CPU llama-swap on this box (`ghcr.io/mostlygeek/llama-swap:cpu`, one model
with `ttl`) is a reasonable second endpoint, but it only earns its 1–2 GB of
RAM if the GPU path's swap cost turns out to be unacceptable. Ollama is not
needed anywhere in this design.

---

## 4. Detection and recognition without a language model

### 4.1 Why both

A VLM answers a question in seconds and costs a model; a detector answers
"person / not person, where" in milliseconds and can run on every frame. The
detector produces the *event*; the VLM is for the *question about* it. Frigate
is that split packaged: motion (CPU) → detector → track → event, with the VLM
optional at the end.

### 4.2 Runtimes and models, with licences

| package (PyPI, 26 Aug 2026) | licence | runs on this CPU | notes |
|---|---|---|---|
| `onnxruntime` 1.29.0 | MIT | yes (AVX2) | The general answer for any ONNX detector; ~30–80 ms for a nano YOLO-class model at 640 px on a few cores. |
| `openvino` 2026.3.0 | Apache-2.0 | yes — Frigate's docs: works on AMD though unsupported | Fastest CPU path for SSDLite/YOLOX (single-digit ms for SSDLite in Frigate reports). |
| `opencv-python-headless` 5.0.0.93 | Apache-2.0 (bundles LGPL FFmpeg) | yes | `cv2.dnn` runs ONNX/OpenVINO; also the RTSP reader. |
| `ai-edge-litert` 2.2.0 | Apache-2.0 | yes | TFLite; what Frigate's "CPU detector" uses. |

| model | licence | verdict |
|---|---|---|
| Ultralytics YOLOv8/11 (`ultralytics`) | **AGPL-3.0** (enterprise licence to avoid it) | AGPL binds on distribution and on serving over a network; a single household is not caught, but this repository carries **no LICENSE file** and publishing Jarvis with an `ultralytics` dependency would put the whole of it under AGPL. Avoid. |
| YOLOv9 (WongKinYiu) | GPL-3.0 | Same shape of problem. |
| YOLO-NAS (Deci) | non-commercial — Frigate's docs say so outright | Avoid. |
| **YOLOX** 0.3.0 (Megvii) | Apache-2.0 | ONNX export, nano/tiny sizes; supported by Frigate's OpenVINO and ONNX detectors. |
| **RF-DETR** 1.9.4 (`rfdetr`, Roboflow) | Apache-2.0 | Transformer detector with a nano size and ONNX export; supported by Frigate's ONNX/OpenVINO detectors. |
| **D-FINE / DEIMv2** | Apache-2.0 | Frigate: OpenVINO "CPU mode only". |
| **SSDLite MobileNet v2** | Apache-2.0 (TF model zoo) | Frigate's bundled default for OpenVINO (`/openvino-model/ssdlite_mobilenet_v2.xml`); COCO-91 labels; the cheapest thing that finds people. |

**Verdict.** Do not write a detector service. It cannot live in `jarvis-core`
(binary wheels), it would duplicate Frigate's motion-gated pipeline badly, and
the only reason to have one — events — Frigate already publishes. If a
detector is ever needed outside Frigate (e.g. a USB webcam the assistant
watches while Frigate is off), the licence-clean stack is RF-DETR-nano or
YOLOX-nano exported to ONNX on `onnxruntime`, as a side container.

### 4.3 Face recognition

| option | code licence | model licence | shape |
|---|---|---|---|
| **InsightFace** (0.7; PyPI `insightface` 1.0.1) | MIT | **"available for non-commercial research purposes only"** — both the auto-downloaded `buffalo_l`/`antelopev2` packs and the manual ones; commercial licence by contacting InsightFace | The accuracy benchmark; `onnxruntime` backend, CPU fine. |
| **dlib** 20.0.1 + `face_recognition` 1.3.0 | Boost / MIT | The recognition ResNet is public domain per its author; the 68-point landmark model is trained on iBUG 300-W, whose licence "excludes commercial use" | Old but simple; HOG detector is CPU-cheap, CNN detector is not. |
| **CompreFace** v1.2.0 (Exadel) | Apache-2.0 | uses InsightFace models → same non-commercial question | Java + PostgreSQL + several GB; overkill for one house. |
| **Frigate built-in** (0.16+) | MIT | model_size `small` = FaceNet embedding on CPU; `large` = ArcFace, "only recommended … when an integrated or dedicated GPU / NPU is available"; face *detection* is a "lightweight DNN model that runs on the CPU" via cv2; AVX2 required | Faces are trained in Frigate's UI (5–10 images to start, 20–30 for robustness), stored under `/media/frigate/clips/faces`, and the result arrives as `sub_label` on the person event and on `frigate/tracked_object_update`. `/api/faces/*` covers register/recognise/rename. |

**Verdict.** If the house wants "it's Alex at the door" for *household
members*, use Frigate's `small` model — it is already on the box, CPU-sized,
and its data stays in Frigate's clips directory under Frigate's retention.
Nothing else is worth a container. Two rules whatever is chosen: the VLM is
never asked to identify people (`SYSTEM_PROMPT` in `analyze.py` already says
"Do not guess identities" — keep it), and recognising *visitors* is biometric
processing of people who did not consent; keep enrolment to the household
and off by default.

---

## 5. Fitting a house assistant

### 5.1 Doorbell / person at the door → a notification

The flow, using only what exists plus Frigate:

```
camera ──RTSP──► Frigate: motion → OpenVINO person → zone "porch" → event
                  │
                  └──MQTT frigate/events {type:new, after:{camera:front_door, label:person, entered_zones:[porch]}}
                                │
                       Jarvis automation (mqtt trigger)
                                │
              vision.look  (camera: Front Door, consent: always → fetched; ask → the phone is asked first)
                                │  fenced description; turn marked untrusted
              companion.notify + notifications.add  →  phone / console / voice
```

```yaml
# automations.yaml — trigger keys per jarvis-core/jarvis/automation/triggers.py:722-740
- alias: Someone at the front door
  trigger:
    - platform: mqtt
      topic: frigate/events
      value_template: >-
        {{ value_json.type == 'new'
           and value_json.after.camera == 'front_door'
           and value_json.after.label == 'person'
           and 'porch' in value_json.after.entered_zones }}
      payload: "True"
  action:
    - service: vision.look
      data:
        camera: Front Door
        question: Is someone at the door, and are they carrying a parcel or wearing a uniform?
        reason: Frigate saw a person on the porch
      response_variable: seen
    - service: companion.notify
      data:
        message: Someone is at the front door.
        kind: notify
        importance: high
```

Three things to get right, all of them already the integration's rules:

- **The description is a quote, not a fact and never an instruction.** It
  arrives fenced; a notification that embeds it should present it as "the
  camera model said: …" and the record in `notifications` should carry it as
  an untrusted `body`, separate from the operator-authored `message`. Today
  `companion.notify` takes one string, so the honest v1 is the fixed sentence
  above and the description on request; attaching untrusted bodies to
  notifications is a small, worthwhile change.
- **Rate limiting already protects the human.** A porch that fires twenty
  events an hour hits `max_per_hour` and the automation gets a clean
  `rate_limited` result instead of twenty pushes; tune per camera.
- **Use the event's frame, not a fresh one.** Frigate's
  `/api/events/{id}/snapshot.jpg?crop=1` is the best shot of the *person*;
  `latest.jpg` two seconds later may be an empty porch. That is the first
  thing a `frigate` integration adds over the config-only flow.

Frigate's own GenAI descriptions (`provider: llamacpp` pointed at
llama-swap's `/upstream/<model>`) are an alternative that puts the description
on `frigate/tracked_object_update` with no Jarvis involvement — and therefore
with no consent check, no fence and no audit row. If they are turned on, the
ingest side must fence that text exactly as `vision` fences its own, and the
Frigate cameras must not include any a household would mark `never`.

### 5.2 "Is anyone in the garden?"

Two tiers, cheapest first:

1. **State, no model, no frame.** Frigate publishes `frigate/garden/person`
   (count) continuously; `GET /api/events?camera=garden&label=person&after=<now-300>&in_progress=1`
   answers "in the last five minutes". A `frigate` integration would surface
   this as `sensor.garden_person_count`, and the assistant answers from state
   like any other sensor — instantly, and no picture leaves Frigate.
2. **A look, when the question needs one.** "What are they doing?", "is the
   gate open?" → `vision.look` on the garden camera (`consent: always`
   outdoors), frame from `latest.jpg?height=720` or, if there is a live
   event, its snapshot.

The tool description for `look_at_camera` should say this ordering out loud,
or a model will reach for the camera when the sensor already knew.

### 5.3 Privacy

- **LAN-only, enforced not assumed.** go2rtc `api.listen: 127.0.0.1:1984`;
  Frigate 5000 loopback-only and 8971 LAN-only via ufw (the same rule
  `docs/security.md` applies to 11434); cameras on a VLAN with no route to the
  internet (Frigate's dual-NIC advice exists for this); `local_only` extended
  to `vision`'s model URL. If the VLM runs on the model host, frames cross the
  tailnet — encrypted and still the operator's, but say so in the docs.
- **Retention is three separate dials.** Jarvis: `frame_ttl` 30 s, one
  description per camera for `description_ttl` (1 h), audit rows with no
  images. go2rtc: nothing, unless `cache=` is used. Frigate: snapshots
  `retain.default`, recordings `alerts`/`detections`/`motion`/`continuous`
  days, and the face library — a biometric store — under `clips/faces`.
  Sensible household defaults: no continuous recording, alerts 7 days,
  detections 2, snapshots 3.
- **Consent map, and Frigate respects it too.** Outdoor `always`, living
  spaces `ask`, bedrooms and children's rooms `never` — and a `never` room is
  not a Frigate camera either, because an NVR recording it is "the picture
  arriving by a different road" (`jarvis-core/docs/vision.md`).
- **Every description is attacker-authored.** From `vision`, from Frigate's
  GenAI, from a `sub_label` typed into Frigate's UI: fenced on the way in, never
  routed to a dispatcher, and the turn's tier raised. The existing tests pin
  this for `vision`; a `frigate` integration inherits the same test
  (`test_every_integration_that_fences_content_also_raises_the_tier` walks the
  tree).
- **The record is the control.** `vision.audit` plus the console's camera
  activity row is what lets a household check "did it look, and why". Frigate's
  UI and MQTT are the equivalent for the NVR. A week of both, then decide
  whether the consent map still feels right — the vision doc's own advice.

---

## 6. Recommended shape

1. **go2rtc behind `--profile cameras`, `vision:` enabled with `still` URLs.**
   Zero code. Proves the cameras, the consent flow and the console indicator on
   real hardware. Pin `alexxit/go2rtc:1.9.14`.
2. **Teach `vision` the OpenAI wire and the `local_only` check.** One module
   (`analyze.py`) plus config keys mirroring `llm:`, tests mirrored in
   `test_vision.py`, `docs/verification.md` row re-measured. Until then the
   integration cannot reach any model this deployment runs.
3. **VLM on the model host: `qwen3-vl-4b` in llama-swap with `ttl`**, gated by
   the VRAM paragraph in §3.3. Fallback/second tier on this box: SmolVLM-500M
   (Apache) or LFM2-VL-1.6B (LFM licence) under a CPU llama-swap — only after
   measuring `timings.prompt_ms` here.
4. **Frigate behind `--profile nvr`**, `ghcr.io/blakeblackshear/frigate:0.17.2`,
   OpenVINO `device: CPU` with SSDLite, 720p/5 fps substreams, one to three
   cameras, enrichments off, MQTT to the loopback broker; config-only
   automation for the door first, a `frigate` integration (entities, event
   snapshots, fenced ingest) second.
5. **Faces: defer.** If wanted, Frigate's `small` model for household members
   only.
6. **Do not:** add `ultralytics` or any binary-wheel detector to `jarvis-core`;
   enable Frigate semantic search on a 16 GB box that also runs whisper; expose
   1984 or 5000 beyond loopback; let any surface treat a camera description as
   anything but a quote.

---

## Sources

Repository (read on 26 August 2026): `jarvis-core/jarvis/integrations/vision/{__init__,analyze,camera,consent,fence}.py`; `jarvis-core/docs/vision.md`; `jarvis-core/config/configuration.yaml`; `jarvis-core/docker-compose.yml`; `jarvis-core/Dockerfile`; `jarvis-core/mosquitto/mosquitto.conf`; `jarvis-core/jarvis/automation/triggers.py`; `jarvis-core/jarvis/integrations/{companion,notifications,mqtt}/__init__.py`; `jarvis-web/src/lib/activity.svelte.ts`; `docs/TOOLING_DECISIONS.md`; `BLOCKERS.md`; `docs/verification.md`; `jarvis-core/docs/security.md`.

go2rtc
- https://github.com/AlexxIT/go2rtc (README; `internal/api`, `internal/mjpeg`, `internal/onvif`, `internal/ffmpeg`, `internal/v4l2`, `internal/rtsp`, `internal/streams` READMEs; LICENSE)
- https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14
- https://hub.docker.com/r/alexxit/go2rtc/tags

Frigate
- https://docs.frigate.video/frigate/installation/
- https://docs.frigate.video/frigate/hardware/
- https://docs.frigate.video/frigate/camera_setup/
- https://docs.frigate.video/configuration/object_detectors/
- https://docs.frigate.video/configuration/authentication/
- https://docs.frigate.video/configuration/record/
- https://docs.frigate.video/configuration/face_recognition/
- https://docs.frigate.video/configuration/semantic_search/
- https://docs.frigate.video/configuration/genai/genai_config/ (and `genai/objects.md`, `genai/review_summaries.md` on the `dev` branch)
- https://docs.frigate.video/integrations/mqtt/
- https://github.com/blakeblackshear/frigate/blob/dev/docs/static/frigate-api.yaml (OpenAPI)
- https://github.com/blakeblackshear/frigate/releases/tag/v0.17.2
- https://github.com/blakeblackshear/frigate/blob/dev/LICENSE
- https://github.com/blakeblackshear/frigate/discussions/9417 (OpenVINO on AMD Ryzen)
- https://github.com/blakeblackshear/frigate/discussions/7601 (inference speeds)

llama.cpp / llama-swap
- https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/mtmd/README.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/mtmd/clip-impl.h (projector types)
- https://github.com/ggml-org/llama.cpp/releases/tag/v0.3.0
- https://github.com/ggml-org/llama.cpp/issues/22582
- https://github.com/mostlygeek/llama-swap (README, LICENSE.md)
- https://github.com/mostlygeek/llama-swap/releases/tag/v251
- https://github.com/ngxson/smolvlm-realtime-webcam

Models (Hugging Face, file sizes from the API with `blobs=true`)
- https://huggingface.co/ggml-org/SmolVLM-500M-Instruct-GGUF · https://huggingface.co/ggml-org/SmolVLM-256M-Instruct-GGUF · https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct
- https://huggingface.co/LiquidAI/LFM2-VL-450M-GGUF · https://huggingface.co/LiquidAI/LFM2-VL-1.6B-GGUF · https://huggingface.co/LiquidAI/LFM2-VL-3B-GGUF · https://huggingface.co/LiquidAI/LFM2-VL-1.6B (model card) · LFM Open License v1.0 (LICENSE in the GGUF repo)
- https://huggingface.co/ggml-org/moondream2-20250414-GGUF · https://huggingface.co/vikhyatk/moondream2 · https://huggingface.co/moondream/moondream3-preview (LICENSE.md)
- https://huggingface.co/ggml-org/InternVL3-2B-Instruct-GGUF
- https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF · https://huggingface.co/ggml-org/Qwen2.5-VL-7B-Instruct-GGUF · https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct (min/max_pixels)
- https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF · https://huggingface.co/unsloth/Qwen3-VL-4B-Instruct-GGUF · https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF · https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- https://huggingface.co/ggml-org/gemma-3-4b-it-GGUF · https://ai.google.dev/gemma/docs/core/model_card_3 · https://ai.google.dev/gemma/terms
- https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF
- https://huggingface.co/openbmb/MiniCPM-V-4_5-gguf · https://huggingface.co/openbmb/MiniCPM-V-4_5

Detection and faces
- https://www.ultralytics.com/license · https://github.com/ultralytics/ultralytics/blob/main/LICENSE
- https://github.com/WongKinYiu/yolov9/blob/main/LICENSE.md
- https://github.com/Megvii-BaseDetection/YOLOX (LICENSE, release 0.3.0)
- https://github.com/roboflow/rf-detr (LICENSE, release 1.9.4) · https://pypi.org/project/rfdetr/
- https://github.com/Peterande/D-FINE/blob/master/LICENSE
- https://pypi.org/project/onnxruntime/ · https://pypi.org/project/openvino/ · https://pypi.org/project/opencv-python-headless/ · https://pypi.org/project/ai-edge-litert/
- https://github.com/deepinsight/insightface (README "License"; `python-package/README.md`) · https://pypi.org/project/insightface/
- https://github.com/davisking/dlib-models (README) · https://pypi.org/project/dlib/ · https://pypi.org/project/face-recognition/
- https://github.com/exadel-inc/CompreFace (LICENSE, release v1.2.0)
