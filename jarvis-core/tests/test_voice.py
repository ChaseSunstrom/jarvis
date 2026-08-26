"""Voice stack tests: Wyoming protocol, audio helpers, pipeline runner, integration.

No network, no containers, no hardware — a fake Wyoming TCP server speaking the
real framing stands in for whisper/piper/openWakeWord.
"""

import asyncio
import contextlib
import io
import json
import math
import sys
import wave
from array import array
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations import voice as voice_integration  # noqa: E402
from jarvis.store import Store  # noqa: E402
from jarvis.voice import audio as audio_helpers  # noqa: E402
from jarvis.voice.audio import (  # noqa: E402
    chunk_pcm,
    duration_seconds,
    pcm_from_wav,
    resample,
    rms,
    wav_bytes,
)
from jarvis.voice.pipeline import PipelineError, PipelineRun  # noqa: E402
from jarvis.voice.pipelines import Pipeline, PipelineStore  # noqa: E402
from jarvis.voice.wyoming import (  # noqa: E402
    WyomingError,
    WyomingEvent,
    WyomingSttClient,
    WyomingTtsClient,
    WyomingWakeClient,
    decode_header,
    encode_event,
    wyoming_info,
)

RATE = 16000
WIDTH = 2
CHANNELS = 1


# ---------------------------------------------------------------------------
# a fake Wyoming server that speaks the real framing
# ---------------------------------------------------------------------------
def frame(event_type, data=None, payload=None, inline_only=False):
    """Build a wire frame by hand (independent of our encoder)."""
    header = {"type": event_type, "version": "1.5.0"}
    data_bytes = b""
    if data and inline_only:
        header["data"] = data
    elif data:
        data_bytes = json.dumps(data).encode("utf-8")
        header["data_length"] = len(data_bytes)
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode("utf-8") + b"\n" + data_bytes + (payload or b"")


async def read_frame(reader):
    """Parse a frame by hand (independent of our decoder)."""
    line = await reader.readline()
    if not line:
        return None
    header = json.loads(line)
    data = dict(header.get("data") or {})
    if header.get("data_length"):
        raw = await reader.readexactly(int(header["data_length"]))
        data.update(json.loads(raw))
    payload = None
    if header.get("payload_length"):
        payload = await reader.readexactly(int(header["payload_length"]))
    return {"type": header["type"], "data": data, "payload": payload, "header": header}


class FakeWyomingServer:
    """Speaks enough of Wyoming for STT, TTS, wake word and `describe`."""

    def __init__(
        self,
        transcript="turn on the kitchen light",
        tts_rate=22050,
        tts_chunks=(b"\x01\x02" * 40, b"\x03\x04" * 40),
        detection="hey_jarvis",
        detect_after=2,
        error_on=None,
        info=None,
    ):
        self.transcript = transcript
        self.tts_rate = tts_rate
        self.tts_chunks = list(tts_chunks)
        self.detection = detection
        self.detect_after = detect_after
        self.error_on = error_on
        self.info = info or {"asr": [{"name": "whisper", "installed": True}]}
        self.events = []
        self.audio_payloads = []
        #: How each client connection ended: "eof" for a polite hang-up, the
        #: exception's name for a slammed door. The real containers log the
        #: latter at ERROR, which is what `test_wyoming_info_hangs_up_politely`
        #: pins.
        self.hangups = []
        self._server = None
        self._transcribing = False
        self._detecting = False
        self.port = 0

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def types(self):
        return [event["type"] for event in self.events]

    def events_of(self, event_type):
        return [event for event in self.events if event["type"] == event_type]

    async def _handle(self, reader, writer):
        chunks_seen = 0
        try:
            while True:
                try:
                    event = await read_frame(reader)
                except (ConnectionError, OSError) as err:
                    self.hangups.append(type(err).__name__)
                    return
                if event is None:
                    self.hangups.append("eof")
                    break
                self.events.append(event)
                kind = event["type"]

                if kind == self.error_on:
                    writer.write(frame("error", {"text": "boom", "code": "test"}))
                    await writer.drain()
                    break

                if kind == "describe":
                    # inline-data form, to prove the reader handles both
                    writer.write(frame("info", self.info, inline_only=True))
                    await writer.drain()

                elif kind == "audio-chunk":
                    self.audio_payloads.append(event["payload"] or b"")
                    chunks_seen += 1
                    if self.detection and self._detecting and chunks_seen >= self.detect_after:
                        writer.write(
                            frame("detection", {"name": self.detection, "timestamp": 1234})
                        )
                        await writer.drain()
                        self._detecting = False

                elif kind == "audio-stop":
                    if self._transcribing:
                        writer.write(frame("transcript", {"text": self.transcript}))
                        await writer.drain()
                        self._transcribing = False
                    elif self._detecting:
                        writer.write(frame("not-detected", {}))
                        await writer.drain()
                        self._detecting = False

                elif kind == "transcribe":
                    self._transcribing = True

                elif kind == "detect":
                    self._detecting = True

                elif kind == "synthesize":
                    writer.write(
                        frame(
                            "audio-start",
                            {"rate": self.tts_rate, "width": 2, "channels": 1, "timestamp": 0},
                        )
                    )
                    for chunk in self.tts_chunks:
                        writer.write(
                            frame(
                                "audio-chunk",
                                {"rate": self.tts_rate, "width": 2, "channels": 1},
                                payload=chunk,
                            )
                        )
                    writer.write(frame("audio-stop", {"timestamp": 100}))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def server():
    fake = await FakeWyomingServer().start()
    try:
        yield fake
    finally:
        await fake.stop()


def sine_pcm(ms=100, rate=RATE, amplitude=9000, freq=440):
    frames = int(rate * ms / 1000)
    values = array(
        "h",
        (int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(frames)),
    )
    return values.tobytes()


def silence_pcm(ms=100, rate=RATE):
    return b"\x00\x00" * int(rate * ms / 1000)


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------
def test_encode_event_framing():
    payload = b"\x01\x02\x03\x04"
    raw = encode_event(WyomingEvent("audio-chunk", {"rate": 16000, "width": 2}, payload))
    line, rest = raw.split(b"\n", 1)
    header = decode_header(line)
    assert header["type"] == "audio-chunk"
    assert header["payload_length"] == 4
    data_length = header["data_length"]
    assert json.loads(rest[:data_length]) == {"rate": 16000, "width": 2}
    assert rest[data_length:] == payload


def test_encode_event_without_data_or_payload():
    raw = encode_event(WyomingEvent("audio-stop"))
    assert raw.endswith(b"\n")
    header = decode_header(raw.strip())
    assert header["type"] == "audio-stop"
    assert "data_length" not in header
    assert "payload_length" not in header


def test_decode_header_rejects_garbage():
    with pytest.raises(WyomingError):
        decode_header(b"not json at all")
    with pytest.raises(WyomingError):
        decode_header(b'{"no": "type"}')


