"""End-to-end proof: the whole platform, booted from a config file, in-process.

Every other suite tests one subsystem behind fakes. This one boots the real
:class:`~jarvis.core.Jarvis` from a real ``configuration.yaml`` and drives it
the way a person would, asserting that the *seams* hold:

    PCM -> STT -> the real conversation agent -> the real tool registry ->
    the real domain services -> a light that is genuinely on -> TTS

Nothing here needs hardware. Exactly three things are faked, and each is
faked at the process boundary where the network would otherwise be:

    Wyoming STT/TTS/wake   plain objects injected via ``jarvis.data`` — the
                           same seam the voice integration documents
    Ollama                 an ``httpx.MockTransport`` serving real NDJSON, so
                           the actual OllamaClient parsing/streaming runs
    the phone/desktop      a transport callable handed to CompanionManager

Everything between those edges is production code: the YAML loader, the
integration loader, the state machine, the service registry, the automation
engine, the script runner, the recorder's SQLite, the FastAPI app, and the
websocket framing the browser HUD and the Android app actually parse.
"""

import asyncio
import json
import sys
import time
import wave
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api.server import create_app  # noqa: E402
from jarvis.auth import DATA_AUTH, ENV_TOKEN, async_setup_auth  # noqa: E402
from jarvis.config import load_config  # noqa: E402
from jarvis.const import STATE_OFF, STATE_ON  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.voice import (  # noqa: E402
    DATA_STT_CLIENT,
    DATA_TTS_CLIENT,
    DATA_WAKE_CLIENT,
    get_voice_data,
)
from jarvis.presence import NEEDS_ANSWER  # noqa: E402

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2

#: The entity the money test drives. A demo light that starts *off*, so
#: "it is on afterwards" cannot be true by accident.
LAB_LIGHT = "light.bed_light"
LAB_ALIAS = "Lab Lights"

#: Automation entity ids are slugified from the `alias:`, not the `id:`.
MOTION_AUTOMATION = "automation.motion_runs_the_evening_script"
LAUNDRY_AUTOMATION = "automation.laundry_asks_the_user"

#: A demo entity the demo platform leaves unassigned, so a test can put it in
#: an area of its own without demo's setup claiming it back on the next boot.
UNASSIGNED_ENTITY = "lock.front_door_lock"

#: Every pipeline run in this file is bounded well under the 300 s default, so
#: a stage that never returns fails the test instead of stalling the suite.
RUN_TIMEOUT = 20.0

#: What the fake model says once the tool call has come back.
REPLY_DELTAS = ("The lab lights are on, ", "Sir.")
FULL_REPLY = "".join(REPLY_DELTAS)


# ===========================================================================
# the configuration under test
# ===========================================================================
CONFIGURATION_YAML = """\
jarvis:
  name: Jarvis E2E
  latitude: 51.5072
  longitude: -0.1276
  elevation: 11
  time_zone: Europe/London
  unit_system: metric
  areas:
    - name: Living Room
      aliases: [lounge, front room]
    - name: Kitchen
    - name: Bedroom
    - name: Lab

recorder:
  db_file: e2e.db
  commit_interval: 0
  auto_purge: false
  purge_keep_days: 3

history:
  days: 2

logbook:
  max_entries: 500
  log_service_calls: true

sun:
  update_interval: 3600

demo:
  create_areas: true

template:
  - sensor:
      - name: Feels Like Outside
        state: "{{ (states('sensor.outside_temperature') | float(0) - 2.0) | round(1) }}"
        unit_of_measurement: "C"
    binary_sensor:
      - name: Anyone Home
        state: "{{ is_state('person.chris', 'home') }}"
        device_class: presence

person:
  - name: Chris
    id: chris
    device_trackers:
      - device_tracker.chris_phone

input_boolean:
  guest_mode:
    name: Guest mode
    initial: "off"
  dryer_started:
    name: Dryer started
    initial: "off"

input_number:
  bedtime_volume:
    name: Bedtime volume
    min: 0
    max: 100
    step: 5
    initial: 30

input_select:
  house_mode:
    name: House mode
    options: [home, away, night]
    initial: home

input_text:
  last_announcement:
    name: Last announcement
    max: 255

companion:

voice:
  language: en
  tts:
    voice: en_GB-alan-medium
  wake:
    model: hey_jarvis
  pipelines:
    - name: Jarvis
      voice: en_GB-alan-medium
      wake_word: hey_jarvis
      language: en
    - name: Guest
      voice: en_US-lessac-medium
      wake_word: ok_nabu
      language: en

llm:
  url: http://ollama.invalid:11434
  model: qwen3:8b
  max_tool_rounds: 3
  approval_ttl: 60
  persona: "You are Jarvis, a composed British AI butler."
  expose:
    domains: [light, switch, cover, climate, fan, media_player, scene, script, lock]
    entities:
      - sensor.outside_temperature
      - person.chris
    exclude_entities:
      - cover.garage_door
  conversation:
    ttl: 900
    max_turns: 20

script:
  lab_evening:
    alias: Lab evening
    description: Put the house into night mode and dim the ceiling lights.
    mode: single
    sequence:
      - service: input_select.select_option
        target:
          entity_id: input_select.house_mode
        data:
          option: night
      - service: light.turn_on
        target:
          entity_id: light.ceiling_lights
        data:
          brightness: 42

scene:
  - name: Away
    id: away
    entities:
      light.ceiling_lights: "off"
      light.kitchen_lights: "off"
      switch.decorative_lights: "off"

automation:
  - id: motion_runs_the_script
    alias: Motion runs the evening script
    mode: single
    trigger:
      - platform: state
        entity_id: binary_sensor.basement_motion
        to: "on"
    action:
      - service: script.lab_evening

  - id: laundry_asks_the_user
    alias: Laundry asks the user
    mode: single
    trigger:
      - platform: event
        event_type: laundry_done
    action:
      - service: companion.ask
        data:
          question: "The washing is done. Shall I start the dryer?"
          options: ["yes", "no"]
          timeout: 15
        response_variable: reply
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ reply.answer == 'yes' }}"
            sequence:
              - service: input_boolean.turn_on
                target:
                  entity_id: input_boolean.dryer_started
"""


