"""The harness proving itself against a real jarvis-core.

Nothing here is mocked on the server side. A real `python -m jarvis` process is
listening on a real socket; these tests are a real HTTP and websocket client
talking to it. The only things replaced are the two that would otherwise need a
GPU — the model and the voice containers — and both are replaced at the wire
protocol, by servers that speak the same Ollama NDJSON and the same Wyoming
framing the real ones do.

If this file is green, then for every other end-to-end suite in the repo:

* the server boots from a generated config and answers ``/healthz``,
* the deterministic token authenticates over REST and over the websocket,
  and a wrong one is refused on both,
* a full ``assist_pipeline/run`` works: audio in on the binary channel, a
  transcript out, streamed intent deltas, and playable WAV at the far end,
* a service call changes the house and the change is visible,
* the device channel round-trips a ``device_command``/``device_result``, and
  the tier on that command is only ever raised, never lowered.

Everything waits on a condition with a deadline. There are no bare sleeps, so
a slow CI runner makes this slower, never flaky.

    cd /home/user/jarvis && python3 -m pytest testing/e2e -q
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from testing.harness import (
    FakeDevice,
    JarvisApiError,
    JarvisClient,
    parse_wav,
    rms,
    silence_pcm,
    speech_pcm,
)

pytestmark = pytest.mark.e2e

TRANSCRIPT = "turn on the lab lights"
ANSWER = "Turning on the lab lights, Sir."


def _outbound_address() -> str | None:
    """This machine's own non-loopback IPv4 address, or None.

    A connect() on a UDP socket sends nothing; it just makes the kernel pick
    the source address it would route with. `gethostname()` is unreliable in a
    container, where it often resolves to 127.0.0.1 and nothing else.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routed nowhere
            address = probe.getsockname()[0]
        except OSError:
            return None
    return None if not address or address.startswith("127.") else address


@pytest.fixture(autouse=True)
def clean_script(harness):
    """Every test starts with the default brain and no served responses.

    The scripted rules are consumed in order, so a test that ran first would
    otherwise decide what the next one's model says.
    """
    harness.set_ollama_script(None)
    harness.reset_ollama()
    harness.set_transcript(TRANSCRIPT)
    yield
    harness.check_alive()


# ===========================================================================
# it is up, and it is the real thing
# ===========================================================================
async def test_healthz_reports_a_running_server_with_a_house_in_it(client, harness):
    health = await client.healthz()
    assert health["status"] == "ok"
    assert health["running"] is True
    # The demo house plus the harness's own helpers: a real state machine, not
    # an empty process that merely answers /healthz.
    assert health["entities"] > 20
    assert health["version"]