def test_encode_event_inlines_only_small_data():
    """Small data is duplicated inline; a big block must not bloat the header.

    The peer reads the header with a single ``readline()`` — asyncio's default
    limit is 64 KiB — so a long `synthesize` request whose text is echoed into
    the header line is physically unreadable by the server.
    """
    small = encode_event(WyomingEvent("synthesize", {"text": "hello"}))
    assert decode_header(small.split(b"\n", 1)[0])["data"] == {"text": "hello"}

    long_text = "hello " * 20_000  # ~120 KB
    raw = encode_event(WyomingEvent("synthesize", {"text": long_text}))
    line, rest = raw.split(b"\n", 1)
    header = decode_header(line)
    assert len(line) < 65536, f"header line is {len(line)} bytes, peer cannot readline() it"
    assert "data" not in header
    # ...but the text still arrives, in the length-prefixed block.
    assert json.loads(rest[: header["data_length"]]) == {"text": long_text}


@asynccontextmanager
async def one_shot_server(handle):
    """Run `handle` as a Wyoming server on a free port, and shut it down after.

    The handler is wrapped so that its writer is *always* closed, which is not
    a tidiness point: Python 3.12 changed `asyncio.Server.wait_closed()` to wait
    for every connection the server accepted rather than only for the listening
    socket. A handler that returns with its writer still open therefore makes
    the shutdown below block forever — and no amount of closing on the client
    side releases it, because it is the server's own transport that is still
    attached.

    On 3.11, which is what this repo is usually developed on, `wait_closed()`
    returned the moment the listener was shut and the missing close was
    invisible. On CI's 3.12 two tests in this file hung the entire jarvis-core
    suite until the job's 20-minute timeout, reported as `cancelled` rather than
    as a failure, so 1253 tests silently stopped being checked.
    """

    async def wrapped(reader, writer):
        try:
            await handle(reader, writer)
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(wrapped, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def test_the_fake_server_shuts_down_even_when_a_client_walks_away():
    """Guard for [one_shot_server]: shutdown must not depend on the client.

    Deliberately leaves the client connection open at the end of the block, the
    exact shape that hung two tests in this file on Python 3.12. Bounded by
    `wait_for` so a regression *fails* here in ten seconds with a name attached,
    instead of parking the whole suite until CI's job timeout kills it.
    """

    async def handle(reader, writer):
        await reader.readline()
        writer.write(frame("info", {"ok": True}))
        await writer.drain()

    async def body():
        async with one_shot_server(handle) as port:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(encode_event(WyomingEvent("describe")))
            await writer.drain()
            assert await reader.readline()
            # and now walk away without closing `writer`.

    await asyncio.wait_for(body(), 10)


async def test_read_event_wraps_oversized_header_as_wyoming_error():
    """A header past the reader's limit is a protocol failure, not a ValueError."""
    from jarvis.voice.wyoming import async_read_event

    big = json.dumps({"type": "info", "data": {"x": "y" * 200_000}}).encode() + b"\n"

    async def handle(reader, writer):
        await reader.readline()
        writer.write(big)
        await writer.drain()

    async with one_shot_server(handle) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)  # default 64 KiB
        writer.write(encode_event(WyomingEvent("describe")))
        await writer.drain()
        with pytest.raises(WyomingError):
            await async_read_event(reader, 5.0)
        writer.close()


async def test_info_with_huge_inline_data_is_readable():
    """piper answering `describe` with hundreds of voices, inline form."""
    payload = {"tts": [{"name": f"voice_{i}", "description": "x" * 200} for i in range(500)]}
    line = json.dumps({"type": "info", "version": "1.5.0", "data": payload}).encode() + b"\n"
    assert len(line) > 65536  # would blow a default StreamReader

    async def handle(reader, writer):
        await reader.readline()
        writer.write(line)
        await writer.drain()

    async with one_shot_server(handle) as port:
        assert await wyoming_info("127.0.0.1", port, timeout=5.0) == payload


async def test_read_event_skips_blank_keepalive_lines():
    from jarvis.voice.wyoming import async_read_event

    async def handle(reader, writer):
        await reader.readline()
        writer.write(b"\n\n" + frame("info", {"ok": True}))
        await writer.drain()

    async with one_shot_server(handle) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode_event(WyomingEvent("describe")))
        await writer.drain()
        event = await async_read_event(reader, 5.0)
        assert event is not None and event.type == "info" and event.data == {"ok": True}
        writer.close()


# ---------------------------------------------------------------------------
# wyoming clients against the fake server
# ---------------------------------------------------------------------------
async def test_wyoming_info(server):
    info = await wyoming_info("127.0.0.1", server.port)
    assert info == {"asr": [{"name": "whisper", "installed": True}]}
    assert server.types == ["describe"]


async def test_wyoming_info_hangs_up_politely(server):
    """The client sends EOF and waits, rather than closing on a server mid-drain.

    Both Wyoming containers logged `ConnectionResetError('Connection lost')`
    at ERROR on every `describe` — the client had read the info and closed
    while the server's own drain was still in flight — and the live suite's
    stack-logs-clean check went red for a conversation that had gone
    perfectly. The server must see a clean end of stream, never a reset.
    """
    await wyoming_info("127.0.0.1", server.port)
    await asyncio.sleep(0.05)
    assert server.hangups == ["eof"]


async def test_transcribe_streams_audio_and_returns_transcript(server):
    client = WyomingSttClient("127.0.0.1", server.port, language="en")
    chunks = [sine_pcm(20), sine_pcm(20), sine_pcm(20)]

    text = await client.transcribe(iter(chunks), rate=RATE)

    assert text == "turn on the kitchen light"
    assert server.types[0] == "transcribe"
    assert server.events[0]["data"]["language"] == "en"
    assert server.types[1] == "audio-start"
    start = server.events[1]["data"]
    assert (start["rate"], start["width"], start["channels"]) == (RATE, WIDTH, CHANNELS)
    assert server.types.count("audio-chunk") == 3
    assert server.types[-1] == "audio-stop"
    assert server.audio_payloads == chunks
    # every chunk carries the audio format alongside the payload
    for event in server.events_of("audio-chunk"):
        assert event["data"]["rate"] == RATE
        assert event["data"]["width"] == WIDTH


async def test_transcribe_accepts_async_iterator(server):
    async def audio():
        for _ in range(2):
            yield sine_pcm(10)

    text = await WyomingSttClient("127.0.0.1", server.port).transcribe(audio())
    assert text == "turn on the kitchen light"
    assert server.types.count("audio-chunk") == 2


async def test_transcribe_raises_on_error_event():
    fake = await FakeWyomingServer(error_on="audio-stop").start()
    try:
        client = WyomingSttClient("127.0.0.1", fake.port)
        with pytest.raises(WyomingError, match="boom"):
            await client.transcribe([sine_pcm(10)])
    finally:
        await fake.stop()


async def test_stt_connection_refused_is_wyoming_error():
    # port 1 is not listening
    client = WyomingSttClient("127.0.0.1", 1, timeout=2.0)
    with pytest.raises(WyomingError):
        await client.transcribe([b"\x00\x00"])


async def test_synthesize_returns_pcm(server):
    client = WyomingTtsClient("127.0.0.1", server.port, voice="en_GB-alan-medium")

    pcm, rate, width, channels = await client.synthesize("hello there")

    assert pcm == b"".join(server.tts_chunks)
    assert (rate, width, channels) == (22050, 2, 1)
    request = server.events_of("synthesize")[0]["data"]
    assert request["text"] == "hello there"
    assert request["voice"] == {"name": "en_GB-alan-medium"}