def write_config(config_dir: Path) -> Path:
    """Drop a realistic configuration.yaml into `config_dir`."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "configuration.yaml").write_text(CONFIGURATION_YAML, encoding="utf-8")
    return config_dir


# ===========================================================================
# fakes — one per hardware/network boundary, and no further
# ===========================================================================
class FakeStt:
    """Stands in for the Wyoming STT container. Keeps every PCM chunk."""

    def __init__(self, text: str = "turn on the lab lights") -> None:
        self.text = text
        self.chunks: list[bytes] = []
        self.rate: int | None = None

    async def transcribe(self, audio, rate: int = SAMPLE_RATE) -> str:
        self.rate = rate
        async for chunk in audio:
            self.chunks.append(chunk)
        return self.text

    @property
    def audio(self) -> bytes:
        return b"".join(self.chunks)


class FakeTts:
    """Stands in for piper. Returns real, parseable PCM."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text, voice=None):
        self.spoken.append(text)
        return (b"\x00\x00" * 640, SAMPLE_RATE, SAMPLE_WIDTH, 1)  # 40 ms


class FakeWake:
    """Stands in for openWakeWord.

    Stops reading the moment it hears the word, exactly as the real client
    does — the rest of the stream is the utterance, and STT needs it.
    """

    def __init__(self, name: str | None = "hey_jarvis", after_chunks: int = 1) -> None:
        self.name = name
        self.after_chunks = after_chunks
        self.chunks = 0

    async def detect(self, audio):
        async for _chunk in audio:
            self.chunks += 1
            if self.name and self.chunks >= self.after_chunks:
                return self.name
        return None


class FakeOllama:
    """A scripted local model served over ``httpx.MockTransport``.

    It speaks genuine Ollama NDJSON, so the real :class:`OllamaClient` does
    the streaming, the chunk accumulation and the tool-call parsing. The
    script is deliberately trivial: the first ``/api/chat`` of a turn asks for
    one tool call, and every one after that (i.e. once a ``tool`` message is
    in the history) speaks the answer.
    """

    def __init__(self, tool_call=None, reply_deltas=REPLY_DELTAS) -> None:
        self.tool_call = tool_call
        self.reply_deltas = list(reply_deltas)
        self.requests: list[dict] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def tools_offered(self) -> list[str]:
        """Tool names the agent attached to the first request."""
        if not self.requests:
            return []
        schema = self.requests[0].get("tools") or []
        return [t.get("function", {}).get("name", "") for t in schema]

    def tool_messages(self) -> list[dict]:
        """The ``role: tool`` messages the agent fed back to the model."""
        out = []
        for payload in self.requests:
            for message in payload.get("messages") or []:
                if message.get("role") == "tool":
                    out.append(message)
        return out

    # --- transport --------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path != "/api/chat":
            return httpx.Response(404, json={"error": f"no route {request.url.path}"})

        payload = json.loads(request.read() or b"{}")
        self.requests.append(payload)
        model = payload.get("model") or "qwen3:8b"
        messages = payload.get("messages") or []
        tools_have_run = any(m.get("role") == "tool" for m in messages)

        if self.tool_call is not None and not tools_have_run:
            name, arguments = self.tool_call
            return _ndjson(
                [
                    {
                        "model": model,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"function": {"name": name, "arguments": arguments}}
                            ],
                        },
                        "done": False,
                    },
                    {
                        "model": model,
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                    },
                ]
            )

        chunks = [
            {
                "model": model,
                "message": {"role": "assistant", "content": delta},
                "done": False,
            }
            for delta in self.reply_deltas
        ]
        chunks.append(
            {
                "model": model,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
            }
        )
        return _ndjson(chunks)


def _ndjson(chunks: list[dict]) -> httpx.Response:
    body = b"".join(json.dumps(chunk).encode("utf-8") + b"\n" for chunk in chunks)
    return httpx.Response(
        200, content=body, headers={"content-type": "application/x-ndjson"}
    )


