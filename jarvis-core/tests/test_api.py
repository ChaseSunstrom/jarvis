"""API server tests: auth, REST, and the websocket clients already speak.

Nothing here touches the network, a broker, Ollama or hardware. The voice
pipeline is driven by fake STT/TTS objects injected through
``jarvis.data["voice"]``, so the real ``PipelineRun`` — and therefore the real
wire format the browser HUD and the Android app parse — is exercised end to
end, right down to the binary audio frames.
"""

import asyncio
import contextlib
import json
import sys
import time
import warnings
import wave
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api.common import ApiError  # noqa: E402
from jarvis.api.server import create_app  # noqa: E402
from jarvis.api.websocket import WebSocketHandler  # noqa: E402
from jarvis.auth import (  # noqa: E402
    DATA_AUTH,
    ENV_TOKEN,
    AuthManager,
    async_setup_auth,
    extract_bearer_token,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.store import Store  # noqa: E402
from jarvis.voice.pipeline import PipelineRun  # noqa: E402

TOKEN_NAME = "test-client"
SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class FakeStt:
    """Records every PCM chunk it is handed, so we can prove audio arrived."""

    def __init__(self, text="turn on the kitchen light"):
        self.text = text
        self.chunks = []
        self.rate = None

    async def transcribe(self, audio, rate=SAMPLE_RATE):
        self.rate = rate
        async for chunk in audio:
            self.chunks.append(chunk)
        return self.text

    @property
    def audio(self):
        return b"".join(self.chunks)


class FakeTts:
    def __init__(self):
        self.spoken = []

    async def synthesize(self, text, voice=None):
        self.spoken.append(text)
        # 40 ms of silence: enough to be a real, parseable WAV.
        return (b"\x00\x00" * 640, SAMPLE_RATE, 2, 1)


class FakeVoice:
    """Stands in for ``jarvis.data['voice']`` — the VoiceData the integration builds."""

    def __init__(self, jarvis, reply="Kitchen light is on."):
        self.jarvis = jarvis
        self.stt = FakeStt()
        self.tts = FakeTts()
        self.reply = reply
        self.heard = None
        self.runs = []
        self.pipelines = None  # no store: the API falls back to its default

    async def converse(self, text, conversation_id=None):
        self.heard = text
        return self.reply

    def async_create_run(self, pipeline=None, **kwargs):
        run = PipelineRun(
            self.jarvis,
            stt=self.stt,
            tts=self.tts,
            converse=self.converse,
            **kwargs,
        )
        self.runs.append(run)
        return run


class RecordingWebhook:
    def __init__(self):
        self.calls = []

    async def __call__(self, data=None, query=None, headers=None, method="POST"):
        self.calls.append({"data": data, "query": query, "method": method})
        return len(self.calls)


class FakeRecorder:
    def __init__(self, rows):
        self.rows = rows
        self.seen = None

    async def async_history(self, entity_ids=None, start_time=None, end_time=None):
        self.seen = (entity_ids, start_time, end_time)
        return self.rows


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


@pytest.fixture
def auth(jarvis, monkeypatch):
    """An in-memory token manager attached the way the real setup attaches one."""
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    manager = AuthManager()
    jarvis.data[DATA_AUTH] = manager
    return manager


@pytest.fixture
def token(auth):
    _info, secret = asyncio.run(auth.create_token(TOKEN_NAME))
    return secret


@pytest.fixture
def client(jarvis, auth, tmp_path):
    # An explicit empty static dir: the test must not care whether someone has
    # built a web client into <repo>/www.
    with TestClient(create_app(jarvis, static_dir=tmp_path / "no-www")) as test_client:
        yield test_client


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def handshake(ws, token):
    """auth_required -> auth -> auth_ok. Returns the auth_ok frame."""
    challenge = ws.receive_json()
    assert challenge["type"] == "auth_required"
    assert challenge["ha_version"].startswith("jarvis-")
    ws.send_json({"type": "auth", "access_token": token})
    ok = ws.receive_json()
    assert ok["type"] == "auth_ok"
    return ok


def wait_for(predicate, timeout=5.0):
    """Poll for something the server finishes on its own thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def read_until(ws, event_type, limit=40):
    """Collect pipeline event frames until `event_type` arrives."""
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") != "event":
            continue
        seen.append(message["event"])
        if message["event"]["type"] == event_type:
            return seen
    raise AssertionError(f"never saw {event_type}; got {[e['type'] for e in seen]}")


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
async def test_create_verify_list_revoke(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    manager = await AuthManager(Store(tmp_path, "auth")).async_load()

    info, secret = await manager.create_token("phone")
    assert manager.verify(secret) is info
    assert manager.verify(secret + "x") is None
    assert manager.verify("") is None
    assert manager.verify(None) is None
    assert manager.is_valid(secret) is True
    assert [t.name for t in manager.list_tokens()] == ["phone"]
    assert manager.list() == manager.list_tokens()
    assert "token_hash" not in info.as_dict()

    assert await manager.revoke(info.id) is True
    assert manager.verify(secret) is None
    assert await manager.revoke(info.id) is False


async def test_tokens_persist_and_the_secret_is_never_stored(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    manager = await AuthManager(Store(tmp_path, "auth")).async_load()
    _info, secret = await manager.create_token("laptop")

    on_disk = (tmp_path / ".storage" / "auth.json").read_text(encoding="utf-8")
    assert secret not in on_disk

    reloaded = await AuthManager(Store(tmp_path, "auth")).async_load()
    assert reloaded.verify(secret) is not None
    assert [t.name for t in reloaded.list_tokens()] == ["laptop"]


async def test_first_run_mints_and_logs_one_token(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    manager = await AuthManager(Store(tmp_path, "auth")).async_load()

    with caplog.at_level("WARNING"):
        secret = await manager.async_ensure_initial_token()

    assert secret and manager.verify(secret) is not None
    assert secret in caplog.text  # printed once, clearly
    assert await manager.async_ensure_initial_token() is None  # never re-minted
    assert len(manager.list_tokens()) == 1


async def test_env_token_overrides_and_suppresses_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_TOKEN, "from-the-environment")
    manager = await AuthManager(Store(tmp_path, "auth")).async_load()

    assert await manager.async_ensure_initial_token() is None
    assert manager.list_tokens() == []
    info = manager.verify("from-the-environment")
    assert info is not None and info.name == ENV_TOKEN
    assert manager.verify("something-else") is None


async def test_async_setup_auth_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    jarvis = Jarvis(tmp_path)

    manager = await async_setup_auth(jarvis, store=Store(tmp_path, "auth"))
    assert jarvis.data[DATA_AUTH] is manager
    assert len(manager.list_tokens()) == 1  # first run minted one

    again = await async_setup_auth(jarvis)
    assert again is manager
    assert len(manager.list_tokens()) == 1


def test_extract_bearer_token():
    assert extract_bearer_token("Bearer abc") == "abc"
    assert extract_bearer_token("bearer abc") == "abc"
    assert extract_bearer_token("Basic abc") is None
    assert extract_bearer_token("Bearer ") is None
    assert extract_bearer_token(None) is None


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
def test_healthz_is_open(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_rest_requires_a_bearer_token(client, token):
    assert client.get("/api/states").status_code == 401
    assert client.get("/api/states", headers=headers("nope")).status_code == 401
    assert client.get("/api/states", headers=headers(token)).status_code == 200
    assert client.get("/api/", headers=headers(token)).json()["message"] == "API running."


def test_get_states(client, jarvis, token):
    jarvis.states.set("light.kitchen", "on", {"brightness": 180})
    jarvis.states.set("sensor.temp", "21.5")

    listing = client.get("/api/states", headers=headers(token)).json()
    assert {item["entity_id"] for item in listing} == {"light.kitchen", "sensor.temp"}

    one = client.get("/api/states/light.kitchen", headers=headers(token)).json()
    assert one["state"] == "on"
    assert one["attributes"]["brightness"] == 180
    assert client.get("/api/states/light.nope", headers=headers(token)).status_code == 404


def test_post_and_delete_state(client, jarvis, token):
    created = client.post(
        "/api/states/sensor.probe",
        headers=headers(token),
        json={"state": "42", "attributes": {"unit_of_measurement": "°C"}},
    )
    assert created.status_code == 201
    assert jarvis.states.get("sensor.probe").state == "42"

    updated = client.post(
        "/api/states/sensor.probe", headers=headers(token), json={"state": "43"}
    )
    assert updated.status_code == 200

    assert client.delete("/api/states/sensor.probe", headers=headers(token)).status_code == 200
    assert jarvis.states.get("sensor.probe") is None
    assert client.delete("/api/states/sensor.probe", headers=headers(token)).status_code == 404


def test_post_service_invokes_the_handler(client, jarvis, token):
    calls = []

    async def handler(call):
        calls.append(call.data)
        jarvis.states.set("light.kitchen", "on", {"brightness": call.get("brightness")})

    jarvis.services.register("light", "turn_on", handler)

    response = client.post(
        "/api/services/light/turn_on",
        headers=headers(token),
        json={"entity_id": "light.kitchen", "brightness": 42},
    )
    assert response.status_code == 200
    assert calls == [{"entity_id": "light.kitchen", "brightness": 42}]
    # HA-compatible body: the states that changed while the service ran.
    assert response.json()[0]["entity_id"] == "light.kitchen"


def test_post_service_merges_target_and_returns_a_response(client, jarvis, token):
    seen = {}

    async def handler(call):
        seen.update(call.data)
        return {"ok": True}

    jarvis.services.register("light", "turn_on", handler, supports_response=True)

    response = client.post(
        "/api/services/light/turn_on?return_response=true",
        headers=headers(token),
        json={"brightness": 5, "target": {"area_id": "kitchen"}},
    )
    assert response.status_code == 200
    assert seen == {"brightness": 5, "area_id": "kitchen"}
    assert response.json()["service_response"] == {"ok": True}


def test_post_unknown_service_is_a_client_error(client, token):
    response = client.post("/api/services/light/nope", headers=headers(token))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "service_not_found"


def test_config_and_events(client, jarvis, token):
    jarvis.config = {"jarvis": {"name": "Home", "latitude": 51.5}}
    jarvis.states.set("light.kitchen", "on")

    config = client.get("/api/config", headers=headers(token)).json()
    assert config["location_name"] == "Home"
    assert config["latitude"] == 51.5
    assert "light" in config["components"]
    assert config["config_dir"] == str(jarvis.config_dir)

    received = []
    jarvis.bus.listen("party_time", lambda event: received.append(event.data))
    listing = client.get("/api/events", headers=headers(token)).json()
    assert {"event": "party_time", "listener_count": 1} in listing

    response = client.post(
        "/api/events/party_time", headers=headers(token), json={"volume": 11}
    )
    assert response.status_code == 200
    assert received == [{"volume": 11}]


def test_history_is_empty_without_a_recorder(client, token):
    response = client.get("/api/history/period", headers=headers(token))
    assert response.status_code == 200
    assert response.json() == []


def test_history_delegates_to_a_recorder(client, jarvis, token):
    recorder = FakeRecorder([[{"entity_id": "light.kitchen", "state": "on"}]])
    jarvis.data["recorder"] = recorder

    response = client.get(
        "/api/history/period/2026-01-01T00:00:00Z",
        headers=headers(token),
        params={"filter_entity_id": "light.kitchen", "end_time": "2026-01-02T00:00:00Z"},
    )
    assert response.status_code == 200
    assert response.json() == recorder.rows
    assert recorder.seen == (
        ["light.kitchen"],
        "2026-01-01T00:00:00Z",
        "2026-01-02T00:00:00Z",
    )


def test_conversation_process_over_rest(client, jarvis, token):
    async def process(call):
        return {
            "response": {
                "speech": {"plain": {"speech": f"you said {call.get('text')}"}},
                "response_type": "action_done",
            },
            "conversation_id": "abc",
        }

    jarvis.services.register("conversation", "process", process, supports_response=True)

    body = client.post(
        "/api/conversation/process",
        headers=headers(token),
        json={"text": "hello", "conversation_id": "abc"},
    ).json()
    assert body["response"]["speech"]["plain"]["speech"] == "you said hello"
    assert body["conversation_id"] == "abc"


def test_conversation_process_without_an_agent(client, token):
    response = client.post(
        "/api/conversation/process", headers=headers(token), json={"text": "hi"}
    )
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "agent_not_found"


def test_tts_proxy_serves_cached_audio_without_a_token(client, jarvis):
    jarvis.data["tts_cache"] = {"deadbeef": (b"RIFFfake-wav", "audio/wav")}

    response = client.get("/api/tts_proxy/deadbeef.wav")
    assert response.status_code == 200
    assert response.content == b"RIFFfake-wav"
    assert response.headers["content-type"].startswith("audio/wav")
    assert client.get("/api/tts_proxy/unknown.wav").status_code == 404


def test_webhook_dispatches_to_the_registered_handler(client, jarvis):
    handler = RecordingWebhook()
    jarvis.data["webhooks"] = {"doorbell": handler}

    response = client.post("/api/webhook/doorbell?who=postman", json={"pressed": True})
    assert response.status_code == 200
    assert response.json() == {"webhook_id": "doorbell", "delivered": 1}
    assert handler.calls[0]["data"] == {"pressed": True}
    assert handler.calls[0]["query"] == {"who": "postman"}
    assert client.post("/api/webhook/nope").status_code == 404


def test_webhook_can_be_locked_down_by_config(client, jarvis, token):
    jarvis.config = {"jarvis": {"webhook_require_auth": True}}
    jarvis.data["webhooks"] = {"doorbell": RecordingWebhook()}

    assert client.post("/api/webhook/doorbell").status_code == 401
    assert client.post("/api/webhook/doorbell", headers=headers(token)).status_code == 200


def test_registry_crud_over_rest(client, jarvis, token):
    async def seed():
        await jarvis.entities.async_get_or_create(
            "light", "demo", "uid-1", "kitchen_light", name="Kitchen Light"
        )

    asyncio.run(seed())

    entities = client.get("/api/config/entity_registry/list", headers=headers(token))
    assert entities.status_code == 200
    assert entities.json()[0]["entity_id"] == "light.kitchen_light"

    updated = client.post(
        "/api/config/entity_registry/update",
        headers=headers(token),
        json={"entity_id": "light.kitchen_light", "name": "Worktop", "exposed": False},
    )
    entry = updated.json()["entity_entry"]
    assert entry["name"] == "Worktop"
    assert entry["exposed"] is False

    missing = client.post(
        "/api/config/entity_registry/update",
        headers=headers(token),
        json={"entity_id": "light.ghost", "name": "x"},
    )
    assert missing.status_code == 404

    created = client.post(
        "/api/config/area_registry/create",
        headers=headers(token),
        json={"name": "Loft", "aliases": ["attic"]},
    ).json()
    area_id = created["id"]

    renamed = client.post(
        "/api/config/area_registry/update",
        headers=headers(token),
        json={"area_id": area_id, "name": "Attic"},
    )
    assert renamed.json()["name"] == "Attic"

    listing = client.get("/api/config/area_registry/list", headers=headers(token))
    assert area_id in {area["id"] for area in listing.json()}

    deleted = client.post(
        "/api/config/area_registry/delete",
        headers=headers(token),
        json={"area_id": area_id},
    )
    assert deleted.json() == {"area_id": area_id, "deleted": True}
    assert (
        client.post(
            "/api/config/area_registry/delete",
            headers=headers(token),
            json={"area_id": area_id},
        ).status_code
        == 404
    )


def test_device_registry_over_rest(client, jarvis, token):
    async def seed():
        return await jarvis.devices.async_get_or_create(["hue:1"], "Bulb", "hue")

    device = asyncio.run(seed())

    listing = client.get("/api/config/device_registry/list", headers=headers(token))
    assert [item["id"] for item in listing.json()] == [device.id]

    updated = client.post(
        "/api/config/device_registry/update",
        headers=headers(token),
        json={"device_id": device.id, "name": "Desk Bulb", "area_id": "office"},
    )
    assert updated.json()["name"] == "Desk Bulb"
    assert updated.json()["area_id"] == "office"

    assert (
        client.post(
            "/api/config/device_registry/update",
            headers=headers(token),
            json={"device_id": "nope"},
        ).status_code
        == 404
    )


def test_token_management_over_rest(client, auth, token):
    listing = client.get("/api/auth/tokens", headers=headers(token)).json()
    assert [item["name"] for item in listing] == [TOKEN_NAME]
    assert "token_hash" not in listing[0]

    minted = client.post(
        "/api/auth/tokens", headers=headers(token), json={"name": "phone"}
    ).json()
    assert client.get("/api/states", headers=headers(minted["access_token"])).status_code == 200

    assert client.delete(f"/api/auth/tokens/{minted['id']}", headers=headers(token)).status_code == 200
    assert client.get("/api/states", headers=headers(minted["access_token"])).status_code == 401


# ---------------------------------------------------------------------------
# websocket: handshake
# ---------------------------------------------------------------------------
def test_ws_handshake(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        challenge = ws.receive_json()
        assert challenge["type"] == "auth_required"
        assert challenge["ha_version"] == "jarvis-0.1.0"

        ws.send_json({"type": "auth", "access_token": token})
        ok = ws.receive_json()
        assert ok["type"] == "auth_ok"
        assert ok["ha_version"] == "jarvis-0.1.0"


def test_ws_rejects_a_bad_token(client):
    with client.websocket_connect("/api/websocket") as ws:
        assert ws.receive_json()["type"] == "auth_required"
        ws.send_json({"type": "auth", "access_token": "not-my-token"})
        assert ws.receive_json()["type"] == "auth_invalid"


def test_ws_rejects_a_command_before_auth(client):
    with client.websocket_connect("/api/websocket") as ws:
        assert ws.receive_json()["type"] == "auth_required"
        ws.send_json({"id": 1, "type": "get_states"})
        assert ws.receive_json()["type"] == "auth_invalid"


# ---------------------------------------------------------------------------
# websocket: commands
# ---------------------------------------------------------------------------
def test_ws_ping_states_config_services(client, jarvis, token):
    jarvis.states.set("light.kitchen", "on", {"friendly_name": "Kitchen"})
    jarvis.services.register("light", "turn_on", lambda call: None, description="on")

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)

        ws.send_json({"id": 1, "type": "ping"})
        assert ws.receive_json() == {"id": 1, "type": "pong"}

        ws.send_json({"id": 2, "type": "get_states"})
        message = ws.receive_json()
        assert message["id"] == 2
        assert message["type"] == "result"
        assert message["success"] is True
        assert message["result"][0]["entity_id"] == "light.kitchen"

        ws.send_json({"id": 3, "type": "get_config"})
        assert ws.receive_json()["result"]["config_dir"] == str(jarvis.config_dir)

        ws.send_json({"id": 4, "type": "get_services"})
        assert ws.receive_json()["result"]["light"]["turn_on"]["description"] == "on"


def test_ws_unknown_command(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 9, "type": "no_such_command"})
        message = ws.receive_json()

    assert message["id"] == 9
    assert message["type"] == "result"
    assert message["success"] is False
    assert message["error"]["code"] == "unknown_command"
    assert "no_such_command" in message["error"]["message"]


def test_ws_call_service(client, jarvis, token):
    calls = []

    async def handler(call):
        calls.append(call.data)
        jarvis.states.set("light.kitchen", "on")
        return {"acked": True}

    jarvis.services.register("light", "turn_on", handler, supports_response=True)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {
                "id": 1,
                "type": "call_service",
                "domain": "light",
                "service": "turn_on",
                "service_data": {"brightness": 128},
                "target": {"entity_id": "light.kitchen"},
                "return_response": True,
            }
        )
        message = ws.receive_json()

    assert message["success"] is True
    assert calls == [{"brightness": 128, "entity_id": "light.kitchen"}]
    assert message["result"]["response"] == {"acked": True}
    assert message["result"]["changed_states"][0]["entity_id"] == "light.kitchen"
    assert message["result"]["context"]["origin"] == "api"


def test_ws_call_service_unknown(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "call_service", "domain": "light", "service": "nope"})
        message = ws.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "service_not_found"


def test_ws_subscribe_and_unsubscribe_events(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)

        ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "doorbell"})
        assert ws.receive_json()["success"] is True

        ws.send_json(
            {"id": 2, "type": "fire_event", "event_type": "doorbell", "event_data": {"n": 1}}
        )
        # The event arrives on subscription 1...
        event = ws.receive_json()
        assert event["id"] == 1
        assert event["type"] == "event"
        assert event["event"]["event_type"] == "doorbell"
        assert event["event"]["data"] == {"n": 1}
        assert event["event"]["context"]["origin"] == "api"
        # ...then the result of the fire_event command itself.
        assert ws.receive_json()["id"] == 2

        ws.send_json({"id": 3, "type": "unsubscribe_events", "subscription": 1})
        assert ws.receive_json()["success"] is True

        ws.send_json({"id": 4, "type": "fire_event", "event_type": "doorbell"})
        assert ws.receive_json()["id"] == 4  # no event frame in between

        ws.send_json({"id": 5, "type": "unsubscribe_events", "subscription": 99})
        assert ws.receive_json()["error"]["code"] == "not_found"


def test_ws_subscribe_delivers_real_state_changes(client, jarvis, token):
    async def handler(call):
        jarvis.states.set("light.kitchen", "on", {"brightness": 99})

    jarvis.services.register("light", "turn_on", handler)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
        assert ws.receive_json()["success"] is True

        ws.send_json({"id": 2, "type": "call_service", "domain": "light", "service": "turn_on"})
        event = ws.receive_json()

    assert event["id"] == 1
    assert event["event"]["event_type"] == "state_changed"
    new_state = event["event"]["data"]["new_state"]
    assert new_state["entity_id"] == "light.kitchen"
    assert new_state["state"] == "on"
    assert new_state["attributes"]["brightness"] == 99
    assert event["event"]["data"]["old_state"] is None


def test_ws_registry_commands(client, jarvis, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)

        ws.send_json({"id": 1, "type": "config/area_registry/create", "name": "Garage"})
        area = ws.receive_json()["result"]
        assert area["name"] == "Garage"

        ws.send_json({"id": 2, "type": "config/area_registry/list"})
        assert [item["name"] for item in ws.receive_json()["result"]] == ["Garage"]

        ws.send_json(
            {
                "id": 3,
                "type": "config/area_registry/update",
                "area_id": area["id"],
                "aliases": ["car port"],
            }
        )
        assert ws.receive_json()["result"]["aliases"] == ["car port"]

        ws.send_json(
            {"id": 4, "type": "config/area_registry/update", "area_id": "nope", "name": "x"}
        )
        assert ws.receive_json()["error"]["code"] == "not_found"

        ws.send_json({"id": 5, "type": "config/area_registry/delete", "area_id": area["id"]})
        assert ws.receive_json()["result"]["deleted"] is True

        ws.send_json({"id": 6, "type": "config/entity_registry/list"})
        assert ws.receive_json()["result"] == []

        ws.send_json({"id": 7, "type": "config/device_registry/list"})
        assert ws.receive_json()["result"] == []


def test_ws_entity_registry_update(client, jarvis, token):
    async def seed():
        await jarvis.entities.async_get_or_create(
            "light", "demo", "uid-1", "kitchen_light", name="Kitchen Light"
        )

    asyncio.run(seed())

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {
                "id": 1,
                "type": "config/entity_registry/update",
                "entity_id": "light.kitchen_light",
                "area_id": "kitchen",
                "aliases": ["worktop"],
            }
        )
        entry = ws.receive_json()["result"]["entity_entry"]

    assert entry["area_id"] == "kitchen"
    assert entry["aliases"] == ["worktop"]
    assert jarvis.entities.get("light.kitchen_light").area_id == "kitchen"


def test_ws_conversation_process(client, jarvis, token):
    async def process(call):
        return {
            "response": {"speech": {"plain": {"speech": "Evening, sir."}}},
            "conversation_id": call.get("conversation_id") or "new",
        }

    jarvis.services.register("conversation", "process", process, supports_response=True)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {"id": 1, "type": "conversation/process", "text": "hi", "conversation_id": "c1"}
        )
        message = ws.receive_json()

    assert message["success"] is True
    assert message["result"]["response"]["speech"]["plain"]["speech"] == "Evening, sir."
    assert message["result"]["conversation_id"] == "c1"


def test_ws_approve_reaches_the_llm_gate(client, jarvis, token):
    seen = {}

    async def approve(call):
        seen.update(call.data)
        return {"status": "executed", "request_id": call.get("request_id")}

    jarvis.services.register("llm", "approve", approve, supports_response=True)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "jarvis/approve", "request_id": "req-7", "approved": True})
        message = ws.receive_json()

    assert seen == {"request_id": "req-7", "approved": True}
    assert message["result"]["status"] == "executed"


def test_ws_approve_defaults_to_approved_and_denies_explicitly(client, jarvis, token):
    seen = []

    async def approve(call):
        seen.append(call.get("approved"))
        return {"status": "ok"}

    jarvis.services.register("llm", "approve", approve, supports_response=True)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "jarvis/approve", "request_id": "r1"})
        ws.receive_json()
        ws.send_json({"id": 2, "type": "jarvis/approve", "request_id": "r1", "approved": False})
        ws.receive_json()

    assert seen == [True, False]


def test_ws_approve_without_the_llm_integration(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "jarvis/approve", "request_id": "req-7"})
        message = ws.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "not_supported"


# ---------------------------------------------------------------------------
# websocket: assist_pipeline
# ---------------------------------------------------------------------------
def test_pipeline_list_returns_the_jarvis_pipeline(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "assist_pipeline/pipeline/list"})
        result = ws.receive_json()["result"]

    assert "Jarvis" in [pipeline["name"] for pipeline in result["pipelines"]]
    assert result["preferred_pipeline"] in [p["id"] for p in result["pipelines"]]


def test_pipeline_list_uses_the_voice_store(client, jarvis, token):
    from jarvis.voice.pipelines import PipelineStore

    store = PipelineStore()
    asyncio.run(store.async_load_config([{"name": "Jarvis"}, {"name": "Guest"}]))
    voice = FakeVoice(jarvis)
    voice.pipelines = store
    jarvis.data["voice"] = voice

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "assist_pipeline/pipeline/list"})
        result = ws.receive_json()["result"]

    assert {p["name"] for p in result["pipelines"]} == {"Jarvis", "Guest"}
    assert result["preferred_pipeline"] == "jarvis"
    assert "wake_word_id" in result["pipelines"][0]  # HA client alias


def test_pipeline_run_streams_events_and_receives_audio(client, jarvis, token):
    voice = FakeVoice(jarvis)
    jarvis.data["voice"] = voice
    pcm = bytes(range(256)) * 8  # 2048 bytes of Int16LE nonsense

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {
                "id": 5,
                "type": "assist_pipeline/run",
                "start_stage": "stt",
                "end_stage": "tts",
                "input": {"sample_rate": SAMPLE_RATE},
                "conversation_id": None,
            }
        )
        # HA confirms the subscription before any event.
        assert ws.receive_json() == {"id": 5, "type": "result", "success": True, "result": None}

        started = ws.receive_json()
        assert started["id"] == 5
        assert started["type"] == "event"
        assert started["event"]["type"] == "run-start"
        assert started["event"]["timestamp"]
        assert started["event"]["data"]["pipeline"]
        assert started["event"]["data"]["language"] == "en"

        handler_id = started["event"]["data"]["runner_data"]["stt_binary_handler_id"]
        assert isinstance(handler_id, int) and 1 <= handler_id <= 255

        # Binary frames: handler-id byte + PCM, then a lone id byte to finish.
        ws.send_bytes(bytes([handler_id]) + pcm)
        ws.send_bytes(bytes([handler_id]))

        events = read_until(ws, "run-end")

    types = [event["type"] for event in events]
    assert types[0] == "stt-start"
    assert types[-1] == "run-end"
    for expected in ("stt-end", "intent-start", "intent-end", "tts-start", "tts-end"):
        assert expected in types
    assert "error" not in types

    # The binary frame really reached the run's audio queue.
    assert voice.stt.audio == pcm
    assert voice.stt.rate == SAMPLE_RATE
    assert voice.heard == "turn on the kitchen light"

    data = {event["type"]: event["data"] for event in events}
    assert data["stt-end"]["stt_output"]["text"] == "turn on the kitchen light"
    speech = data["intent-end"]["intent_output"]["response"]["speech"]["plain"]["speech"]
    assert speech == "Kitchen light is on."
    assert data["intent-end"]["intent_output"]["conversation_id"]
    assert data["tts-end"]["tts_output"]["url"].startswith("/api/tts_proxy/")
    assert data["tts-end"]["tts_output"]["mime_type"] == "audio/wav"
    assert data["intent-progress"]["chat_log_delta"]["content"] == "Kitchen light is on."


def test_pipeline_run_tts_audio_is_served_by_the_proxy(client, jarvis, token):
    jarvis.data["voice"] = FakeVoice(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {
                "id": 1,
                "type": "assist_pipeline/run",
                "start_stage": "intent",
                "end_stage": "tts",
                "input": {"text": "are the lights on?"},
            }
        )
        assert ws.receive_json()["type"] == "result"
        events = read_until(ws, "run-end")

    url = next(e["data"]["tts_output"]["url"] for e in events if e["type"] == "tts-end")
    response = client.get(url)  # no bearer token: audio players cannot send one
    assert response.status_code == 200
    with wave.open(BytesIO(response.content)) as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() > 0


def test_pipeline_run_reports_errors_as_events(client, jarvis, token):
    class BrokenVoice(FakeVoice):
        def async_create_run(self, pipeline=None, **kwargs):
            return PipelineRun(self.jarvis, stt=None, tts=None, converse=None, **kwargs)

    jarvis.data["voice"] = BrokenVoice(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "assist_pipeline/run", "start_stage": "stt"})
        assert ws.receive_json()["type"] == "result"
        events = read_until(ws, "run-end")

    error = next(event for event in events if event["type"] == "error")
    assert error["data"]["code"] == "stt-provider-missing"
    assert error["data"]["message"]


def test_binary_frame_for_an_unknown_handler_is_ignored(client, jarvis, token):
    jarvis.data["voice"] = FakeVoice(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_bytes(bytes([200]) + b"orphan audio")
        # The connection is still healthy and answering commands.
        ws.send_json({"id": 1, "type": "ping"})
        assert ws.receive_json() == {"id": 1, "type": "pong"}


def test_concurrent_runs_get_distinct_handler_ids(client, jarvis, token):
    jarvis.data["voice"] = FakeVoice(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        for msg_id in (1, 2):
            ws.send_json({"id": msg_id, "type": "assist_pipeline/run", "start_stage": "stt"})

        # Both runs are live and waiting for audio; their frames interleave.
        handler_ids = {}
        for _ in range(20):
            message = ws.receive_json()
            if message.get("type") == "event" and message["event"]["type"] == "run-start":
                handler_ids[message["id"]] = message["event"]["data"]["runner_data"][
                    "stt_binary_handler_id"
                ]
            if len(handler_ids) == 2:
                break

        # End both runs rather than abandoning them mid-flight: leaving live
        # runs for the teardown to cancel races `TestClient`, which cancels the
        # whole app task the instant the block exits.
        for handler_id in handler_ids.values():
            ws.send_bytes(bytes([handler_id]))
        ended = set()
        for _ in range(40):
            message = ws.receive_json()
            if message.get("type") == "event" and message["event"]["type"] == "run-end":
                ended.add(message["id"])
            if ended == set(handler_ids):
                break

    assert set(handler_ids) == {1, 2}
    assert len(set(handler_ids.values())) == 2  # one audio channel each
    assert ended == {1, 2}  # and both ran to completion, independently


def test_pipeline_run_without_a_voice_stack_uses_the_real_runner(client, token):
    """No jarvis.data['voice']: the lazily-imported PipelineRun still answers."""
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "assist_pipeline/run", "start_stage": "stt"})
        assert ws.receive_json()["type"] == "result"
        events = read_until(ws, "run-end")

    types = [event["type"] for event in events]
    assert types[0] == "run-start"
    assert "error" in types  # nothing is configured, so it fails cleanly


# ---------------------------------------------------------------------------
# app wiring
# ---------------------------------------------------------------------------
def test_index_when_no_web_client_is_built(client):
    body = client.get("/").json()
    assert body["websocket"] == "/api/websocket"
    assert body["version"]


def test_static_frontend_is_served_when_present(jarvis, auth, tmp_path):
    www = tmp_path / "www"
    www.mkdir()
    (www / "index.html").write_text("<h1>Jarvis HUD</h1>", encoding="utf-8")

    with TestClient(create_app(jarvis, static_dir=www)) as test_client:
        index = test_client.get("/")
        assert index.status_code == 200
        assert "Jarvis HUD" in index.text
        # API routes still win over the static mount.
        assert test_client.get("/healthz").json()["status"] == "ok"


def test_cors_headers_for_lan_clients(client):
    response = client.options(
        "/api/states",
        headers={
            "Origin": "http://jarvis.local:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_no_auth_manager_means_locked_not_open(jarvis, tmp_path):
    with TestClient(create_app(jarvis, static_dir=tmp_path / "no-www")) as test_client:
        assert test_client.get("/healthz").status_code == 200
        assert test_client.get("/api/states").status_code == 401
        with test_client.websocket_connect("/api/websocket") as ws:
            assert ws.receive_json()["type"] == "auth_required"
            ws.send_json({"type": "auth", "access_token": "anything"})
            assert ws.receive_json()["type"] == "auth_invalid"


def test_api_error_carries_a_code_and_status():
    err = ApiError("nope", "not today", 418)
    assert (err.code, err.message, err.status) == ("nope", "not today", 418)
    assert str(err) == "not today"


def test_malformed_websocket_json(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_text("{not json")
        message = ws.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "invalid_format"


# ---------------------------------------------------------------------------
# the entry point
# ---------------------------------------------------------------------------
def test_parse_args_defaults_and_overrides(monkeypatch):
    from jarvis.__main__ import DEFAULT_CONFIG_DIR, ENV_CONFIG_DIR, parse_args

    monkeypatch.delenv(ENV_CONFIG_DIR, raising=False)
    args = parse_args([])
    assert args.config == DEFAULT_CONFIG_DIR
    assert (args.host, args.port, args.create_token) == (None, None, None)

    monkeypatch.setenv(ENV_CONFIG_DIR, "/etc/jarvis")
    assert parse_args([]).config == "/etc/jarvis"

    args = parse_args(["-c", "/cfg", "--host", "127.0.0.1", "--port", "9000", "-v"])
    assert (args.config, args.host, args.port, args.verbose) == (
        "/cfg", "127.0.0.1", 9000, True,
    )


def test_server_options_precedence():
    from jarvis.__main__ import DEFAULT_HOST, DEFAULT_PORT, _server_options, parse_args

    empty = parse_args([])
    assert _server_options({}, empty) == (DEFAULT_HOST, DEFAULT_PORT)
    assert _server_options({"jarvis": {"host": "10.0.0.5", "port": 8123}}, empty) == (
        "10.0.0.5",
        8123,
    )
    # The nested http: block works too, and the CLI beats both.
    assert _server_options({"jarvis": {"http": {"port": 1234}}}, empty) == (
        DEFAULT_HOST,
        1234,
    )
    override = parse_args(["--host", "0.0.0.0", "--port", "9"])
    assert _server_options({"jarvis": {"host": "10.0.0.5", "port": 8123}}, override) == (
        "0.0.0.0",
        9,
    )


def test_setup_logging_reads_the_config():
    import logging

    from jarvis.__main__ import setup_logging

    root = logging.getLogger()
    before = root.level
    try:
        setup_logging({"jarvis": {"log_level": "warning"}, "logger": {"logs": {"jarvis.noisy": "error"}}})
        assert root.level == logging.WARNING
        assert logging.getLogger("jarvis.noisy").level == logging.ERROR

        setup_logging({"jarvis": {"log_level": "warning"}}, override="debug")
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(before)
        logging.getLogger("jarvis.noisy").setLevel(logging.NOTSET)


async def test_async_run_reports_a_missing_config(tmp_path):
    import logging

    from jarvis.__main__ import async_run, parse_args

    root = logging.getLogger()
    before = root.level
    try:
        code = await async_run(parse_args(["-c", str(tmp_path / "nowhere")]))
    finally:
        root.setLevel(before)
    assert code == 2


async def test_create_token_flag_mints_and_exits(tmp_path, monkeypatch, capsys):
    import logging

    from jarvis.__main__ import async_run, parse_args

    monkeypatch.delenv(ENV_TOKEN, raising=False)
    (tmp_path / "configuration.yaml").write_text("jarvis:\n  name: Test\n", encoding="utf-8")

    root = logging.getLogger()
    before = root.level
    try:
        code = await async_run(
            parse_args(["-c", str(tmp_path), "--create-token", "phone"])
        )
    finally:
        root.setLevel(before)

    assert code == 0
    printed = capsys.readouterr().out.strip()
    assert printed  # the token itself, on stdout, for scripting

    manager = await AuthManager(Store(tmp_path, "auth")).async_load()
    info = manager.verify(printed)
    assert info is not None and info.name == "phone"


def test_ws_frames_never_interleave(client, token):
    """Events fired mid-command keep their order relative to command results."""
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "noise"})
        assert ws.receive_json()["success"] is True

        for index in range(5):
            ws.send_json(
                {
                    "id": 10 + index,
                    "type": "fire_event",
                    "event_type": "noise",
                    "event_data": {"n": index},
                }
            )

        received = [json.loads(ws.receive_text()) for _ in range(10)]

    events = [m for m in received if m["type"] == "event"]
    results = [m for m in received if m["type"] == "result"]
    assert [event["event"]["data"]["n"] for event in events] == [0, 1, 2, 3, 4]
    assert [result["id"] for result in results] == [10, 11, 12, 13, 14]


# ---------------------------------------------------------------------------
# regression tests
#
# Every test below stands for a defect that was live in this API layer.
# ---------------------------------------------------------------------------
def gated_lock(jarvis):
    """A real Tier-3 tool held by the real safety gate, wired to `llm.approve`.

    Nothing is faked: this is `jarvis.llm.tools.ToolRegistry` with the same
    `parse_approved` the llm integration installs, so a test here proves what
    the front door actually does.
    """
    from jarvis.integrations.llm import parse_approved
    from jarvis.llm.tools import TIER_APPROVAL, ToolRegistry

    registry = ToolRegistry(jarvis)
    unlocked = []

    async def unlock(args, context=None):
        unlocked.append(args)
        return {"status": "ok"}

    registry.register(
        name="lock_control",
        description="Unlock the front door",
        handler=unlock,
        tier=TIER_APPROVAL,
    )

    async def handle_approve(call):
        return await registry.approve_request(
            str(call.get("request_id") or ""), parse_approved(call.get("approved"))
        )

    jarvis.services.register("llm", "approve", handle_approve, supports_response=True)

    held = asyncio.run(registry.call("lock_control", {"action": "unlock"}, None))
    assert held["status"] == "approval_required"  # the gate really is holding it
    return held["request_id"], unlocked


@pytest.mark.parametrize("refusal", ["false", "False", "no", "deny", "0", 0, False])
def test_rest_approve_treats_a_non_affirmative_as_a_refusal(
    client, jarvis, token, refusal
):
    """`approved: "false"` must not unlock the front door.

    `bool("false")` is True, so casting the flag before handing it to
    `llm.approve` turned an explicit deny — which is how the phone's Deny
    button arrives once it has been through a JSON string field — into
    execution of the very action the gate was holding.
    """
    request_id, unlocked = gated_lock(jarvis)

    response = client.post(
        "/api/jarvis/approve",
        json={"request_id": request_id, "approved": refusal},
        headers=headers(token),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "denied"
    assert unlocked == []  # the Tier-3 handler never ran


@pytest.mark.parametrize("refusal", ["false", "no", "0", False])
def test_ws_approve_treats_a_non_affirmative_as_a_refusal(client, jarvis, token, refusal):
    request_id, unlocked = gated_lock(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json(
            {
                "id": 1,
                "type": "jarvis/approve",
                "request_id": request_id,
                "approved": refusal,
            }
        )
        message = ws.receive_json()

    assert message["result"]["status"] == "denied"
    assert unlocked == []


@pytest.mark.parametrize("affirmative", [None, True, "true", "YES", "on", "approve", 1])
def test_approve_still_executes_on_an_affirmative(client, jarvis, token, affirmative):
    """The gate must not become impossible to pass, either."""
    request_id, unlocked = gated_lock(jarvis)

    body = {"request_id": request_id}
    if affirmative is not None:
        body["approved"] = affirmative

    response = client.post("/api/jarvis/approve", json=body, headers=headers(token))

    assert response.json()["status"] == "executed"
    assert unlocked == [{"action": "unlock"}]


def test_approval_flag_fails_closed():
    from jarvis.api.common import approval_flag

    assert approval_flag(None) is True  # "just say yes" — no flag at all
    for yes in (True, 1, 1.0, "true", " Yes ", "ON", "approve", "approved", "ok", "1"):
        assert approval_flag(yes) is True, yes
    for no in (False, 0, 2, "false", "no", "n", "off", "0", "", "maybe", [], {}, object()):
        assert approval_flag(no) is False, no


def test_ws_subscribe_with_a_reused_id_is_refused_and_leaks_no_listener(
    client, jarvis, token
):
    """A second subscription on a live id used to orphan the first listener.

    `self._subscriptions[msg_id] = unsub` overwrote the entry, so cleanup could
    not unsubscribe the first one — it stayed on the bus for the life of the
    process, serialising every matching event into a socket nobody was reading.
    """
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 7, "type": "subscribe_events", "event_type": "doorbell"})
        assert ws.receive_json()["success"] is True

        for _ in range(3):
            ws.send_json({"id": 7, "type": "subscribe_events", "event_type": "doorbell"})
            refusal = ws.receive_json()
            assert refusal["success"] is False
            assert refusal["error"]["code"] == "id_reuse"

        # A fresh id is still fine.
        ws.send_json({"id": 8, "type": "subscribe_events", "event_type": "doorbell"})
        assert ws.receive_json()["success"] is True

    assert wait_for(lambda: not jarvis.bus._listeners.get("doorbell")), (
        f"listeners survived the connection: {jarvis.bus._listeners.get('doorbell')}"
    )


def test_ws_pipeline_run_with_a_reused_id_is_refused(client, jarvis, token):
    """Two runs under one id dropped the first task: never cancelled, never freed."""
    jarvis.data["voice"] = FakeVoice(jarvis)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": 1, "type": "assist_pipeline/run", "start_stage": "stt"})
        assert ws.receive_json()["success"] is True
        run_start = ws.receive_json()
        handler_id = run_start["event"]["data"]["runner_data"]["stt_binary_handler_id"]

        ws.send_json({"id": 1, "type": "assist_pipeline/run", "start_stage": "stt"})
        for _ in range(10):
            message = ws.receive_json()
            if message.get("type") == "result":
                break
        else:  # pragma: no cover - only on a regression
            raise AssertionError("no result for the second run")

        # End the first run rather than leaving it for teardown to cancel.
        ws.send_bytes(bytes([handler_id]))
        read_until(ws, "run-end")

    assert message["success"] is False
    assert message["error"]["code"] == "id_reuse"


def test_ws_a_non_scalar_id_is_rejected_not_a_crash(client, token):
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        ws.send_json({"id": ["not", "hashable"], "type": "subscribe_events"})
        message = ws.receive_json()

    assert message["success"] is False
    assert message["error"]["code"] == "invalid_format"


def test_ws_audio_is_not_starved_by_a_slow_command(client, jarvis, token):
    """Binary audio must reach a live run while a slow command is executing.

    The receive loop used to execute each command inline, so a `call_service`
    that took a few seconds also stopped the socket being read — and the audio
    frames for a voice run already in flight sat in the kernel buffer until it
    finished. Here the slow service cannot return until the audio has arrived,
    so if the two share a loop the test deadlocks rather than merely slowing.
    """
    voice = FakeVoice(jarvis)
    jarvis.data["voice"] = voice
    audio_arrived = asyncio.Event()

    original_transcribe = voice.stt.transcribe

    async def transcribe(audio, rate=SAMPLE_RATE):
        text = await original_transcribe(audio, rate)
        audio_arrived.set()
        return text

    voice.stt.transcribe = transcribe

    async def blocks_until_audio_lands(call):
        # The cap only bounds the failure case; when audio flows this returns
        # the moment the run has consumed it.
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(audio_arrived.wait(), 4)
        return None

    jarvis.services.register("demo", "slow", blocks_until_audio_lands)

    pcm = b"\x11\x22" * 320
    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        # A short deadline on the run, so starvation shows up as a failed run
        # rather than merely a slow one.
        ws.send_json(
            {"id": 1, "type": "assist_pipeline/run", "start_stage": "stt", "timeout": 1.5}
        )
        assert ws.receive_json()["success"] is True
        run_start = ws.receive_json()
        handler_id = run_start["event"]["data"]["runner_data"]["stt_binary_handler_id"]

        # Occupy the command worker, then speak.
        ws.send_json({"id": 2, "type": "call_service", "domain": "demo", "service": "slow"})
        ws.send_bytes(bytes([handler_id]) + pcm)
        ws.send_bytes(bytes([handler_id]))

        events = read_until(ws, "run-end")

    assert voice.stt.audio == pcm
    assert "error" not in [event["type"] for event in events]


def test_ws_commands_still_execute_in_the_order_they_were_sent(client, jarvis, token):
    """The queue behind the receive loop must not reorder commands.

    "Turn the light off, then on" has to stay that way; a per-command task
    would let the second overtake the first.
    """
    order = []

    async def record(call):
        # Deliberately uneven: a later command would finish first if they ran
        # concurrently.
        await asyncio.sleep(0.05 if call.get("n") == 0 else 0)
        order.append(call.get("n"))

    jarvis.services.register("demo", "record", record)

    with client.websocket_connect("/api/websocket") as ws:
        handshake(ws, token)
        for n in range(5):
            ws.send_json(
                {
                    "id": 100 + n,
                    "type": "call_service",
                    "domain": "demo",
                    "service": "record",
                    "service_data": {"n": n},
                }
            )
        results = [ws.receive_json()["id"] for _ in range(5)]

    assert order == [0, 1, 2, 3, 4]
    assert results == [100, 101, 102, 103, 104]


class FakeWebSocket:
    """Just enough Starlette WebSocket to drive `WebSocketHandler.run()` directly.

    `TestClient` cancels the whole app task the instant its `with` block exits,
    without waiting for the app to notice the disconnect — a real server does
    not — so the graceful-drain path can only be exercised from here.
    """

    def __init__(self, incoming, settle=0):
        self._incoming = list(incoming)
        # Seconds to yield between frames. At 0 every frame is delivered
        # before the command worker gets a turn, which is the interesting case
        # for "queued but not started"; a small value instead lets each
        # command actually begin before the next frame lands.
        self._settle = settle
        self.sent = []
        self.closed = None
        self.client_state = WebSocketState.CONNECTING
        self.application_state = WebSocketState.CONNECTING

    async def accept(self):
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def receive(self):
        if self._settle:
            await asyncio.sleep(self._settle)
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def close(self, code=1000):
        self.closed = code
        self.client_state = WebSocketState.DISCONNECTED
        self.application_state = WebSocketState.DISCONNECTED


def text_frame(payload):
    return {"type": "websocket.receive", "text": json.dumps(payload)}


async def test_ws_commands_finish_when_the_client_disconnects(jarvis, auth):
    """A disconnect must not abort a half-executed service call.

    Commands now run on a worker rather than inline in the receive loop, and
    the worker is stopped with a sentinel — not cancelled — precisely so that
    "unlock the door" cannot be interrupted halfway by a phone losing wifi.
    """
    _info, secret = await auth.create_token("drain")
    finished = []

    async def slow(call):
        await asyncio.sleep(0.05)
        finished.append(call.get("marker"))

    jarvis.services.register("demo", "slow", slow)

    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame(
                {
                    "id": 1,
                    "type": "call_service",
                    "domain": "demo",
                    "service": "slow",
                    "service_data": {"marker": "started"},
                }
            ),
            text_frame(
                {
                    "id": 2,
                    "type": "call_service",
                    "domain": "demo",
                    "service": "slow",
                    "service_data": {"marker": "queued"},
                }
            ),
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )

    await WebSocketHandler(jarvis, socket).run()

    # Both were received before the disconnect, so both are honoured — and in
    # order, which is what the old inline loop guaranteed.
    assert finished == ["started", "queued"]


async def test_ws_a_command_already_running_is_not_cut_off_by_the_disconnect(jarvis, auth):
    """The in-flight case, with nothing left queued behind it.

    Teardown skips the drain when the worker is idle and the queue is empty —
    that is the ordinary disconnect and it must stay cheap. The check has to
    notice a command that is *running*, though, or the shortcut cancels it.
    """
    _info, secret = await auth.create_token("inflight")
    finished = []

    async def slow(call):
        await asyncio.sleep(0.3)
        finished.append(call.get("marker"))

    jarvis.services.register("demo", "slow", slow)

    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame(
                {
                    "id": 1,
                    "type": "call_service",
                    "domain": "demo",
                    "service": "slow",
                    "service_data": {"marker": "ran"},
                }
            ),
            {"type": "websocket.disconnect", "code": 1000},
        ],
        # Long enough for the worker to pick the command up, short enough that
        # the disconnect lands while it is still running.
        settle=0.05,
    )

    await WebSocketHandler(jarvis, socket).run()

    assert finished == ["ran"]


async def test_ws_a_wedged_command_does_not_hang_the_disconnect(jarvis, auth, monkeypatch):
    """The drain is bounded: a service that never returns is cut loose."""
    from jarvis.api import websocket as ws_module

    monkeypatch.setattr(ws_module, "DRAIN_TIMEOUT", 0.1)
    _info, secret = await auth.create_token("wedged")

    async def never_returns(call):
        await asyncio.sleep(30)

    jarvis.services.register("demo", "wedged", never_returns)

    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame({"id": 1, "type": "call_service", "domain": "demo", "service": "wedged"}),
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )

    await asyncio.wait_for(WebSocketHandler(jarvis, socket).run(), 5)


async def test_ws_cleanup_finishes_even_when_the_task_is_cancelled(jarvis, auth):
    """A hard cancel — what a shutting-down server does — must still release.

    Cleanup releases the bus listeners synchronously, before it awaits
    anything, precisely so a cancellation landing during the drain cannot
    strand them on the bus for the life of the process.
    """
    _info, secret = await auth.create_token("cancelled")

    async def wedged(call):
        await asyncio.sleep(30)

    jarvis.services.register("demo", "wedged", wedged)

    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame({"id": 1, "type": "subscribe_events", "event_type": "doorbell"}),
            text_frame({"id": 2, "type": "call_service", "domain": "demo", "service": "wedged"}),
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    task = asyncio.create_task(WebSocketHandler(jarvis, socket).run())
    # Let it reach the drain, where it is stuck behind the wedged command.
    for _ in range(100):
        await asyncio.sleep(0.01)
        if jarvis.bus._listeners.get("doorbell"):
            break

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not jarvis.bus._listeners.get("doorbell")


async def test_ws_handler_cleans_up_after_itself(jarvis, auth):
    """No listener, run task or binary channel outlives the connection."""
    _info, secret = await auth.create_token("tidy")
    jarvis.data["voice"] = FakeVoice(jarvis)

    socket = FakeWebSocket(
        [
            text_frame({"type": "auth", "access_token": secret}),
            text_frame({"id": 1, "type": "subscribe_events", "event_type": "doorbell"}),
            text_frame({"id": 2, "type": "assist_pipeline/run", "start_stage": "stt"}),
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    handler = WebSocketHandler(jarvis, socket)

    await handler.run()

    assert handler._subscriptions == {}
    assert handler._runs == {}
    assert handler._binary_handlers == {}
    assert not jarvis.bus._listeners.get("doorbell")


async def test_ws_an_unauthenticated_socket_is_hung_up_on(jarvis, auth, monkeypatch):
    """A peer that never authenticates must not hold the connection forever.

    Binary frames before auth were skipped and waited on again, so anyone who
    completed the HTTP upgrade could hold a connection and a task open
    indefinitely, having proved nothing. Driven off `TestClient` because a
    regression here is a hang, and the deadline has to be the test's.
    """
    from jarvis.api import websocket as ws_module

    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT", 0.1)

    class ChattyButNeverAuthenticates(FakeWebSocket):
        async def receive(self):
            await asyncio.sleep(0.01)  # yields, so a deadline can fire
            return {"type": "websocket.receive", "bytes": b"\x01not audio"}

    socket = ChattyButNeverAuthenticates([])

    await asyncio.wait_for(WebSocketHandler(jarvis, socket).run(), 5)

    assert socket.sent[-1]["type"] == "auth_invalid"
    assert socket.closed == 1008


async def test_ws_a_valid_token_is_not_rushed_by_the_auth_deadline(jarvis, auth):
    """The deadline must not be so eager that a normal client trips on it."""
    _info, secret = await auth.create_token("prompt")
    socket = FakeWebSocket([text_frame({"type": "auth", "access_token": secret})])

    await asyncio.wait_for(WebSocketHandler(jarvis, socket).run(), 5)

    assert [frame["type"] for frame in socket.sent] == ["auth_required", "auth_ok"]


def test_post_state_rejects_attributes_that_are_not_an_object(client, token):
    """A string `attributes` reached `dict()` inside the state machine: a 500."""
    response = client.post(
        "/api/states/light.kitchen",
        json={"state": "on", "attributes": "brightness=5"},
        headers=headers(token),
    )
    assert response.status_code == 400
    assert "attributes" in response.json()["detail"]


def test_webhook_answers_every_method_it_advertises(client, jarvis):
    """HEAD included — some senders probe with it before they trust a URL."""
    hook = RecordingWebhook()
    jarvis.data["webhooks"] = {"probe-me": hook}

    for request in (client.get, client.post, client.put, client.head):
        assert request("/api/webhook/probe-me").status_code == 200

    assert [call["method"] for call in hook.calls] == ["GET", "POST", "PUT", "HEAD"]


def test_the_openapi_schema_generates_without_duplicate_operation_ids(jarvis, tmp_path):
    """A multi-method route made FastAPI warn on every boot."""
    app = create_app(jarvis, static_dir=tmp_path / "no-www")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()

    duplicates = [str(w.message) for w in caught if "Duplicate Operation ID" in str(w.message)]
    assert duplicates == []
    assert sorted(schema["paths"]["/api/webhook/{webhook_id}"]) == ["get", "post", "put"]