async def test_synthesize_voice_override(server):
    client = WyomingTtsClient("127.0.0.1", server.port, voice="default")
    await client.synthesize("hi", voice="en_US-amy-low")
    assert server.events_of("synthesize")[0]["data"]["voice"]["name"] == "en_US-amy-low"


async def test_wake_detect(server):
    client = WyomingWakeClient("127.0.0.1", server.port, model="hey_jarvis")

    async def audio():
        for _ in range(6):
            yield sine_pcm(20)
            await asyncio.sleep(0)

    assert await client.detect(audio()) == "hey_jarvis"
    detect = server.events_of("detect")[0]["data"]
    assert detect["names"] == ["hey_jarvis"]


async def test_wake_not_detected():
    fake = await FakeWyomingServer(detection=None).start()
    try:
        client = WyomingWakeClient("127.0.0.1", fake.port, model="hey_jarvis")
        assert await client.detect([sine_pcm(20)]) is None
    finally:
        await fake.stop()


async def test_wake_detect_deadline_covers_the_whole_stream():
    """A satellite streaming forever into a mute service must not pin us.

    The client's `timeout` has to bound the streaming loop too — not just the
    wait that happens after the audio ends, which an endless microphone never
    reaches.
    """

    async def handle(reader, writer):
        # Reads everything, answers nothing (a wake service whose model died).
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                header = json.loads(line)
                if header.get("data_length"):
                    await reader.readexactly(int(header["data_length"]))
                if header.get("payload_length"):
                    await reader.readexactly(int(header["payload_length"]))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass

    async def endless_microphone():
        while True:
            yield silence_pcm(20)
            await asyncio.sleep(0.001)

    async with one_shot_server(handle) as port:
        client = WyomingWakeClient("127.0.0.1", port, timeout=0.4)
        started = asyncio.get_running_loop().time()
        # WyomingError first: WyomingTimeoutError also subclasses TimeoutError.
        with pytest.raises(WyomingError):
            await asyncio.wait_for(client.detect(endless_microphone()), 5.0)
        assert asyncio.get_running_loop().time() - started < 3.0


# ---------------------------------------------------------------------------
# audio helpers
# ---------------------------------------------------------------------------
def test_wav_bytes_is_readable_by_wave():
    pcm = sine_pcm(50)
    data = wav_bytes(pcm, RATE, WIDTH, CHANNELS)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"

    with wave.open(io.BytesIO(data), "rb") as wav_file:
        assert wav_file.getframerate() == RATE
        assert wav_file.getsampwidth() == WIDTH
        assert wav_file.getnchannels() == CHANNELS
        assert wav_file.getnframes() == len(pcm) // 2
        assert wav_file.readframes(wav_file.getnframes()) == pcm


def test_pcm_from_wav_roundtrip():
    pcm = sine_pcm(30)
    assert pcm_from_wav(wav_bytes(pcm, 22050, 2, 1)) == (pcm, 22050, 2, 1)


def test_wav_bytes_rejects_bad_format():
    with pytest.raises(ValueError):
        wav_bytes(b"\x00\x00", rate=0)
    with pytest.raises(ValueError):
        wav_bytes(b"\x00\x00", width=3)


def test_rms_and_silence():
    assert rms(b"") == 0.0
    assert rms(silence_pcm(20)) == 0.0
    loud = rms(sine_pcm(20, amplitude=9000))
    assert 5000 < loud < 8000  # sine RMS is amplitude/sqrt(2)
    assert audio_helpers.is_silence(silence_pcm(20))
    assert not audio_helpers.is_silence(sine_pcm(20))


def test_resample_to_16k():
    pcm = sine_pcm(100, rate=32000, freq=200)
    out = resample(pcm, 32000, 16000)
    assert len(out) == pytest.approx(len(pcm) // 2, abs=4)
    assert resample(pcm, 16000, 16000) == pcm  # same rate is a no-op
    assert resample(b"", 8000, 16000) == b""

    # upsampling keeps the waveform roughly as loud
    up = resample(sine_pcm(50, rate=8000, freq=200), 8000, 16000)
    assert len(up) == pytest.approx(2 * len(sine_pcm(50, rate=8000)), abs=8)
    assert rms(up) == pytest.approx(rms(sine_pcm(50, rate=8000, freq=200)), rel=0.1)


def test_chunk_pcm_and_duration():
    pcm = sine_pcm(100)
    chunks = list(chunk_pcm(pcm, chunk_ms=20, rate=RATE))
    assert len(chunks) == 5
    assert all(len(chunk) == 640 for chunk in chunks)
    assert b"".join(chunks) == pcm
    assert duration_seconds(pcm, RATE, WIDTH, CHANNELS) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# pipeline runner
# ---------------------------------------------------------------------------
class FakeStt:
    def __init__(self, text="turn on the kitchen light", error=None):
        self.text = text
        self.error = error
        self.received = b""
        self.rate = None

    async def transcribe(self, audio_iter, rate=16000):
        self.rate = rate
        async for chunk in audio_iter:
            self.received += chunk
        if self.error:
            raise self.error
        return self.text


class FakeTts:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def synthesize(self, text, voice=None):
        self.calls.append((text, voice))
        if self.error:
            raise self.error
        return (sine_pcm(40, rate=22050), 22050, 2, 1)


class FakeWake:
    def __init__(self, name="hey_jarvis"):
        self.name = name
        self.chunks = 0

    async def detect(self, audio_iter):
        async for _chunk in audio_iter:
            self.chunks += 1
            if self.chunks >= 2:
                return self.name
        return None


def make_converse(reply="Turning on the kitchen light.", error=None):
    seen = []

    async def converse(text, conversation_id):
        seen.append((text, conversation_id))
        if error:
            raise error
        for word in reply.split(" "):
            yield word + " " if word != reply.split(" ")[-1] else word

    converse.seen = seen
    return converse


async def queue_of(*chunks, end=True):
    queue = asyncio.Queue()
    for chunk in chunks:
        queue.put_nowait(chunk)
    if end:
        queue.put_nowait(None)
    return queue


def collector():
    events = []

    async def event_cb(event_type, data):
        events.append((event_type, data))

    return events, event_cb


async def test_pipeline_full_run_emits_exact_event_sequence(tmp_path):
    jarvis = Jarvis(tmp_path)
    stt, tts = FakeStt(), FakeTts()
    converse = make_converse("Turning on the kitchen light.")
    run = PipelineRun(
        jarvis,
        pipeline=Pipeline(id="jarvis", name="Jarvis", tts_voice="en_GB-alan-medium"),
        stt=stt,
        tts=tts,
        converse=converse,
        binary_handler_id=7,
    )
    events, event_cb = collector()

    queue = await queue_of(sine_pcm(30), sine_pcm(30), silence_pcm(1000))
    await run.execute(queue, event_cb)

    types = [event_type for event_type, _ in events]
    assert types == [
        "run-start",
        "stt-start",
        "stt-vad-start",
        "stt-vad-end",
        "stt-end",
        "intent-start",
        "intent-progress",
        "intent-progress",
        "intent-progress",
        "intent-progress",
        "intent-progress",
        "intent-end",
        "tts-start",
        "tts-end",
        "run-end",
    ]
    # run-start
    run_start = events[0][1]
    assert run_start["pipeline"] == "jarvis"
    assert run_start["language"] == "en"
    assert run_start["runner_data"] == {"stt_binary_handler_id": 7, "timeout": 300}
    assert isinstance(run_start["runner_data"]["stt_binary_handler_id"], int)

    by_type = {}
    for event_type, data in events:
        by_type.setdefault(event_type, []).append(data)

    # stt
    assert by_type["stt-start"][0]["engine"] == "wyoming"
    assert by_type["stt-start"][0]["metadata"]["sample_rate"] == 16000
    assert isinstance(by_type["stt-vad-start"][0]["timestamp"], int)
    assert by_type["stt-vad-end"][0]["timestamp"] > by_type["stt-vad-start"][0]["timestamp"]
    assert by_type["stt-end"][0] == {"stt_output": {"text": "turn on the kitchen light"}}
    assert stt.received == sine_pcm(30) + sine_pcm(30) + silence_pcm(1000)

    # intent
    assert by_type["intent-start"][0] == {"engine": "ollama", "language": "en"}
    deltas = [data["chat_log_delta"] for data in by_type["intent-progress"]]
    assert all(delta["role"] == "assistant" for delta in deltas)
    assert "".join(delta["content"] for delta in deltas) == "Turning on the kitchen light."
    intent_end = by_type["intent-end"][0]["intent_output"]
    assert intent_end["response"]["speech"]["plain"] == {
        "speech": "Turning on the kitchen light.",
        "extra_data": None,
    }
    assert intent_end["response"]["response_type"] == "action_done"
    assert intent_end["response"]["data"] == {}
    assert intent_end["conversation_id"] == run.conversation_id
    assert converse.seen == [("turn on the kitchen light", run.conversation_id)]

    # tts
    tts_start = by_type["tts-start"][0]
    assert tts_start["engine"] == "wyoming"
    assert tts_start["language"] == "en"
    assert tts_start["voice"] == "en_GB-alan-medium"
    assert tts_start["tts_input"] == "Turning on the kitchen light."
    assert tts.calls == [("Turning on the kitchen light.", "en_GB-alan-medium")]

    tts_output = by_type["tts-end"][0]["tts_output"]
    assert tts_output["mime_type"] == "audio/wav"
    assert tts_output["url"].startswith("/api/tts_proxy/")
    assert tts_output["url"].endswith(".wav")

    token = tts_output["url"].removeprefix("/api/tts_proxy/").removesuffix(".wav")
    cached, mime = jarvis.data["tts_cache"][token]
    assert mime == "audio/wav"
    with wave.open(io.BytesIO(cached), "rb") as wav_file:  # served audio is a real WAV
        assert wav_file.getframerate() == 22050
    assert by_type["run-end"][0] == {}
    assert run.error is None
    assert run.response_text == "Turning on the kitchen light."


async def test_pipeline_text_only_run_skips_stt(tmp_path):
    jarvis = Jarvis(tmp_path)
    run = PipelineRun(
        jarvis,
        stt=FakeStt(),
        tts=FakeTts(),
        converse=make_converse("Hello back."),
        start_stage="intent",
        conversation_id="conv-1",
    )
    events, event_cb = collector()

    await run.execute_text("hello there", event_cb)

    types = [event_type for event_type, _ in events]
    assert "stt-start" not in types and "stt-end" not in types
    assert types[0] == "run-start" and types[-1] == "run-end"
    assert "intent-start" in types and "tts-end" in types
    intent_end = [data for kind, data in events if kind == "intent-end"][0]
    assert intent_end["intent_output"]["conversation_id"] == "conv-1"


async def test_pipeline_end_stage_stt(tmp_path):
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt("what time is it"),
        tts=FakeTts(),
        converse=make_converse(),
        end_stage="stt",
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)

    types = [event_type for event_type, _ in events]
    assert types[-2:] == ["stt-end", "run-end"]
    assert "intent-start" not in types
    assert run.stt_text == "what time is it"