class FakeDeviceChannel:
    """The websocket half of the device channel, without a websocket.

    Records what Jarvis pushed to each device and lets a test answer as that
    device would, which is exactly what ``CompanionManager`` is handed by the
    API layer in production.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def __call__(self, device_id: str, payload: dict) -> bool:
        self.sent.append((device_id, dict(payload)))
        return True  # delivered

    def messages_for(self, device_id: str) -> list[dict]:
        return [payload for target, payload in self.sent if target == device_id]

    @property
    def last(self) -> dict:
        return self.sent[-1][1]


# ===========================================================================
# boot helpers
# ===========================================================================
def loud_pcm(chunks: int = 4, samples: int = 320) -> list[bytes]:
    """PCM that reads as speech to the VAD (well above the RMS threshold)."""
    frame = b"".join(
        int(8000 if index % 2 else -8000).to_bytes(2, "little", signed=True)
        for index in range(samples)
    )
    return [frame] * chunks


async def boot(config_dir: Path, ollama: FakeOllama | None = None) -> Jarvis:
    """Build, set up and start a Jarvis from `config_dir`, with fakes attached.

    The fakes go into ``jarvis.data`` *before* setup, which is the documented
    injection seam — the integrations themselves are entirely real.
    """
    config = load_config(config_dir)
    jarvis = Jarvis(config_dir)

    jarvis.data[DATA_STT_CLIENT] = FakeStt()
    jarvis.data[DATA_TTS_CLIENT] = FakeTts()
    jarvis.data[DATA_WAKE_CLIENT] = FakeWake()
    jarvis.data["llm_transport"] = (ollama or FakeOllama()).transport

    await async_setup_auth(jarvis)
    await jarvis.async_setup(config)
    await jarvis.async_start()
    return jarvis


async def wait_until(predicate, timeout: float = 5.0, message: str = "") -> None:
    """Poll until `predicate` holds. Automations run as background tasks."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(message or f"condition never held within {timeout}s")


async def add_alias(jarvis: Jarvis, entity_id: str, alias: str) -> None:
    """Name an entity the way a user does on the /devices page."""
    entry = jarvis.entities.get(entity_id)
    assert entry is not None, f"{entity_id} is not in the entity registry"
    await jarvis.entities.update(entity_id, aliases=[*entry.aliases, alias])


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    return write_config(tmp_path / "config")


@pytest.fixture
async def house(config_dir):
    """A fully booted house with the lab light aliased and the model scripted.

    The scripted model is parked on ``jarvis.data`` — the scratch space
    integrations already share — so a test can inspect what the agent sent it
    without every test needing to unpack a tuple.
    """
    ollama = FakeOllama(tool_call=("turn_on", {"name": "lab lights"}))
    jarvis = await boot(config_dir, ollama)
    await add_alias(jarvis, LAB_LIGHT, LAB_ALIAS)
    jarvis.data["e2e_ollama"] = ollama
    try:
        yield jarvis
    finally:
        await jarvis.async_stop()


# ===========================================================================
# 1. the platform boots
# ===========================================================================
async def test_every_configured_integration_sets_up_and_entities_appear(house):
    """A realistic configuration.yaml produces a working house."""
    jarvis = house

    # Core integrations plus everything named in the YAML.
    for domain in ("voice", "llm", "companion", "recorder", "input_helpers"):
        assert jarvis.data.get(domain) is not None, f"{domain} did not set up"
    for domain, service in (
        ("light", "turn_on"),
        ("script", "lab_evening"),
        ("scene", "turn_on"),
        ("conversation", "process"),
        ("companion", "ask"),
        ("history", "get"),
        ("recorder", "purge"),
        ("input_select", "select_option"),
        ("automation", "trigger"),
        ("voice", "say"),
    ):
        assert jarvis.services.has_service(domain, service), f"{domain}.{service} missing"

    # Entities from every source: demo platform, templates, helpers, YAML
    # scripts/scenes/automations, person, sun.
    for entity_id in (
        "light.ceiling_lights",
        LAB_LIGHT,
        "switch.coffee_machine",
        "sensor.outside_temperature",
        "climate.thermostat",
        "lock.front_door_lock",
        "sensor.feels_like_outside",
        "binary_sensor.anyone_home",
        "input_boolean.guest_mode",
        "input_number.bedtime_volume",
        "input_select.house_mode",
        "input_text.last_announcement",
        "script.lab_evening",
        "scene.away",
        MOTION_AUTOMATION,
        LAUNDRY_AUTOMATION,
        "person.chris",
        "sun.sun",
    ):
        assert jarvis.states.get(entity_id) is not None, f"{entity_id} never appeared"

    # Areas from YAML and from the demo integration, with aliases intact.
    area_names = {area.name for area in jarvis.areas.areas.values()}
    assert {"Living Room", "Kitchen", "Bedroom", "Lab"} <= area_names
    living_room = jarvis.areas.get_by_name("lounge")
    assert living_room is not None and living_room.name == "Living Room"

    # Entities are wired to areas through their devices, which is what makes
    # "the lights in the kitchen" resolvable.
    assert jarvis.area_for_entity("light.kitchen_lights") == "kitchen"

    # The template sensor really evaluated against the demo sensor (15.6 - 2).
    assert jarvis.states.get("sensor.feels_like_outside").state == "13.6"