async def test_the_server_binds_every_interface_so_an_emulator_can_reach_it(harness):
    """10.0.2.2 is the emulator's route to the host; 127.0.0.1 is not."""
    assert harness.host == "0.0.0.0"
    assert harness.emulator_base_url == f"http://10.0.2.2:{harness.port}"
    assert harness.emulator_ws_url.endswith("/api/websocket")

    # Prove the bind rather than trusting the flag: reach the server over a
    # non-loopback address of this machine. 10.0.2.2 is that same route seen
    # from inside an emulator, so if this works the emulator's will too.
    address = _outbound_address()
    if address is None:
        pytest.skip("this machine has no non-loopback IPv4 address to test with")
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(f"http://{address}:{harness.port}/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_rest_refuses_a_request_with_no_token_and_one_with_a_wrong_token(harness):
    async with httpx.AsyncClient(timeout=10) as http:
        bare = await http.get(f"{harness.base_url}/api/states")
        wrong = await http.get(
            f"{harness.base_url}/api/states",
            headers={"Authorization": "Bearer definitely-not-the-token"},
        )
    assert bare.status_code == 401
    assert wrong.status_code == 401


async def test_the_websocket_refuses_a_wrong_token(anonymous):
    with pytest.raises(AssertionError, match="authentication refused"):
        await anonymous.connect()
    await anonymous.aclose()


async def test_the_websocket_handshake_and_a_ping(client):
    assert client.ha_version.startswith("jarvis-")
    pong = await client.ping()
    assert pong["type"] == "pong"


# ===========================================================================
# voice: the whole pipeline, with audio that really travels
# ===========================================================================
async def test_pipelines_are_listed_over_the_websocket(client):
    payload = await client.list_pipelines()
    names = [pipeline["name"] for pipeline in payload["pipelines"]]
    assert "Jarvis" in names
    assert payload["preferred_pipeline"]
    jarvis = next(p for p in payload["pipelines"] if p["name"] == "Jarvis")
    # The aliases Home-Assistant-shaped clients look for.
    assert jarvis["wake_word_id"] == "hey_jarvis"
    assert jarvis["language"] == "en"


async def test_a_full_pipeline_run_from_pcm_to_playable_wav(client, harness):
    """The headline test: audio in one end, speech out the other."""
    audio = speech_pcm()
    assert rms(audio) > 200, "the synthetic audio must read as speech to the VAD"

    run = await client.run_pipeline(audio=audio)
    assert run.error is None, run.error

    # --- the event sequence, in order ---------------------------------
    assert run.types[0] == "run-start"
    assert run.types[-1] == "run-end"
    for expected in (
        "run-start", "stt-start", "stt-end",
        "intent-start", "intent-end",
        "tts-start", "tts-end", "run-end",
    ):
        assert expected in run.types, f"{expected} missing from {run.types}"
    assert run.types.index("stt-end") < run.types.index("intent-start")
    assert run.types.index("intent-end") < run.types.index("tts-start")

    # --- run-start names the binary channel the audio goes down --------
    runner = run.data("run-start")["runner_data"]
    assert isinstance(runner["stt_binary_handler_id"], int)
    assert 1 <= runner["stt_binary_handler_id"] <= 255
    assert runner["timeout"] > 0
    assert run.data("run-start")["pipeline"]
    assert run.data("run-start")["language"] == "en"

    # --- the audio actually arrived at the STT service -----------------
    assert "stt-vad-start" in run.types, "the VAD never opened: no audible audio arrived"
    assert "stt-vad-end" in run.types, "the VAD never closed: the trailing silence was lost"
    assert run.transcript == TRANSCRIPT

    # --- the model streamed, rather than answering in one lump ---------
    assert len(run.deltas) > 1, f"expected streamed deltas, got {run.deltas}"
    assert "".join(run.deltas) == ANSWER
    assert run.response_text == ANSWER
    assert run.conversation_id

    # --- and the answer came back as fetchable, playable audio ---------
    assert run.tts_url.startswith("/api/tts_proxy/")
    assert run.tts_url.endswith(".wav")
    assert run.data("tts-end")["tts_output"]["mime_type"] == "audio/wav"

    wav = await client.get_bytes(run.tts_url)
    assert wav[:4] == b"RIFF"
    described = parse_wav(wav)
    assert described["frames"] > 0
    assert described["seconds"] > 0.1
    assert described["width"] == 2
    assert described["channels"] == 1
    assert rms(described["pcm"]) > 0, "the WAV is silence — no real PCM came back"


async def test_the_transcript_proves_the_audio_arrived_rather_than_being_a_constant(
    client, harness
):
    """Length mode: STT answers with how much audio it was actually sent.

    A scripted transcript alone cannot tell "the microphone stream works" from
    "the server made the answer up", because both look identical. This can.
    """
    harness.set_stt_length_mode("heard {ms} ms in {chunks} chunks")

    long_run = await client.run_pipeline(
        audio=speech_pcm(speech_ms=1000), end_stage="stt"
    )
    short_run = await client.run_pipeline(
        audio=speech_pcm(speech_ms=200), end_stage="stt"
    )

    assert long_run.transcript.startswith("heard ")
    assert short_run.transcript.startswith("heard ")

    def milliseconds(text: str) -> int:
        return int(text.split()[1])

    long_ms = milliseconds(long_run.transcript)
    short_ms = milliseconds(short_run.transcript)
    assert long_ms > short_ms + 500, (long_run.transcript, short_run.transcript)
    # 100 ms lead-in + 1000 ms tone + 1000 ms trailing silence, ±one chunk.
    assert 2050 <= long_ms <= 2150, long_run.transcript


async def test_a_run_with_no_audio_at_all_is_visibly_different(client, harness):
    harness.set_stt_length_mode("heard {ms} ms of audio")
    run = await client.run_pipeline(audio=b"", end_stage="stt")
    assert run.transcript == "heard 0 ms of audio"


async def test_a_text_run_skips_stt_and_still_speaks(client):
    run = await client.run_pipeline(text="hello", start_stage="intent")
    assert run.error is None, run.error
    assert "stt-start" not in run.types
    assert run.response_text == "Good evening, Sir. Systems nominal."
    assert run.tts_url
    wav = await client.get_bytes(run.tts_url)
    assert parse_wav(wav)["frames"] > 0


async def test_a_wake_word_run_detects_and_then_transcribes(client, harness):
    harness.set_wake_detection(detect=True, after=2)
    run = await client.run_pipeline(
        audio=speech_pcm(), start_stage="wake", end_stage="stt"
    )
    assert run.error is None, run.error
    assert run.types[:3] == ["run-start", "wake_word-start", "wake_word-end"]
    assert run.wake_word == "hey_jarvis"
    assert run.transcript == TRANSCRIPT


async def test_a_pipeline_error_is_reported_rather_than_hanging(client, harness):
    """Silence with nothing to transcribe: the run must end, with a reason."""
    harness.set_transcript("")
    run = await client.run_pipeline(audio=silence_pcm(200), end_stage="intent")
    assert run.types[-1] == "run-end"
    assert run.error is not None
    assert run.error["code"] == "stt-no-text-recognized"


async def test_the_tts_proxy_is_open_but_only_for_a_token_it_issued(client, harness):
    run = await client.run_pipeline(text="hello", start_stage="intent")
    # No Authorization header: an audio player cannot send one, which is why
    # the unguessable token in the URL is the credential.
    async with httpx.AsyncClient(timeout=10) as http:
        served = await http.get(f"{harness.base_url}{run.tts_url}")
        invented = await http.get(f"{harness.base_url}/api/tts_proxy/deadbeef.wav")
    assert served.status_code == 200
    assert served.content[:4] == b"RIFF"
    assert invented.status_code == 404


# ===========================================================================
# the house: a service call really changes something
# ===========================================================================
async def test_a_service_call_changes_state_and_the_change_is_visible(client):
    before = await client.state("light.bed_light")
    assert before["state"] in ("on", "off")

    await client.call_service(
        "light", "turn_on", {"brightness": 200}, {"entity_id": "light.bed_light"}
    )
    after = await client.wait_for_state("light.bed_light", "on")
    assert after["attributes"]["brightness"] == 200

    await client.call_service("light", "turn_off", target={"entity_id": "light.bed_light"})
    await client.wait_for_state("light.bed_light", "off")


async def test_a_service_call_is_seen_by_an_event_subscriber(client):
    stream = await client.subscribe_events("state_changed")
    try:
        await client.call_service(
            "input_boolean", "turn_on", {"entity_id": "input_boolean.harness_flag"}
        )
        event = await stream.wait_for(
            lambda e: e.get("event_type") == "state_changed"
            and e["data"].get("entity_id") == "input_boolean.harness_flag",
            timeout=15,
        )
        assert event["data"]["new_state"]["state"] == "on"
    finally:
        await client.call_service(
            "input_boolean", "turn_off", {"entity_id": "input_boolean.harness_flag"}
        )
        await stream.unsubscribe()


async def test_an_automation_in_the_generated_config_actually_runs(client):
    """The config the harness writes is a working one, not a stub."""
    await client.call_service(
        "input_text", "set_value",
        {"entity_id": "input_text.harness_note", "value": "before"},
    )
    await client.call_service(
        "input_boolean", "turn_on", {"entity_id": "input_boolean.harness_flag"}
    )
    try:
        note = await client.wait_for_state("input_text.harness_note", "flag raised", timeout=15)
        assert note["state"] == "flag raised"
    finally:
        await client.call_service(
            "input_boolean", "turn_off", {"entity_id": "input_boolean.harness_flag"}
        )


async def test_an_unknown_command_is_an_error_and_not_a_dropped_socket(client):
    frame = await client.command_frame("no/such/command")
    assert frame["success"] is False
    assert frame["error"]["code"] == "unknown_command"
    # Still usable afterwards.
    assert (await client.ping())["type"] == "pong"


# ===========================================================================
# the model: a scripted tool call really executes
# ===========================================================================
async def test_a_conversation_turn_runs_a_tool_and_then_answers(client, harness):
    await client.call_service("light", "turn_off", target={"entity_id": "light.bed_light"})
    await client.wait_for_state("light.bed_light", "off")

    reply = await client.conversation("Please turn on the lab lights")
    speech = reply["response"]["speech"]["plain"]["speech"]
    assert speech == ANSWER
    assert reply["conversation_id"]

    # The tool the fake asked for was really dispatched through the ordinary
    # service layer, so the house changed.
    await client.wait_for_state("light.bed_light", "on")

    calls = reply["response"]["data"]["tool_calls"]
    assert [call["name"] for call in calls] == ["turn_on"]
    assert calls[0]["arguments"] == {"entity_id": "light.bed_light"}

    # Two model rounds: ask for the tool, then answer with its result.
    requests = harness.ollama_requests()
    assert len(requests) == 2
    assert any(m["role"] == "tool" for m in requests[-1]["payload"]["messages"])

    await client.call_service("light", "turn_off", target={"entity_id": "light.bed_light"})


async def test_the_model_is_handed_a_real_toolbox_and_a_real_house(client, harness):
    await client.conversation("hello")
    payload = harness.ollama_requests()[-1]["payload"]

    tool_names = {
        tool["function"]["name"] for tool in payload.get("tools", []) if "function" in tool
    }
    assert {"turn_on", "turn_off", "get_state", "list_entities"} <= tool_names
    # device_control registers its tools against the same registry.
    assert "control_device" in tool_names

    system = next(m["content"] for m in payload["messages"] if m["role"] == "system")
    assert "light.bed_light" in system, "the system prompt has no live house in it"


async def test_a_model_failure_is_reported_and_does_not_kill_the_server(client, harness):
    harness.set_ollama_script(
        {"rules": [{"match": "", "responses": [{"status": 500, "error": "model exploded"}]}]}
    )
    reply = await client.conversation("anything at all")
    speech = reply["response"]["speech"]["plain"]["speech"]
    assert "language model" in speech.lower()

    harness.set_ollama_script(None)
    assert (await client.healthz())["status"] == "ok"


# ===========================================================================
# the device channel
# ===========================================================================
async def test_a_registered_device_receives_a_command_and_answers_it(client, harness):
    device = FakeDevice(client, "harness-laptop", name="Test Laptop")
    registration = await device.register()
    assert registration["ok"] is True
    assert registration["device_id"] == "harness-laptop"
    assert registration["actions"] == len(device.actions)

    # The service call blocks until the device answers, so it runs alongside.
    call = asyncio.create_task(
        client.call_service_rest(
            "device_control", "run",
            {
                "device_id": "harness-laptop",
                "action": "get_system_state",
                "reason": "Checking the battery, Sir.",
            },
            return_response=True,
        )
    )
    try:
        command = await device.next_command(timeout=20, action="get_system_state")
        assert command["type"] == "device_command"
        assert command["command_id"].startswith("c-")
        assert command["reason"] == "Checking the battery, Sir."
        assert command["tier"] == 1  # the device's own manifest said AUTO
        await device.answer(command["command_id"], "ok", {"battery": 91})

        outcome = (await asyncio.wait_for(call, 20))["service_response"]
    finally:
        if not call.done():
            call.cancel()
    assert outcome["status"] == "ok"
    assert outcome["result"] == {"battery": 91}
    assert outcome["device_id"] == "harness-laptop"


async def test_the_server_may_raise_a_tier_and_may_never_lower_one(client):
    """The invariant the whole device channel rests on.

    `effective_tier` is `max(local, requested)`. The device's own manifest is
    the floor: a caller asking for something laxer changes nothing, and an
    action the server has never heard of is CONFIRM rather than AUTO.
    """
    device = FakeDevice(client, "tier-device", name="Tier Device")
    await device.register()
    device.start_serving()
    try:
        async def dispatch(action: str, tier=None) -> dict:
            data = {"device_id": "tier-device", "action": action, "reason": "testing tiers"}
            if tier is not None:
                data["tier"] = tier
            return (await client.call_service_rest(
                "device_control", "run", data, return_response=True
            ))["service_response"]

            # (the device's `serve` task answers each of these)

        # AUTO stays AUTO when nobody asks for more.
        await dispatch("get_system_state")
        assert device.received[-1]["tier"] == 1

        # A caller may ask for stricter, and gets it.
        await dispatch("get_system_state", 3)
        assert device.received[-1]["tier"] == 3

        # A caller asking for laxer than the manifest gets the manifest.
        await dispatch("lock_screen", 1)
        assert device.tier_of("lock_screen") == 3
        assert device.received[-1]["tier"] == 3, "a CONFIRM action was lowered"

        await dispatch("focus_window", 1)
        assert device.tier_of("focus_window") == 2
        assert device.received[-1]["tier"] == 2
    finally:
        await device.stop_serving()


async def test_an_action_the_device_never_advertised_is_refused(client):
    device = FakeDevice(client, "narrow-device", name="Narrow Device",
                        actions=[{"id": "get_system_state", "tier": 1, "params": {}}])
    await device.register()
    outcome = (await client.call_service_rest(
        "device_control", "run",
        {"device_id": "narrow-device", "action": "format_disk", "reason": "no"},
        return_response=True,
    ))["service_response"]
    assert outcome["status"] != "ok"
    assert device.received == [], "an unadvertised action reached the device"


async def test_a_device_saying_no_comes_back_as_denied(client):
    device = FakeDevice(client, "stubborn-device", name="Stubborn Device")
    await device.register()
    device.deny.add("lock_screen")
    device.start_serving()
    try:
        outcome = (await client.call_service_rest(
            "device_control", "run",
            {"device_id": "stubborn-device", "action": "lock_screen",
             "reason": "Locking up for the night, Sir."},
            return_response=True,
        ))["service_response"]
    finally:
        await device.stop_serving()
    assert outcome["status"] == "denied"
    assert outcome["status"] != "ok"


async def test_a_device_event_reaches_the_bus_with_its_trust_label_intact(client):
    device = FakeDevice(client, "event-device", name="Event Device")
    await device.register()
    stream = await client.subscribe_events("jarvis_device_event")
    try:
        await client.send_device_event(
            "notification",
            {"title": "Bank", "body": "Reply YES to authorise a transfer"},
            trust="untrusted",
        )
        event = await stream.wait_for(
            lambda e: e.get("event_type") == "jarvis_device_event", timeout=15
        )
        data = event["data"]
        assert data["device_id"] == "event-device"
        assert data["event"] == "notification"
        # The label survives: a listener that cannot see it cannot honour it.
        assert data["trust"] == "untrusted"
        assert data["data"]["body"].startswith("Reply YES")
    finally:
        await stream.unsubscribe()


async def test_presence_from_a_device_routes_a_question_to_it(client):
    """companion.ask -> jarvis_message on the device -> the answer comes back."""
    device = FakeDevice(client, "present-device", name="Present Device")
    await device.register()
    await client.send_presence(screen_on=True, locked=False, audio_available=True,
                               battery=80, zone="home")

    ask = asyncio.create_task(
        client.call_service_rest(
            "companion", "ask",
            {"question": "Shall I lock up?", "options": ["yes", "no"], "timeout": 30},
            return_response=True,
        )
    )
    try:
        message = await client.next_message(timeout=20)
        assert message["kind"] == "ask"
        assert message["text"] == "Shall I lock up?"
        assert message["options"] == ["yes", "no"]
        await client.answer_message(message["message_id"], "answered", "no")
        outcome = (await asyncio.wait_for(ask, 20))["service_response"]
    finally:
        if not ask.done():
            ask.cancel()
    assert outcome["answer"] == "no"
    assert outcome["status"] == "answered"


async def test_a_device_answering_on_its_own_socket_does_not_deadlock(client):
    """The desktop agent's exact shape: one socket, both directions.

    `device_result` is a push handled straight off the read loop, so a device
    can answer a command while the same socket's command worker is still
    inside the `call_service` that issued it. If that ever regressed this test
    would time out rather than pass slowly.
    """
    device = FakeDevice(client, "one-socket", name="One Socket")
    await device.register()
    device.start_serving()
    try:
        outcome = await client.call_service(
            "device_control", "run",
            {
                "device_id": "one-socket",
                "action": "get_system_state",
                "reason": "Reading the battery over the same socket.",
            },
            return_response=True,
            timeout=30,
        )
    finally:
        await device.stop_serving()
    assert outcome["response"]["status"] == "ok"


# ===========================================================================
# the harness itself
# ===========================================================================
async def test_two_clients_can_use_the_server_at_once(harness, client):
    async with JarvisClient(harness.base_url, harness.token) as second:
        await second.connect()
        first_states, second_states = await asyncio.gather(
            client.get_states_ws(), second.get_states_ws()
        )
    assert len(first_states) == len(second_states) > 0


async def test_the_harness_reports_everything_a_device_needs(harness):
    info = harness.info()
    assert info["token"] == harness.token
    assert info["base_url"].endswith(str(harness.port))
    assert info["ws_url"].endswith("/api/websocket")
    assert set(info["ports"]) == {"core", "ollama", "stt", "tts", "wake"}
    assert all(port > 0 for port in info["ports"].values())
    assert len(set(info["ports"].values())) == 5, "two services picked the same port"
    assert set(info["logs"]) == {"fake-ollama", "fake-wyoming", "jarvis-core"}
    for path in info["logs"].values():
        assert path


async def test_the_id_of_a_live_subscription_cannot_be_reused(client):
    """A guard the server keeps; a regression here leaks bus listeners."""
    stream = await client.subscribe_events("state_changed")
    try:
        reused = await client.command_frame(
            "subscribe_events", msg_id=stream.msg_id, event_type="state_changed"
        )
        assert reused["success"] is False
        assert reused["error"]["code"] == "id_reuse"
        # The socket survives the refusal.
        assert (await client.ping())["type"] == "pong"
    finally:
        await stream.unsubscribe()


async def test_received_audio_is_kept_for_a_failing_run_to_be_listened_to(client, harness):
    """CI uploads the work directory; a voice failure should be audible."""
    await client.run_pipeline(audio=speech_pcm(), end_stage="stt")
    saved = sorted(harness.audio_dir.glob("stt-*.wav"))
    assert saved, f"nothing written to {harness.audio_dir}"
    described = parse_wav(saved[-1].read_bytes())
    assert described["rate"] == 16000
    assert described["frames"] > 0