async def test_pipeline_end_stage_intent(tmp_path):
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt(),
        tts=FakeTts(),
        converse=make_converse("ok"),
        end_stage="intent",
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)
    types = [event_type for event_type, _ in events]
    assert "tts-start" not in types
    assert types[-2:] == ["intent-end", "run-end"]


async def test_pipeline_wake_stage(tmp_path):
    wake = FakeWake()
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt(),
        tts=FakeTts(),
        wake=wake,
        converse=make_converse("yes?"),
        start_stage="wake",
    )
    events, event_cb = collector()
    await run.execute(await queue_of(*[sine_pcm(20)] * 4), event_cb)

    types = [event_type for event_type, _ in events]
    assert types[:3] == ["run-start", "wake_word-start", "wake_word-end"]
    assert "stt-start" in types and "tts-end" in types
    wake_end = [data for kind, data in events if kind == "wake_word-end"][0]
    assert wake_end["wake_word_output"]["wake_word_id"] == "hey_jarvis"
    assert run.detected_wake_word == "hey_jarvis"


async def test_pipeline_tts_only_stage(tmp_path):
    jarvis = Jarvis(tmp_path)
    run = PipelineRun(jarvis, tts=FakeTts(), start_stage="tts", end_stage="tts")
    events, event_cb = collector()
    await run.execute(None, event_cb, text="The kettle has boiled.")

    types = [event_type for event_type, _ in events]
    assert types == ["run-start", "tts-start", "tts-end", "run-end"]
    assert run.tts_url and run.tts_token in jarvis.data["tts_cache"]


async def test_pipeline_stt_failure_emits_error_then_run_end(tmp_path):
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt(error=WyomingError("stt container is down")),
        tts=FakeTts(),
        converse=make_converse(),
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)

    types = [event_type for event_type, _ in events]
    assert types[-2:] == ["error", "run-end"]
    error = [data for kind, data in events if kind == "error"][0]
    assert error["code"] == "stt-stream-failed"
    assert "stt container is down" in error["message"]
    assert run.error is not None and run.error.code == "stt-stream-failed"


async def test_pipeline_no_text_recognized(tmp_path):
    run = PipelineRun(
        Jarvis(tmp_path), stt=FakeStt(text="  "), tts=FakeTts(), converse=make_converse()
    )
    events, event_cb = collector()
    await run.execute(await queue_of(silence_pcm(20)), event_cb)

    error = [data for kind, data in events if kind == "error"][0]
    assert error["code"] == "stt-no-text-recognized"
    assert [kind for kind, _ in events][-1] == "run-end"


async def test_pipeline_missing_provider(tmp_path):
    run = PipelineRun(Jarvis(tmp_path), stt=None, converse=make_converse())
    events, event_cb = collector()
    await run.execute(await queue_of(silence_pcm(20)), event_cb)
    error = [data for kind, data in events if kind == "error"][0]
    assert error["code"] == "stt-provider-missing"


async def test_pipeline_timeout(tmp_path):
    class SlowStt:
        async def transcribe(self, audio_iter, rate=16000):
            async for _ in audio_iter:
                pass
            await asyncio.sleep(5)
            return "too late"

    run = PipelineRun(Jarvis(tmp_path), stt=SlowStt(), converse=make_converse(), timeout=0.1)
    events, event_cb = collector()
    await run.execute(await queue_of(silence_pcm(20)), event_cb)
    error = [data for kind, data in events if kind == "error"][0]
    assert error["code"] == "timeout"