# ===========================================================================
# 2. the money test — a full voice round trip through the real pipeline
# ===========================================================================
async def test_voice_round_trip_from_pcm_to_a_light_that_is_really_on(house):
    """PCM in, spoken answer out, and the light actually changed.

    Every stage between the microphone and the speaker is production code:
    the pipeline runner, the conversation agent, the Ollama client, the tool
    registry, the light domain services and the entity object underneath.
    """
    jarvis = house
    ollama = jarvis.data["e2e_ollama"]
    voice = get_voice_data(jarvis)
    assert voice is not None

    light_before = jarvis.states.get(LAB_LIGHT)
    assert light_before.state == STATE_OFF, "the test is meaningless if it starts on"

    run = voice.async_create_run(
        start_stage="stt", end_stage="tts", timeout=RUN_TIMEOUT
    )

    queue: asyncio.Queue = asyncio.Queue()
    for chunk in loud_pcm():
        queue.put_nowait(chunk)
    queue.put_nowait(None)  # end of audio

    await run.execute(queue)

    # --- the pipeline contract the HUD, satellites and phone parse ---------
    assert run.error is None, run.error and run.error.message
    assert run.event_types == [
        "run-start",
        "stt-start",
        "stt-vad-start",
        "stt-vad-end",
        "stt-end",
        "intent-start",
        "intent-progress",
        "intent-progress",
        "intent-end",
        "tts-start",
        "tts-end",
        "run-end",
    ]

    events = {event.type: event.data for event in run.events}
    assert events["run-start"]["language"] == "en"
    assert events["run-start"]["runner_data"]["timeout"] > 0
    assert events["stt-end"]["stt_output"]["text"] == "turn on the lab lights"
    speech = events["intent-end"]["intent_output"]["response"]["speech"]["plain"]["speech"]
    assert speech == FULL_REPLY
    assert events["intent-end"]["intent_output"]["conversation_id"] == run.conversation_id
    assert events["tts-start"]["tts_input"] == FULL_REPLY
    assert events["tts-start"]["voice"] == "en_GB-alan-medium"
    assert events["tts-end"]["tts_output"]["url"].startswith("/api/tts_proxy/")
    assert events["tts-end"]["tts_output"]["mime_type"] == "audio/wav"

    # The audio really travelled: STT saw the bytes we queued.
    assert voice.stt.audio == b"".join(loud_pcm())
    assert voice.stt.rate == SAMPLE_RATE

    # --- the model was driven properly ------------------------------------
    assert len(ollama.requests) == 2, "expected one tool round then one answer"
    assert "turn_on" in ollama.tools_offered
    assert ollama.requests[0]["messages"][-1]["content"] == "turn on the lab lights"
    # The tool result went back to the model verbatim, and reported success.
    tool_result = json.loads(ollama.tool_messages()[0]["content"])
    assert tool_result["status"] == "ok"
    assert LAB_LIGHT in json.dumps(tool_result)

    # --- THE POINT: the house actually changed ----------------------------
    light_after = jarvis.states.get(LAB_LIGHT)
    assert light_after.state == STATE_ON, "the light did not actually turn on"
    assert light_after.last_changed > light_before.last_changed
    # ...and it was the entity object that changed it, not a bare state write.
    assert jarvis.entity_object(LAB_LIGHT).state == STATE_ON
    # Nothing else was swept up by the name match.
    assert jarvis.states.get("light.kitchen_lights").state == STATE_ON  # demo default
    assert jarvis.states.get("light.ceiling_lights").state == STATE_ON  # demo default

    # --- the spoken audio is real, playable WAV ---------------------------
    token = run.tts_token
    wav_bytes, mime = jarvis.data["tts_cache"][token]
    assert mime == "audio/wav"
    with wave.open(BytesIO(wav_bytes)) as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == SAMPLE_WIDTH
        assert wav.getnframes() == 640
    assert voice.tts.spoken == [FULL_REPLY]


async def test_a_wake_word_run_prefixes_the_same_pipeline(house):
    """start_stage=wake adds the two wake events in front of the same run.

    This is the satellite's shape: one continuous stream, the first chunk of
    which trips the wake word and the rest of which is the utterance.
    """
    voice = get_voice_data(house)
    run = voice.async_create_run(
        start_stage="wake", end_stage="tts", timeout=RUN_TIMEOUT
    )

    queue: asyncio.Queue = asyncio.Queue()
    for chunk in loud_pcm():
        queue.put_nowait(chunk)
    queue.put_nowait(None)

    await run.execute(queue)

    assert run.error is None, run.error and run.error.message
    assert run.event_types[:3] == ["run-start", "wake_word-start", "wake_word-end"]
    assert run.detected_wake_word == "hey_jarvis"
    assert run.event_types[-1] == "run-end"

    # The wake stage handed the remaining audio on rather than eating it.
    assert voice.wake.chunks == 1
    assert len(voice.stt.chunks) == 3

    # And the rest of the run behaved exactly as the stt-first one does.
    assert run.stt_text == "turn on the lab lights"
    assert run.response_text == FULL_REPLY
    assert house.states.get(LAB_LIGHT).state == STATE_ON