async def test_pipeline_no_vad_events_for_silence(tmp_path):
    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt(), tts=FakeTts(), converse=make_converse())
    events, event_cb = collector()
    await run.execute(await queue_of(silence_pcm(50)), event_cb)
    types = [event_type for event_type, _ in events]
    assert "stt-vad-start" not in types and "stt-vad-end" not in types


async def test_pipeline_accepts_plain_string_agent(tmp_path):
    async def converse(text, conversation_id):
        return f"you said {text}"

    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt("hi"), tts=FakeTts(), converse=converse)
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)
    deltas = [data["chat_log_delta"]["content"] for kind, data in events if kind == "intent-progress"]
    assert deltas == ["you said hi"]


async def test_pipeline_accepts_single_argument_agent(tmp_path):
    async def converse(text):
        return "ok"

    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt("hi"), tts=FakeTts(), converse=converse)
    await run.execute(await queue_of(sine_pcm(20)))
    assert run.response_text == "ok"


async def test_pipeline_accepts_dict_deltas(tmp_path):
    async def converse(text, conversation_id):
        for part in ("Kitchen ", "light on."):
            yield {"role": "assistant", "content": part}

    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt("x"), tts=FakeTts(), converse=converse)
    await run.execute(await queue_of(sine_pcm(20)))
    assert run.response_text == "Kitchen light on."


async def test_pipeline_timeout_from_a_client_is_always_bounded(tmp_path):
    """`assist_pipeline/run` lets a websocket client name the timeout.

    Nothing it sends may produce a run with no deadline: a run holds a Wyoming
    connection and a driver task open for as long as it lasts.
    """
    from jarvis.voice.pipeline import DEFAULT_TIMEOUT, MAX_TIMEOUT

    for hostile in (0, -1, -1e9, float("inf"), float("nan"), None, "nonsense"):
        run = PipelineRun(Jarvis(tmp_path), tts_cache={}, timeout=hostile)
        assert run.timeout == DEFAULT_TIMEOUT, f"timeout={hostile!r} escaped the clamp"

    assert PipelineRun(None, tts_cache={}, timeout=10**9).timeout == MAX_TIMEOUT
    assert PipelineRun(None, tts_cache={}, timeout=12.5).timeout == 12.5

    # ...and the clamped value is what the run actually enforces.
    class Hanging:
        async def transcribe(self, audio_iter, rate=16000):
            async for _ in audio_iter:
                pass
            await asyncio.sleep(30)

    run = PipelineRun(Jarvis(tmp_path), stt=Hanging(), tts_cache={}, timeout=0)
    run.timeout = 0.1  # stand in for the default, without waiting 300 s
    events, event_cb = collector()
    await run.execute(await queue_of(silence_pcm(20)), event_cb)
    assert [data for kind, data in events if kind == "error"][0]["code"] == "timeout"


async def test_pipeline_events_are_mirrored_on_the_bus(tmp_path):
    jarvis = Jarvis(tmp_path)
    seen = []
    jarvis.bus.listen("voice_pipeline_event", lambda event: seen.append(event.data))

    run = PipelineRun(
        jarvis, stt=FakeStt("hi"), tts=FakeTts(), converse=make_converse("ok")
    )
    await run.execute(await queue_of(sine_pcm(20)))

    assert [item["type"] for item in seen] == run.event_types
    assert {item["run_id"] for item in seen} == {run.run_id}
    assert seen[0]["data"]["pipeline"] == run.pipeline_id


async def test_wake_and_stt_share_one_queue_without_losing_audio(tmp_path):
    """The wake stage must hand the rest of the stream to STT, not eat it."""
    stt = FakeStt()
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=stt,
        tts=FakeTts(),
        wake=FakeWake(),  # detects on the 2nd chunk
        converse=make_converse("yes?"),
        start_stage="wake",
    )
    chunks = [bytes([index]) * 640 for index in range(6)]
    queue = await queue_of(*chunks)
    await run.execute(queue)

    assert run.error is None
    assert run.detected_wake_word == "hey_jarvis"
    # Everything after the wake word reaches STT, in order and exactly once.
    assert stt.received == b"".join(chunks[2:])
    assert queue.empty()


async def test_wake_service_timeout_is_reported_as_wake_word_timeout(tmp_path):
    class MuteWake:
        async def detect(self, audio_iter):
            async for _ in audio_iter:
                pass
            raise WyomingError("timed out waiting for wake word")

    class SlowWake(MuteWake):
        async def detect(self, audio_iter):
            async for _ in audio_iter:
                pass
            raise TimeoutError("timed out waiting for wake word")

    run = PipelineRun(Jarvis(tmp_path), wake=SlowWake(), stt=FakeStt(), start_stage="wake")
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)
    assert [d for k, d in events if k == "error"][0]["code"] == "wake-word-timeout"

    run = PipelineRun(Jarvis(tmp_path), wake=MuteWake(), stt=FakeStt(), start_stage="wake")
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)
    assert [d for k, d in events if k == "error"][0]["code"] == "wake-stream-failed"


def test_pipeline_rejects_bad_stages(tmp_path):
    with pytest.raises(ValueError):
        PipelineRun(None, start_stage="nonsense")
    with pytest.raises(ValueError):
        PipelineRun(None, start_stage="tts", end_stage="stt")


async def test_pipeline_binary_handler_ids_increment(tmp_path):
    first = PipelineRun(None, tts_cache={})
    second = PipelineRun(None, tts_cache={})
    assert second.binary_handler_id == first.binary_handler_id + 1


async def test_pipeline_against_real_wyoming_clients(server, tmp_path):
    """Full run with the real protocol clients pointed at the fake server."""
    jarvis = Jarvis(tmp_path)
    run = PipelineRun(
        jarvis,
        stt=WyomingSttClient("127.0.0.1", server.port),
        tts=WyomingTtsClient("127.0.0.1", server.port, voice="en_GB-alan-medium"),
        converse=make_converse("Done."),
        tts_voice="en_GB-alan-medium",
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20), sine_pcm(20)), event_cb)

    assert run.error is None
    assert run.stt_text == "turn on the kitchen light"
    assert run.tts_url.startswith("/api/tts_proxy/")
    cached, mime = jarvis.data["tts_cache"][run.tts_token]
    assert pcm_from_wav(cached)[1] == 22050
    assert [kind for kind, _ in events][-1] == "run-end"


# ---------------------------------------------------------------------------
# pipeline config store
# ---------------------------------------------------------------------------
async def test_pipeline_store_defaults(tmp_path):
    store = PipelineStore(store=Store(tmp_path, "voice_pipelines"))
    await store.async_load()
    await store.async_load_config(None, {"tts_voice": "en_GB-alan-medium"})

    assert [pipeline.name for pipeline in store.list()] == ["Jarvis"]
    default = store.preferred
    assert default.name == "Jarvis"
    assert default.id == "jarvis"
    assert default.language == "en"
    assert default.tts_voice == "en_GB-alan-medium"
    assert default.wake_word == "hey_jarvis"
    assert default.conversation_engine == "ollama"
    assert store.get("jarvis") is default
    assert store.get_by_name("jarvis") is default
    assert store.get("nope") is None
    assert store.resolve("nope") is default


async def test_pipeline_store_from_yaml(tmp_path):
    store = PipelineStore(store=Store(tmp_path, "voice_pipelines"))
    await store.async_load()
    await store.async_load_config(
        [
            {"name": "Jarvis", "voice": "en_GB-alan-medium", "wake_word": "hey_jarvis"},
            {"name": "Guest", "tts": "wyoming", "voice": "en_US-amy-low", "language": "en"},
        ],
        {"language": "en"},
    )

    assert sorted(pipeline.name for pipeline in store.list()) == ["Guest", "Jarvis"]
    guest = store.get_by_name("Guest")
    assert guest.tts_voice == "en_US-amy-low"
    assert guest.id == "guest"
    assert store.preferred.name == "Jarvis"


async def test_pipeline_store_persists(tmp_path):
    store = PipelineStore(store=Store(tmp_path, "voice_pipelines"))
    await store.async_load()
    await store.async_load_config(None, {"tts_voice": "en_GB-alan-medium"})
    created = await store.async_create({"name": "Bedroom", "voice": "en_US-amy-low"})
    await store.async_set_preferred(created.id)

    reloaded = PipelineStore(store=Store(tmp_path, "voice_pipelines"))
    await reloaded.async_load()
    assert sorted(pipeline.name for pipeline in reloaded.list()) == ["Bedroom", "Jarvis"]
    assert reloaded.preferred.name == "Bedroom"
    assert reloaded.get_by_name("Bedroom").tts_voice == "en_US-amy-low"

    assert await reloaded.async_update(created.id, {"voice": "en_GB-alan-medium"})
    assert reloaded.get(created.id).tts_voice == "en_GB-alan-medium"
    assert await reloaded.async_delete(created.id) is True
    assert reloaded.get(created.id) is None
    assert await reloaded.async_delete("jarvis") is False  # never delete the last one


async def test_tts_cache_is_capped(tmp_path):
    from jarvis.voice.pipeline import MAX_CACHED_TTS, store_tts_audio

    jarvis = Jarvis(tmp_path)
    tokens = [store_tts_audio(jarvis, bytes([index % 256]))[0] for index in range(MAX_CACHED_TTS + 20)]
    cache = jarvis.data["tts_cache"]
    assert len(cache) == MAX_CACHED_TTS
    assert tokens[-1] in cache  # newest kept
    assert tokens[0] not in cache  # oldest evicted


async def test_pipeline_store_update_keeps_extra_fields(tmp_path):
    store = PipelineStore(store=Store(tmp_path, "voice_pipelines"))
    await store.async_load()
    created = await store.async_create({"name": "Study", "satellite": "esp32-study"})
    assert created.extra == {"satellite": "esp32-study"}

    updated = await store.async_update(created.id, {"extra": {"room": "study"}})
    assert updated.extra == {"satellite": "esp32-study", "room": "study"}
    updated = await store.async_update(created.id, {"voice": "en_US-amy-low"})
    assert updated.tts_voice == "en_US-amy-low"
    assert updated.extra == {"satellite": "esp32-study", "room": "study"}


def test_pipeline_from_dict_aliases():
    pipeline = Pipeline.from_dict(
        {
            "name": "Study",
            "stt": "wyoming",
            "tts": "wyoming",
            "voice": "en_GB-alan-medium",
            "wake_word_id": "hey_jarvis",
            "conversation_agent": "ollama",
            "something_custom": 42,
        }
    )
    assert pipeline.id == "study"
    assert pipeline.tts_voice == "en_GB-alan-medium"
    assert pipeline.wake_word == "hey_jarvis"
    assert pipeline.conversation_engine == "ollama"
    assert pipeline.extra == {"something_custom": 42}
    assert pipeline.as_dict()["name"] == "Study"


# ---------------------------------------------------------------------------
# the voice integration
# ---------------------------------------------------------------------------
async def setup_voice(tmp_path, config=None, stt=None, tts=None, wake=None, agent=None):
    jarvis = Jarvis(tmp_path)
    if stt is not None:
        jarvis.data["voice_stt_client"] = stt
    if tts is not None:
        jarvis.data["voice_tts_client"] = tts
    if wake is not None:
        jarvis.data["voice_wake_client"] = wake
    if agent is not None:
        jarvis.data["conversation_agent"] = agent
    assert await voice_integration.async_setup(jarvis, config) is True
    return jarvis


async def test_setup_builds_wyoming_clients_from_yaml(tmp_path):
    jarvis = await setup_voice(
        tmp_path,
        {
            "stt": {"host": "10.0.0.5", "port": 10300},
            "tts": {"host": "10.0.0.5", "port": 10200, "voice": "en_GB-alan-medium"},
            "wake": {"host": "10.0.0.5", "port": 10400, "model": "hey_jarvis"},
            "pipelines": [{"name": "Jarvis"}],
        },
    )
    data = jarvis.data["voice"]
    assert isinstance(data.stt, WyomingSttClient)
    assert (data.stt.host, data.stt.port) == ("10.0.0.5", 10300)
    assert isinstance(data.tts, WyomingTtsClient)
    assert (data.tts.port, data.tts.voice) == (10200, "en_GB-alan-medium")
    assert isinstance(data.wake, WyomingWakeClient)
    assert (data.wake.port, data.wake.model) == (10400, "hey_jarvis")
    assert data.pipelines.preferred.name == "Jarvis"
    assert data.pipelines.preferred.tts_voice == "en_GB-alan-medium"
    assert jarvis.services.has_service("voice", "say")
    assert jarvis.services.has_service("voice", "get_pipelines")


async def test_setup_with_no_config_uses_defaults(tmp_path):
    jarvis = await setup_voice(tmp_path, None)
    data = jarvis.data["voice"]
    assert (data.stt.host, data.stt.port) == ("127.0.0.1", 10300)
    assert (data.tts.host, data.tts.port) == ("127.0.0.1", 10200)
    assert (data.wake.host, data.wake.port) == ("127.0.0.1", 10400)
    assert data.wake.model == "hey_jarvis"
    assert jarvis.data["tts_cache"] == {}


async def test_setup_can_disable_a_service(tmp_path):
    jarvis = await setup_voice(tmp_path, {"wake": False})
    assert jarvis.data["voice"].wake is None


async def test_voice_say_service_caches_and_fires_event(tmp_path):
    tts = FakeTts()
    jarvis = await setup_voice(tmp_path, {"tts": {"voice": "en_GB-alan-medium"}}, tts=tts)
    fired = []
    jarvis.bus.listen("voice_said", lambda event: fired.append(event))

    result = await jarvis.async_call_service(
        "voice", "say", {"text": "The garage door is open."}, return_response=True
    )

    assert result["url"].startswith("/api/tts_proxy/")
    assert result["mime_type"] == "audio/wav"
    assert tts.calls == [("The garage door is open.", "en_GB-alan-medium")]
    cached, mime = voice_integration.get_tts_audio(jarvis, result["token"])
    assert mime == "audio/wav"
    assert pcm_from_wav(cached)[1] == 22050
    assert voice_integration.get_tts_audio(jarvis, result["token"] + ".wav") is not None
    assert voice_integration.get_tts_audio(jarvis, "unknown") is None
    assert fired and fired[0].data["text"] == "The garage door is open."