# ===========================================================================
# 3. the same thing, over the wire
# ===========================================================================
def test_the_pipeline_and_service_calls_over_the_websocket_api(config_dir):
    """The websocket contract, driven by a real ASGI client.

    Synchronous on purpose: ``TestClient`` runs the app in its own event loop,
    and everything with an asyncio primitive in it (the pipeline's audio queue,
    the automation tasks) has to live in that same loop. ``portal.call`` is how
    the setup and teardown get in there.
    """
    ollama = FakeOllama(tool_call=("turn_on", {"name": "lab lights"}))
    jarvis = Jarvis(config_dir)
    jarvis.data[DATA_STT_CLIENT] = FakeStt()
    jarvis.data[DATA_TTS_CLIENT] = FakeTts()
    jarvis.data[DATA_WAKE_CLIENT] = FakeWake()
    jarvis.data["llm_transport"] = ollama.transport

    app = create_app(jarvis, static_dir=config_dir / "no-www")
    config = load_config(config_dir)

    async def _start():
        await async_setup_auth(jarvis)
        await jarvis.async_setup(config)
        await add_alias(jarvis, LAB_LIGHT, LAB_ALIAS)
        await jarvis.async_start()
        _info, secret = await jarvis.data[DATA_AUTH].create_token("e2e")
        return secret

    with TestClient(app) as client:
        token = client.portal.call(_start)
        try:
            assert jarvis.states.get(LAB_LIGHT).state == STATE_OFF
            assert client.get("/healthz").json()["running"] is True

            with client.websocket_connect("/api/websocket") as ws:
                # --- handshake --------------------------------------------
                challenge = ws.receive_json()
                assert challenge["type"] == "auth_required"
                assert challenge["ha_version"].startswith("jarvis-")
                ws.send_json({"type": "auth", "access_token": token})
                assert ws.receive_json()["type"] == "auth_ok"

                # --- pipeline list ----------------------------------------
                ws.send_json({"id": 1, "type": "assist_pipeline/pipeline/list"})
                listed = ws.receive_json()
                assert listed["success"] is True
                pipelines = listed["result"]["pipelines"]
                assert {p["name"] for p in pipelines} == {"Jarvis", "Guest"}
                assert listed["result"]["preferred_pipeline"] in [p["id"] for p in pipelines]
                assert pipelines[0]["wake_word_id"]  # the HA client alias

                # --- a full run, with binary audio frames -----------------
                ws.send_json(
                    {
                        "id": 2,
                        "type": "assist_pipeline/run",
                        "start_stage": "stt",
                        "end_stage": "tts",
                        "timeout": RUN_TIMEOUT,
                        "input": {"sample_rate": SAMPLE_RATE},
                    }
                )
                assert ws.receive_json() == {
                    "id": 2, "type": "result", "success": True, "result": None
                }

                started = ws.receive_json()
                assert started["event"]["type"] == "run-start"
                handler_id = started["event"]["data"]["runner_data"][
                    "stt_binary_handler_id"
                ]
                assert 1 <= handler_id <= 255

                for chunk in loud_pcm():
                    ws.send_bytes(bytes([handler_id]) + chunk)
                ws.send_bytes(bytes([handler_id]))  # lone id byte = end of audio

                events = read_until(ws, "run-end")
                types = [event["type"] for event in events]
                assert "error" not in types
                assert types == [
                    "stt-start",
                    "stt-vad-start",
                    "stt-vad-end",
                    "stt-end",
                    "intent-start",
                    "intent-progress",
                    "intent-progress",
                    "intent-end",
                    "tts-start",
                    "tts-end",
                    "run-end",
                ]

                data = {event["type"]: event["data"] for event in events}
                assert data["stt-end"]["stt_output"]["text"] == "turn on the lab lights"
                spoken = data["intent-end"]["intent_output"]["response"]["speech"]
                assert spoken["plain"]["speech"] == FULL_REPLY
                tts_url = data["tts-end"]["tts_output"]["url"]

                # The light really changed, over the wire, through the model.
                assert jarvis.states.get(LAB_LIGHT).state == STATE_ON

                # --- call_service seen through a subscription -------------
                ws.send_json(
                    {"id": 3, "type": "subscribe_events", "event_type": "state_changed"}
                )
                assert ws.receive_json()["success"] is True

                ws.send_json(
                    {
                        "id": 4,
                        "type": "call_service",
                        "domain": "light",
                        "service": "turn_off",
                        "target": {"entity_id": LAB_LIGHT},
                    }
                )

                changed = read_state_change(ws, LAB_LIGHT)
                assert changed["data"]["new_state"]["state"] == STATE_OFF
                assert changed["data"]["old_state"]["state"] == STATE_ON

                result = read_result(ws, 4)
                assert result["success"] is True
                touched = {
                    state["entity_id"] for state in result["result"]["changed_states"]
                }
                assert touched == {LAB_LIGHT}
                assert result["result"]["context"]["id"]
                assert jarvis.states.get(LAB_LIGHT).state == STATE_OFF

            # TTS audio is served unauthenticated — audio players send no headers.
            audio = client.get(tts_url)
            assert audio.status_code == 200
            assert audio.headers["content-type"].startswith("audio/wav")
            with wave.open(BytesIO(audio.content)) as wav:
                assert wav.getnframes() == 640

            # And the REST half of the same contract answers too.
            rest = client.post(
                "/api/conversation/process",
                json={"text": "and now?"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rest.status_code == 200
            assert rest.json()["response"]["speech"]["plain"]["speech"] == FULL_REPLY
        finally:
            client.portal.call(jarvis.async_stop)


def read_until(ws, event_type: str, limit: int = 60) -> list[dict]:
    """Collect pipeline event frames up to and including `event_type`."""
    seen: list[dict] = []
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") != "event":
            continue
        seen.append(message["event"])
        if message["event"]["type"] == event_type:
            return seen
    raise AssertionError(f"never saw {event_type}; got {[e['type'] for e in seen]}")


def read_state_change(ws, entity_id: str, limit: int = 40) -> dict:
    """The next state_changed event frame for `entity_id`."""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") != "event":
            continue
        event = message["event"]
        if event.get("data", {}).get("entity_id") == entity_id:
            return event
    raise AssertionError(f"no state_changed for {entity_id}")


def read_result(ws, msg_id, limit: int = 40) -> dict:
    """The result frame for `msg_id`, skipping any events in between."""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == "result" and message.get("id") == msg_id:
            return message
    raise AssertionError(f"no result for id {msg_id}")


# ===========================================================================
# 4. automation -> script -> another entity
# ===========================================================================
async def test_a_state_trigger_runs_a_script_that_changes_another_entity(house):
    """The whole automation chain, and `last_triggered` afterwards."""
    jarvis = house
    automation_id = MOTION_AUTOMATION

    # A never-run automation has no `last_triggered` at all (the entity layer
    # drops null attributes), which is the state the chain starts from.
    assert jarvis.states.get(automation_id).state == STATE_ON
    assert "last_triggered" not in jarvis.states.get(automation_id).attributes
    assert jarvis.states.get("input_select.house_mode").state == "home"
    assert jarvis.states.get("script.lab_evening").state == STATE_OFF

    # The trigger: a sensor changes, exactly as a real motion sensor would.
    jarvis.states.set("binary_sensor.basement_motion", STATE_ON)

    # Step one of the script.
    await wait_until(
        lambda: jarvis.states.get("input_select.house_mode").state == "night",
        message="the script never ran through to the input_select",
    )
    # ...and step two, which is the far end of the chain.
    await wait_until(
        lambda: jarvis.states.get("light.ceiling_lights").attributes.get("brightness")
        == 42,
        message="the script never reached the light",
    )
    await wait_until(
        lambda: jarvis.states.get("script.lab_evening").state == STATE_OFF
        and jarvis.states.get("script.lab_evening").attributes.get("last_triggered"),
        message="the script never finished and stamped last_triggered",
    )

    # Every link in the chain is accounted for.
    triggered = jarvis.states.get(automation_id).attributes.get("last_triggered")
    assert triggered, "the automation never stamped last_triggered"

    # The script is also exposed as a service the LLM and the HUD can call.
    assert jarvis.services.has_service("script", "lab_evening")

    # A second, unrelated transition does not run it again (`to: "on"` only).
    jarvis.states.set("binary_sensor.basement_motion", STATE_OFF)
    await asyncio.sleep(0.05)
    assert jarvis.states.get(automation_id).attributes["last_triggered"] == triggered


async def test_a_scene_applies_across_domains(house):
    """Scenes are a destination: one call, several domains."""
    jarvis = house
    assert jarvis.states.get("light.ceiling_lights").state == STATE_ON
    assert jarvis.states.get("switch.decorative_lights").state == STATE_ON

    await jarvis.async_call_service("scene", "turn_on", {"entity_id": "scene.away"})

    assert jarvis.states.get("light.ceiling_lights").state == STATE_OFF
    assert jarvis.states.get("light.kitchen_lights").state == STATE_OFF
    assert jarvis.states.get("switch.decorative_lights").state == STATE_OFF


# ===========================================================================
# 5. cross-device: ask the phone, branch on the answer
# ===========================================================================
async def test_companion_ask_reaches_the_phone_and_the_automation_branches(house):
    """Two devices, one question, and an automation that waits for the answer."""
    jarvis = house
    presence = jarvis.data["presence"]
    companion = jarvis.data["companion"]

    # Two devices register, the way the websocket layer registers them.
    presence.register("desktop-1", "Study Desktop", "desktop", ["notify", "speak"])
    presence.update("desktop-1", screen_on=True, locked=False)
    presence.register("phone-1", "Pixel 8", "android", ["notify", "speak", "ask"])
    presence.update("phone-1", screen_on=True, locked=False)
    # The user last touched the phone, which is what should decide the routing.
    presence.touch_interaction("phone-1")

    ranked = [device.device_id for device in presence.rank(NEEDS_ANSWER)]
    assert ranked[0] == "phone-1", f"routing picked the wrong device: {ranked}"

    channel = FakeDeviceChannel()
    companion.set_transport(channel)

    assert jarvis.states.get("input_boolean.dryer_started").state == STATE_OFF

    # Fire the automation's trigger. It will block inside companion.ask.
    jarvis.bus.fire("laundry_done", {})

    await wait_until(
        lambda: bool(channel.messages_for("phone-1")),
        message="the question never reached the phone",
    )
    assert not channel.messages_for("desktop-1"), "the desktop should not have been asked"

    question = channel.messages_for("phone-1")[0]
    assert question["type"] == "jarvis_message"
    assert question["kind"] == "ask"
    assert question["mode"] == "ask"
    assert question["text"] == "The washing is done. Shall I start the dryer?"
    assert question["options"] == ["yes", "no"]

    # The phone answers — the same call the websocket push handler makes.
    assert companion.on_device_answer(question["message_id"], "yes") is True

    await wait_until(
        lambda: jarvis.states.get("input_boolean.dryer_started").state == STATE_ON,
        message="the automation never resumed after the answer",
    )

    # An answer is data, not an authorisation: nothing else moved.
    assert jarvis.states.get("input_boolean.guest_mode").state == STATE_OFF


async def test_a_no_answer_leaves_the_branch_untaken(house):
    """The negative case: the same chain, answered "no", changes nothing."""
    jarvis = house
    presence = jarvis.data["presence"]
    companion = jarvis.data["companion"]
    presence.register("phone-1", "Pixel 8", "android", ["notify", "ask"])
    presence.update("phone-1", screen_on=True, locked=False)
    presence.touch_interaction("phone-1")

    channel = FakeDeviceChannel()
    companion.set_transport(channel)

    jarvis.bus.fire("laundry_done", {})
    await wait_until(lambda: bool(channel.sent), message="the question never went out")

    assert companion.on_device_answer(channel.last["message_id"], "no") is True

    await wait_until(
        lambda: jarvis.states.get("automation.laundry_asks_the_user").attributes[
            "current"
        ]
        == 0,
        message="the automation never finished",
    )
    assert jarvis.states.get("input_boolean.dryer_started").state == STATE_OFF


# ===========================================================================
# 6. recorder + history
# ===========================================================================
async def test_states_are_recorded_and_history_get_reads_them_back(house):
    """The recorder's SQLite really holds what the house did."""
    jarvis = house
    recorder = jarvis.data["recorder"]

    started = time.time()
    await jarvis.async_call_service(
        "light", "turn_on", {"entity_id": LAB_LIGHT, "brightness": 200}
    )
    await jarvis.async_call_service("light", "turn_off", {"entity_id": LAB_LIGHT})

    written = await recorder.async_commit()
    assert written > 0, "the recorder queued nothing"
    assert Path(recorder.db_path) == house.config_dir / "e2e.db"
    assert Path(recorder.db_path).exists()

    response = await jarvis.async_call_service(
        "history",
        "get",
        {"entity_id": LAB_LIGHT, "start": started - 60},
        return_response=True,
    )
    # history.get answers {entity_id: [rows oldest-first]}.
    rows = response["history"][LAB_LIGHT]
    assert rows, "history.get returned no rows for the light"
    assert response["start"] and response["end"]

    states = [row["state"] for row in rows]
    assert STATE_ON in states and STATE_OFF in states
    assert states[-1] == STATE_OFF
    on_row = next(row for row in rows if row["state"] == STATE_ON)
    assert on_row["attributes"]["brightness"] == 200

    # The rows came from SQLite, not from the live state machine.
    counts = await recorder.row_counts()
    assert counts["states"] >= len(rows)
    assert LAB_LIGHT in await recorder.recorded_entity_ids()


# ===========================================================================
# 7. restart persistence
# ===========================================================================
async def test_a_restart_from_the_same_config_dir_keeps_everything(config_dir):
    """Stop, rebuild from the same directory, and find the house as it was."""
    first = await boot(config_dir)
    try:
        await add_alias(first, LAB_LIGHT, LAB_ALIAS)
        # A room created at runtime (the HUD's /areas page), not from YAML,
        # holding an entity the demo platform does not reassign on boot.
        workshop = await first.areas.create("Workshop", ["shed"])
        await first.entities.update(UNASSIGNED_ENTITY, area_id=workshop.id)
        assert first.area_for_entity(UNASSIGNED_ENTITY) == workshop.id

        # Helper values the user changed.
        await first.async_call_service(
            "input_select", "select_option",
            {"entity_id": "input_select.house_mode", "option": "away"},
        )
        await first.async_call_service(
            "input_number", "set_value",
            {"entity_id": "input_number.bedtime_volume", "value": 65},
        )
        await first.async_call_service(
            "input_boolean", "turn_on", {"entity_id": "input_boolean.guest_mode"}
        )
        await first.async_call_service(
            "input_text", "set_value",
            {"entity_id": "input_text.last_announcement", "value": "dinner is ready"},
        )

        before = {
            "areas": {area.id: area.name for area in first.areas.areas.values()},
            "devices": sorted(first.devices.devices),
            "entities": sorted(first.entities.entities),
        }
        assert before["devices"], "the demo platform created no devices"
    finally:
        await first.async_stop()

    # --- restart ----------------------------------------------------------
    second = await boot(config_dir)
    try:
        assert {a.id: a.name for a in second.areas.areas.values()} == before["areas"]
        assert sorted(second.devices.devices) == before["devices"]
        assert sorted(second.entities.entities) == before["entities"]

        # The runtime-created area and its aliases came back.
        restored = second.areas.get_by_name("shed")
        assert restored is not None and restored.name == "Workshop"
        assert second.area_for_entity(UNASSIGNED_ENTITY) == restored.id

        # The alias a user typed survived, so voice still resolves the name.
        assert LAB_ALIAS in second.entities.get(LAB_LIGHT).aliases

        # Helper values won over the `initial:` in configuration.yaml.
        assert second.states.get("input_select.house_mode").state == "away"
        assert second.states.get("input_number.bedtime_volume").state == "65"
        assert second.states.get("input_boolean.guest_mode").state == STATE_ON
        assert (
            second.states.get("input_text.last_announcement").state == "dinner is ready"
        )

        # A restarted house is still a working one.
        await second.async_call_service(
            "light", "turn_on", {"entity_id": LAB_LIGHT}
        )
        assert second.states.get(LAB_LIGHT).state == STATE_ON
    finally:
        await second.async_stop()


# ===========================================================================
# 8. the security model still holds with everything wired together
# ===========================================================================
async def test_a_gated_action_is_held_even_when_the_model_asks_for_it(config_dir):
    """The lock is exposed to the model and still cannot be opened by it.

    This is the one property that must survive integration: the gate lives in
    the tool registry, outside the conversation, so a model that calls the
    tool gets `approval_required` and the door stays shut.
    """
    ollama = FakeOllama(
        tool_call=("lock_control", {"name": "front door lock", "action": "unlock"}),
        reply_deltas=("That one needs your say-so, Sir.",),
    )
    jarvis = await boot(config_dir, ollama)
    try:
        locked_before = jarvis.states.get("lock.front_door_lock").state

        approvals: list[dict] = []
        jarvis.bus.listen("jarvis_approval_required", lambda e: approvals.append(e.data))

        result = await jarvis.async_call_service(
            "conversation", "process", {"text": "unlock the front door"},
            return_response=True,
        )

        assert result["response"]["speech"]["plain"]["speech"]
        tool_result = json.loads(ollama.tool_messages()[0]["content"])
        assert tool_result["status"] == "approval_required"
        assert tool_result["request_id"]

        # Nothing happened to the door, and the request is on the pending list.
        assert jarvis.states.get("lock.front_door_lock").state == locked_before
        pending = await jarvis.async_call_service(
            "llm", "pending_requests", {}, return_response=True
        )
        assert [item["request_id"] for item in pending["pending"]] == [
            tool_result["request_id"]
        ]

        # The approval carries the verbatim action, not the model's paraphrase.
        assert approvals and approvals[0]["tool"] == "lock_control"
        assert approvals[0]["arguments"]["action"] == "unlock"
    finally:
        await jarvis.async_stop()


async def test_the_excluded_entity_is_invisible_to_the_model(house):
    """`exclude_entities` holds through the whole booted stack.

    The garage door exists, has working services and is in a domain the model
    is otherwise allowed to drive — it is unreachable purely because the tool
    registry refuses to resolve it.
    """
    jarvis = house
    registry = jarvis.data["llm_tools"]
    garage = "cover.garage_door"

    assert jarvis.states.get(garage) is not None, "the entity must exist to be excluded"
    closed_before = jarvis.states.get(garage).state

    # Named outright, it does not resolve.
    read = await registry.call("get_state", {"entity_id": garage}, context=None)
    assert read["status"] == "error"
    assert "no exposed entity" in read["error"]

    # Asked to open it, nothing happens to the door.
    opened = await registry.call(
        "set_cover_position", {"entity_id": garage, "position": 100}, context=None
    )
    assert opened["status"] == "error"
    assert jarvis.states.get(garage).state == closed_before

    # And it is never advertised in the first place.
    listed = await registry.call("list_entities", {}, context=None)
    assert garage not in json.dumps(listed)
    assert LAB_LIGHT in json.dumps(listed)  # the ones it may touch are there
    assert garage not in jarvis.data["llm"].house_summary()