async def test_voice_say_requires_text(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    with pytest.raises(ValueError):
        await jarvis.async_call_service("voice", "say", {})


async def test_voice_say_plays_on_media_player(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    calls = []

    async def _play_media(call):
        calls.append(call.data)

    jarvis.services.register("media_player", "play_media", _play_media)
    await jarvis.async_call_service(
        "voice", "say", {"text": "hello", "entity_id": "media_player.kitchen"}
    )
    assert calls and calls[0]["entity_id"] == "media_player.kitchen"
    assert calls[0]["media_id"].startswith("/api/tts_proxy/")
    assert calls[0]["media_type"] == "music"


async def test_voice_say_splits_comma_separated_targets(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    played = []

    async def _play_media(call):
        played.append(call.data["entity_id"])

    jarvis.services.register("media_player", "play_media", _play_media)
    result = await jarvis.async_call_service(
        "voice",
        "say",
        {"text": "hello", "entity_id": "media_player.kitchen, media_player.study"},
        return_response=True,
    )
    assert played == ["media_player.kitchen", "media_player.study"]
    assert result["entity_id"] == ["media_player.kitchen", "media_player.study"]
    assert result["failed"] == {}


async def test_voice_say_reports_playback_failures(tmp_path):
    """A dead speaker must not be reported to the caller as a success."""
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    played = []

    async def _play_media(call):
        entity_id = call.data["entity_id"]
        if entity_id.endswith("broken"):
            raise RuntimeError("amp offline")
        played.append(entity_id)

    jarvis.services.register("media_player", "play_media", _play_media)
    result = await jarvis.async_call_service(
        "voice",
        "say",
        {"text": "hi", "entity_id": ["media_player.a", "media_player.broken", "media_player.b"]},
        return_response=True,
    )
    # one bad target does not stop the others...
    assert played == ["media_player.a", "media_player.b"]
    assert result["played"] == ["media_player.a", "media_player.b"]
    # ...but the caller is told about it
    assert "media_player.broken" in result["failed"]
    assert "amp offline" in result["failed"]["media_player.broken"]


async def test_voice_say_reports_missing_media_player(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    result = await jarvis.async_call_service(
        "voice", "say", {"text": "hi", "entity_id": "media_player.kitchen"}, return_response=True
    )
    assert result["played"] == []
    assert "media_player.kitchen" in result["failed"]
    assert result["url"].startswith("/api/tts_proxy/")  # audio is still cached


async def test_voice_say_without_target_reports_nothing_failed(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, tts=FakeTts())
    result = await jarvis.async_call_service(
        "voice", "say", {"text": "hi"}, return_response=True
    )
    assert result["entity_id"] == [] and result["failed"] == {} and result["played"] == []


async def test_get_pipelines_service(tmp_path):
    jarvis = await setup_voice(tmp_path, {"pipelines": [{"name": "Jarvis"}, {"name": "Guest"}]})
    result = await jarvis.async_call_service(
        "voice", "get_pipelines", {}, return_response=True
    )
    assert result["preferred_pipeline"] == "jarvis"
    assert sorted(item["name"] for item in result["pipelines"]) == ["Guest", "Jarvis"]


async def test_integration_creates_runs_with_configured_clients(tmp_path):
    stt, tts = FakeStt(), FakeTts()

    async def agent(text, conversation_id):
        yield f"heard {text}"

    jarvis = await setup_voice(
        tmp_path,
        {"tts": {"voice": "en_GB-alan-medium"}},
        stt=stt,
        tts=tts,
        agent=agent,
    )
    run = voice_integration.async_create_run(jarvis)
    assert run.tts_voice == "en_GB-alan-medium"
    assert run.pipeline_id == "jarvis"

    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(20)), event_cb)

    assert run.error is None
    assert run.response_text == "heard turn on the kitchen light"
    assert [kind for kind, _ in events][-1] == "run-end"


async def test_run_without_conversation_agent_still_answers(tmp_path):
    jarvis = await setup_voice(tmp_path, {}, stt=FakeStt(), tts=FakeTts())
    run = voice_integration.async_create_run(jarvis)
    await run.execute(await queue_of(sine_pcm(20)))
    assert run.response_text == voice_integration.NO_AGENT_REPLY
    assert run.error is None


async def test_conversation_agent_via_service(tmp_path):
    jarvis = Jarvis(tmp_path)

    async def _process(call):
        return {
            "response": {"speech": {"plain": {"speech": f"echo: {call.get('text')}"}}},
            "conversation_id": call.get("conversation_id"),
        }

    jarvis.services.register("conversation", "process", _process, supports_response=True)
    jarvis.data["voice_stt_client"] = FakeStt("hello")
    jarvis.data["voice_tts_client"] = FakeTts()
    assert await voice_integration.async_setup(jarvis, {}) is True

    run = voice_integration.async_create_run(jarvis)
    await run.execute(await queue_of(sine_pcm(20)))
    assert run.response_text == "echo: hello"


async def test_create_run_without_setup_raises(tmp_path):
    with pytest.raises(PipelineError):
        voice_integration.async_create_run(Jarvis(tmp_path))


async def test_setup_rejects_bad_config(tmp_path):
    assert await voice_integration.async_setup(Jarvis(tmp_path), ["not", "a", "mapping"]) is False


async def test_voice_data_info_reports_services(tmp_path, server):
    jarvis = await setup_voice(
        tmp_path,
        {
            "stt": {"host": "127.0.0.1", "port": server.port},
            "tts": False,
            "wake": False,
        },
    )
    info = await jarvis.data["voice"].async_info()
    assert "asr" in info["stt"]
    assert "tts" not in info and "wake" not in info


# --- a reply that is only whitespace must not be handed to Piper -------------
#
# Reported as "TTS returned no audio" against a Piper container that logged
# `Ready` and was working moments earlier. Piper was fine. It had been asked to
# synthesise "\n\n": a reasoning model whose entire turn was a thinking block
# leaves the stripper whitespace to return, and `if not self.response_text` is
# false for whitespace, so the empty-reply guard let it straight through.

async def test_a_whitespace_only_reply_never_reaches_the_tts_service(tmp_path):
    async def converse(text, conversation_id):
        yield "\n"
        yield "  \n"

    tts = FakeTts()
    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt("hi"), tts=tts, converse=converse)
    await run.execute(await queue_of(sine_pcm(20)))
    assert tts.calls == [], (
        f"Piper was asked to say {tts.calls!r} — whitespace is nothing to say, "
        "and asking produces the misleading 'returned no audio'"
    )


async def test_a_real_reply_still_reaches_the_tts_service(tmp_path):
    """The guard must not have swallowed the ordinary case with it."""
    async def converse(text, conversation_id):
        yield "  Kitchen light on.  "

    tts = FakeTts()
    run = PipelineRun(Jarvis(tmp_path), stt=FakeStt("hi"), tts=tts, converse=converse)
    await run.execute(await queue_of(sine_pcm(20)))
    # Trimmed, not verbatim: `speakable()` collapses whitespace on the way to
    # the synthesiser, because a leading blank line is the normal shape of a
    # streamed reply and means nothing out loud. The reply itself — what the
    # console shows and the archive keeps — is untouched.
    assert [call[0] for call in tts.calls] == ["Kitchen light on."]


async def test_the_spoken_answer_is_the_answer_not_the_working(tmp_path):
    """The pipeline speaks `result.text`, not every delta it streamed.

    The deltas include what the model wrote in a round that then called a tool
    — a guess made before the tool ran. Out loud that is one breath containing
    both "the bed light is already off, sir" and "the bed light is now off,
    sir", and there is no screen out here to tell them apart. Found by talking
    to it; see ISSUES.md.
    """

    class Agent:
        """Just enough of ConversationAgent: a stream and a last_result."""

        def __init__(self) -> None:
            self.last_result = type(
                "R", (), {"text": "The bed light is now off, sir.",
                          "preamble": "The bed light is already off, sir. "}
            )()

        async def converse(self, text, conversation_id=None, **kwargs):
            for piece in ("The bed light is already off, sir. ",
                          "The bed light is now off, sir."):
                yield piece

    agent = Agent()
    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt("turn off the bed light"),
        tts=FakeTts(),
        converse=agent.converse,
        start_stage="intent",
        end_stage="intent",
    )
    await run.execute(None, None, text="turn off the bed light")

    assert run.response_text == "The bed light is now off, sir."
    # The deltas still carried everything: a surface showing the working can.
    progress = "".join(
        str((event.data.get("chat_log_delta") or {}).get("content") or "")
        for event in run.events
        if event.type == "intent-progress"
    )
    assert "already off" in progress


async def test_an_agent_with_no_last_result_is_left_alone(tmp_path):
    """Duck-typed, and optional: the stand-in agent and every test's two-line
    coroutine have no `last_result`, and their stream is the answer."""

    async def converse(text, conversation_id=None, **kwargs):
        yield "Very good, Sir."

    run = PipelineRun(
        Jarvis(tmp_path),
        stt=FakeStt("hello"),
        tts=FakeTts(),
        converse=converse,
        start_stage="intent",
        end_stage="intent",
    )
    await run.execute(None, None, text="hello")
    assert run.response_text == "Very good, Sir."


# --- what is actually sent to the synthesiser --------------------------------
#
# Found live, against `wyoming-piper:2.3.1`: a reply that opens with an ellipsis
# produced NO AUDIO AT ALL and failed the turn with `wave.Error: # channels not
# specified`. Piper splits its input into sentences and synthesises each; a
# leading "...?" phonemises to nothing, its wav writer closes having written no
# frames, and the whole request dies — including the perfectly speakable
# sentence after it. Measured: the same sentence without the ellipsis gave
# 183 KB of audio.
#
# A model reacting to a sound it could not make out opens with an ellipsis
# often, so this is what Jarvis says when the room is quiet and something
# rustles — the `voice-room-tone` scenario, exactly.

def test_a_leading_ellipsis_is_not_sent_to_the_synthesiser():
    from jarvis.voice.pipeline import speakable

    said = "\n\n...? Shall I fetch something, Sir, or were you merely testing the silence?"
    assert speakable(said) == (
        "Shall I fetch something, Sir, or were you merely testing the silence?"
    )


def test_text_with_nothing_pronounceable_becomes_empty_not_an_error():
    from jarvis.voice.pipeline import speakable

    for nothing in ("...?", "— !! ...", "   ", "\n\n", ""):
        assert speakable(nothing) == ""


def test_ordinary_replies_are_untouched_except_for_whitespace():
    from jarvis.voice.pipeline import speakable

    assert speakable("Done, Sir.") == "Done, Sir."
    # Newlines mean nothing out loud, and a reply that begins with one is the
    # normal shape of a streamed answer.
    assert speakable("Done, Sir.\n\nThe hall light is on.") == "Done, Sir. The hall light is on."
    # A number-only sentence is speakable; the filter is about punctuation.
    assert speakable("21.") == "21."


async def test_a_reply_that_cannot_be_spoken_skips_tts_rather_than_failing(monkeypatch):
    """The turn still ends cleanly — the text answer is the answer."""
    from jarvis.voice import pipeline as pipeline_module

    calls: list[str] = []

    class _Tts:
        def synthesize(self, text, voice=None):  # pragma: no cover - must not run
            calls.append(text)
            raise AssertionError("TTS was asked to say nothing")

    run = object.__new__(pipeline_module.PipelineRun)
    run.tts = _Tts()
    run.run_id = "test"
    url = await pipeline_module.PipelineRun._run_tts(run, "...?")
    assert url == ""
    assert not calls


async def test_synthesize_hangs_up_politely(server):
    """Every Wyoming exit is a hang-up, not only `describe`.

    Piper logged `BrokenPipeError` at ERROR three times in one live run: the
    core had read the last audio chunk and closed while the server still had
    a write in flight. The synthesis path must end the stream the way
    `wyoming_info` does — EOF first, then wait — so the server sees a clean
    end and logs nothing.
    """
    client = WyomingTtsClient("127.0.0.1", server.port)
    await client.synthesize("the kettle is on")
    await asyncio.sleep(0.05)
    assert server.hangups == ["eof"]


# --- early speech (M60) --------------------------------------------------------


async def test_the_first_sentence_is_spoken_before_the_reply_is_finished(tmp_path):
    """A finished sentence is synthesised while the model writes the next.

    The wait a person notices runs from the end of their sentence to the start
    of Jarvis's; synthesising after the model has finished puts the whole
    generation in front of the first word. With early speech the first
    sentence is a `tts-chunk` before `intent-end`, and the whole reply still
    arrives as `tts-end` for a client that plays only that.
    """
    jarvis = Jarvis(tmp_path)
    stt, tts = FakeStt(), FakeTts()
    converse = make_converse("The kitchen light is on. Good night, Sir.")
    run = PipelineRun(
        jarvis,
        pipeline=Pipeline(id="jarvis", name="Jarvis", tts_voice="en_GB-alan-medium"),
        stt=stt,
        tts=tts,
        converse=converse,
        binary_handler_id=7,
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(30), sine_pcm(30), silence_pcm(1000)), event_cb)

    types = [event_type for event_type, _ in events]
    assert "tts-chunk" in types and "tts-end" in types
    assert types.index("tts-chunk") < types.index("intent-end"), "the first sentence waited for the whole reply"
    chunk = [data for kind, data in events if kind == "tts-chunk"][0]
    assert chunk["index"] == 0 and chunk["text"] == "The kitchen light is on."
    assert chunk["tts_output"]["url"].startswith("/api/tts_proxy/")
    assert tts.calls[0][0] == "The kitchen light is on.", tts.calls
    assert tts.calls[1][0] == "The kitchen light is on. Good night, Sir."
    assert run.spoken_chunks == [chunk["tts_output"]["url"]]
    # `tts-end` still carries the whole reply, and beside it the part the
    # chunks did not cover, for a client that played them.
    end = [data for kind, data in events if kind == "tts-end"][0]["tts_output"]
    assert end["url"] == run.tts_url and end["chunks"] == 1
    assert end["remainder_url"] and end["remainder_url"] != end["url"]
    assert tts.calls[2][0] == "Good night, Sir."


async def test_early_speech_can_be_switched_off(tmp_path):
    jarvis = Jarvis(tmp_path)
    run = PipelineRun(
        jarvis,
        pipeline=Pipeline(id="jarvis", name="Jarvis"),
        stt=FakeStt(),
        tts=FakeTts(),
        converse=make_converse("The kitchen light is on. Good night, Sir."),
        early_speech=False,
    )
    events, event_cb = collector()
    await run.execute(await queue_of(sine_pcm(30), silence_pcm(1000)), event_cb)
    assert "tts-chunk" not in [event_type for event_type, _ in events]
