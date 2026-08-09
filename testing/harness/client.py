"""An async client for driving a real jarvis-core from a test.

One object speaks both halves of the contract — REST over httpx, and the
websocket that carries commands, subscribed events, voice pipeline runs and
the device channel — so a test reads as a sequence of things a user or a
device does, rather than as socket bookkeeping:

    async with JarvisClient(harness.base_url, harness.token) as jarvis:
        await jarvis.connect()                       # auth_required -> auth_ok
        run = await jarvis.run_pipeline(audio=speech_pcm())
        assert run.transcript == "turn on the lab lights"
        wav = await jarvis.get_bytes(run.tts_url)

    device = FakeDevice(jarvis, "test-laptop")
    await device.register()
    command = await device.next_command()
    await device.answer(command["command_id"], "ok")

Every wait here is a wait *for a condition* with a deadline. There is no
`sleep(2)` anywhere: the websocket is drained by one reader task that fans
frames out to futures and queues, so a test asks for the next matching frame
and gets it the moment it arrives.

Used by ``testing/e2e`` and by the desktop end-to-end agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx

try:  # websockets >= 14 keeps the asyncio client here
    from websockets.asyncio.client import connect as _ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets.client import connect as _ws_connect  # type: ignore[no-redef]

try:
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    ConnectionClosed = OSError  # type: ignore[assignment,misc]

DEFAULT_RATE = 16000
DEFAULT_WIDTH = 2
DEFAULT_CHANNELS = 1
DEFAULT_CHUNK_MS = 20

#: Comfortably above the pipeline's default VAD threshold of 200 RMS.
SPEECH_AMPLITUDE = 0.3

#: Long enough to clear the pipeline's 900 ms VAD silence window.
DEFAULT_TRAILING_SILENCE_MS = 1000

DEFAULT_TIMEOUT = 30.0

__all__ = [
    "DEFAULT_DEVICE_ACTIONS",
    "FakeDevice",
    "JarvisApiError",
    "JarvisClient",
    "PipelineRun",
    "parse_wav",
    "pcm_chunks",
    "rms",
    "silence_pcm",
    "speech_pcm",
    "tone_pcm",
]


class JarvisApiError(RuntimeError):
    """A websocket command came back ``success: false``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# audio the pipeline will treat as real speech
# ---------------------------------------------------------------------------
def tone_pcm(
    milliseconds: int,
    rate: int = DEFAULT_RATE,
    frequency: float = 220.0,
    amplitude: float = SPEECH_AMPLITUDE,
    channels: int = DEFAULT_CHANNELS,
) -> bytes:
    """Signed 16-bit LE PCM. Loud enough that the VAD calls it speech."""
    frames = max(int(rate * milliseconds / 1000), 1)
    peak = int(32767 * max(0.0, min(1.0, amplitude)))
    step = 2 * math.pi * frequency / rate
    samples = array("h", bytes(frames * channels * 2))
    for index in range(frames):
        value = int(peak * math.sin(step * index))
        for channel in range(channels):
            samples[index * channels + channel] = value
    if sys.byteorder != "little":  # pragma: no cover
        samples.byteswap()
    return samples.tobytes()


def silence_pcm(
    milliseconds: int, rate: int = DEFAULT_RATE, channels: int = DEFAULT_CHANNELS
) -> bytes:
    return bytes(max(int(rate * milliseconds / 1000), 0) * channels * DEFAULT_WIDTH)


def speech_pcm(
    speech_ms: int = 600,
    trailing_silence_ms: int = DEFAULT_TRAILING_SILENCE_MS,
    leading_silence_ms: int = 100,
    rate: int = DEFAULT_RATE,
) -> bytes:
    """Silence, then a tone, then enough silence to close the VAD window.

    That shape is what makes a run emit ``stt-vad-start`` and ``stt-vad-end``:
    the pipeline's energy VAD opens on the tone and closes after 900 ms below
    the threshold. Deterministic — the same bytes every run.
    """
    return (
        silence_pcm(leading_silence_ms, rate)
        + tone_pcm(speech_ms, rate)
        + silence_pcm(trailing_silence_ms, rate)
    )


def pcm_chunks(
    pcm: bytes,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    channels: int = DEFAULT_CHANNELS,
) -> list[bytes]:
    frame = max(width * channels, 1)
    size = max(int(rate * chunk_ms / 1000), 1) * frame
    return [pcm[offset : offset + size] for offset in range(0, len(pcm), size) if pcm[offset : offset + size]]


def rms(pcm: bytes, width: int = DEFAULT_WIDTH) -> float:
    values = array("h")
    values.frombytes(pcm[: len(pcm) - (len(pcm) % width)])
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def parse_wav(data: bytes) -> dict[str, Any]:
    """Open WAV bytes and describe them, or raise ``wave.Error``."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        return {
            "frames": frames,
            "rate": rate,
            "width": handle.getsampwidth(),
            "channels": handle.getnchannels(),
            "seconds": frames / rate if rate else 0.0,
            "pcm": handle.readframes(frames),
        }


# ---------------------------------------------------------------------------
# a pipeline run's transcript
# ---------------------------------------------------------------------------
@dataclass
class PipelineRun:
    """Every event one ``assist_pipeline/run`` produced, and what they said."""

    msg_id: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def types(self) -> list[str]:
        return [event["type"] for event in self.events]

    def event(self, event_type: str) -> dict[str, Any] | None:
        for event in self.events:
            if event["type"] == event_type:
                return event
        return None

    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == event_type]

    def data(self, event_type: str) -> dict[str, Any]:
        event = self.event(event_type)
        return dict(event.get("data") or {}) if event else {}

    @property
    def binary_handler_id(self) -> int | None:
        runner = self.data("run-start").get("runner_data") or {}
        value = runner.get("stt_binary_handler_id")
        return int(value) if isinstance(value, int) else None

    @property
    def transcript(self) -> str:
        return str((self.data("stt-end").get("stt_output") or {}).get("text") or "")

    @property
    def deltas(self) -> list[str]:
        return [
            str((event.get("data") or {}).get("chat_log_delta", {}).get("content") or "")
            for event in self.events_of("intent-progress")
        ]

    @property
    def response_text(self) -> str:
        output = self.data("intent-end").get("intent_output") or {}
        speech = ((output.get("response") or {}).get("speech") or {}).get("plain") or {}
        return str(speech.get("speech") or "")

    @property
    def conversation_id(self) -> str:
        return str((self.data("intent-end").get("intent_output") or {}).get("conversation_id") or "")

    @property
    def tts_url(self) -> str:
        return str((self.data("tts-end").get("tts_output") or {}).get("url") or "")

    @property
    def wake_word(self) -> str:
        return str((self.data("wake_word-end").get("wake_word_output") or {}).get("wake_word_id") or "")

    @property
    def error(self) -> dict[str, Any] | None:
        event = self.event("error")
        return dict(event.get("data") or {}) if event else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PipelineRun {self.types}>"


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------
class JarvisClient:
    """REST + websocket, with one reader task fanning frames out."""

    def __init__(
        self,
        base_url: str,
        token: str,
        ws_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ws_url = ws_url or (
            self.base_url.replace("https://", "wss://").replace("http://", "ws://")
            + "/api/websocket"
        )
        self.timeout = timeout

        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True)
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._streams: dict[int, asyncio.Queue] = {}
        self._closed = False

        #: Server pushes that carry no id.
        self.device_commands: asyncio.Queue = asyncio.Queue()
        self.messages: asyncio.Queue = asyncio.Queue()
        self.unrouted: asyncio.Queue = asyncio.Queue()
        #: Everything received, for a failing test to print.
        self.frames: list[dict[str, Any]] = []
        self.ha_version: str = ""
        self.device_id: str | None = None

    # --- lifecycle --------------------------------------------------------
    async def __aenter__(self) -> "JarvisClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        await self._http.aclose()

    # --- REST -------------------------------------------------------------
    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, auth: bool = True) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if auth else {}

    async def request(
        self, method: str, path: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        return await self._http.request(
            method, self._url(path), headers=self._headers(auth), **kwargs
        )

    async def get_json(self, path: str, *, auth: bool = True, **kwargs: Any) -> Any:
        response = await self.request("GET", path, auth=auth, **kwargs)
        response.raise_for_status()
        return response.json()

    async def post_json(
        self, path: str, payload: Any = None, *, auth: bool = True, **kwargs: Any
    ) -> Any:
        response = await self.request("POST", path, auth=auth, json=payload or {}, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def get_bytes(self, path: str, *, auth: bool = False) -> bytes:
        """Fetch a raw body. TTS audio is served unauthenticated by design."""
        response = await self.request("GET", path, auth=auth)
        response.raise_for_status()
        return response.content

    async def healthz(self) -> dict[str, Any]:
        return await self.get_json("/healthz", auth=False)

    async def states(self) -> list[dict[str, Any]]:
        return await self.get_json("/api/states")

    async def state(self, entity_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/states/{entity_id}")

    async def call_service_rest(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        *,
        return_response: bool = False,
    ) -> Any:
        params = {"return_response": "true"} if return_response else None
        return await self.post_json(
            f"/api/services/{domain}/{service}", data or {}, params=params
        )

    async def conversation(self, text: str, conversation_id: str | None = None) -> dict[str, Any]:
        return await self.post_json(
            "/api/conversation/process",
            {"text": text, "conversation_id": conversation_id},
        )

    async def wait_for_state(
        self, entity_id: str, state: str, timeout: float = 10.0, interval: float = 0.05
    ) -> dict[str, Any]:
        """Poll until an entity reaches a state. A condition, not a sleep."""
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                last = await self.state(entity_id)
            except httpx.HTTPStatusError:
                last = None
            if last is not None and last.get("state") == state:
                return last
            await asyncio.sleep(interval)
        raise AssertionError(
            f"{entity_id} did not reach {state!r} within {timeout:g}s "
            f"(last: {last.get('state') if isinstance(last, dict) else last!r})"
        )

    # --- websocket --------------------------------------------------------
    async def connect(self) -> "JarvisClient":
        """Open the socket and complete ``auth_required`` -> ``auth_ok``."""
        self._ws = await _ws_connect(
            self.ws_url,
            open_timeout=self.timeout,
            close_timeout=5,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=30,
        )
        challenge = json.loads(await self._recv("auth_required"))
        if challenge.get("type") != "auth_required":
            raise AssertionError(f"expected auth_required, got {challenge!r}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        reply = json.loads(await self._recv("the reply to auth"))
        if reply.get("type") != "auth_ok":
            raise AssertionError(f"authentication refused: {reply!r}")
        self.ha_version = str(reply.get("ha_version") or "")
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def _recv(self, what: str) -> Any:
        assert self._ws is not None
        try:
            return await asyncio.wait_for(self._ws.recv(), self.timeout)
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise AssertionError(
                f"the server did not send {what} within {self.timeout:g}s"
            ) from err

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            while True:
                raw = await self._ws.recv()
                if isinstance(raw, (bytes, bytearray)):
                    continue  # the server never sends us binary
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                self.frames.append(frame)
                self._route(frame)
        except (ConnectionClosed, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover - transport died
            if not self._closed:
                raise
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        ConnectionError("the websocket closed before the reply arrived")
                    )

    def _route(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        msg_id = frame.get("id")
        if kind in ("result", "pong"):
            future = self._pending.pop(msg_id, None) if isinstance(msg_id, int) else None
            if future is not None and not future.done():
                future.set_result(frame)
            return
        if kind == "event":
            queue = self._streams.get(msg_id) if isinstance(msg_id, int) else None
            (queue or self.unrouted).put_nowait(frame)
            return
        if kind == "device_command":
            self.device_commands.put_nowait(frame)
            return
        if kind == "jarvis_message":
            self.messages.put_nowait(frame)
            return
        self.unrouted.put_nowait(frame)

    def _claim_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send_raw(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("connect() first")
        await self._ws.send(json.dumps(payload))

    async def send_binary(self, payload: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("connect() first")
        await self._ws.send(payload)

    async def command(
        self, msg_type: str, timeout: float | None = None, **fields: Any
    ) -> Any:
        """Send a command and return its ``result``, raising on failure."""
        frame = await self.command_frame(msg_type, timeout=timeout, **fields)
        if not frame.get("success", False):
            error = frame.get("error") or {}
            raise JarvisApiError(
                str(error.get("code") or "unknown"), str(error.get("message") or frame)
            )
        return frame.get("result")

    async def command_frame(
        self,
        msg_type: str,
        timeout: float | None = None,
        msg_id: int | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """The whole result frame — for asserting on a *failure*.

        ``msg_id`` forces the id, which is how a test can deliberately reuse
        one that is already live and see the server refuse it.
        """
        msg_id = self._claim_id() if msg_id is None else int(msg_id)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self.send_raw({"id": msg_id, "type": msg_type, **fields})
            return await asyncio.wait_for(future, timeout or self.timeout)
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise AssertionError(
                f"no reply to {msg_type!r} within {timeout or self.timeout:g}s"
            ) from err
        finally:
            self._pending.pop(msg_id, None)

    async def ping(self) -> dict[str, Any]:
        return await self.command_frame("ping")

    async def get_states_ws(self) -> list[dict[str, Any]]:
        return await self.command("get_states")

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        *,
        return_response: bool = False,
        timeout: float | None = None,
    ) -> Any:
        return await self.command(
            "call_service",
            timeout=timeout,
            domain=domain,
            service=service,
            service_data=service_data or {},
            target=target,
            return_response=return_response,
        )

    async def list_pipelines(self) -> dict[str, Any]:
        return await self.command("assist_pipeline/pipeline/list")

    # --- event subscriptions ---------------------------------------------
    async def subscribe_events(self, event_type: str | None = None) -> "EventStream":
        msg_id = self._claim_id()
        queue: asyncio.Queue = asyncio.Queue()
        self._streams[msg_id] = queue
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        payload: dict[str, Any] = {"id": msg_id, "type": "subscribe_events"}
        if event_type:
            payload["event_type"] = event_type
        await self.send_raw(payload)
        try:
            frame = await asyncio.wait_for(future, self.timeout)
        except (asyncio.TimeoutError, TimeoutError) as err:
            self._streams.pop(msg_id, None)
            self._pending.pop(msg_id, None)
            raise AssertionError(
                f"subscribe_events was not confirmed within {self.timeout:g}s"
            ) from err
        if not frame.get("success", False):
            self._streams.pop(msg_id, None)
            error = frame.get("error") or {}
            raise JarvisApiError(str(error.get("code")), str(error.get("message")))
        return EventStream(self, msg_id, queue)

    # --- voice ------------------------------------------------------------
    async def run_pipeline(
        self,
        *,
        audio: bytes | None = None,
        text: str | None = None,
        pipeline: str | None = None,
        start_stage: str = "stt",
        end_stage: str = "tts",
        conversation_id: str | None = None,
        sample_rate: int = DEFAULT_RATE,
        chunk_ms: int = DEFAULT_CHUNK_MS,
        timeout: float = 60.0,
        run_timeout: float | None = None,
        send_end_of_audio: bool = True,
    ) -> PipelineRun:
        """Run one pipeline end to end and collect every event it emitted.

        Audio is streamed on the binary channel the run itself names in
        ``run-start`` — the same framing the phone and the satellites use — and
        the run is followed until ``run-end`` or the timeout.
        """
        msg_id = self._claim_id()
        queue: asyncio.Queue = asyncio.Queue()
        self._streams[msg_id] = queue
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        payload: dict[str, Any] = {
            "id": msg_id,
            "type": "assist_pipeline/run",
            "start_stage": start_stage,
            "end_stage": end_stage,
        }
        if pipeline:
            payload["pipeline"] = pipeline
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if run_timeout is not None:
            payload["timeout"] = run_timeout
        run_input: dict[str, Any] = {}
        if text is not None:
            run_input["text"] = text
        if audio is not None:
            run_input["sample_rate"] = sample_rate
        if run_input:
            payload["input"] = run_input

        run = PipelineRun(msg_id=msg_id)
        feeder: asyncio.Task | None = None
        try:
            await self.send_raw(payload)
            try:
                frame = await asyncio.wait_for(future, self.timeout)
            except (asyncio.TimeoutError, TimeoutError) as err:
                raise AssertionError(
                    f"assist_pipeline/run was not accepted within {self.timeout:g}s"
                ) from err
            if not frame.get("success", False):
                error = frame.get("error") or {}
                raise JarvisApiError(
                    str(error.get("code") or "unknown"), str(error.get("message") or frame)
                )

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"pipeline run did not finish within {timeout:g}s; "
                        f"saw {run.types}"
                    )
                try:
                    event_frame = await asyncio.wait_for(queue.get(), remaining)
                except (asyncio.TimeoutError, TimeoutError) as err:
                    raise AssertionError(
                        f"pipeline run stalled after {timeout:g}s; "
                        f"events so far: {run.types}"
                    ) from err
                event = event_frame.get("event") or {}
                run.events.append(
                    {"type": str(event.get("type") or ""), "data": event.get("data") or {}}
                )
                if run.events[-1]["type"] == "run-start" and audio is not None and feeder is None:
                    handler_id = run.binary_handler_id
                    if handler_id is None:
                        raise AssertionError(
                            f"run-start carried no stt_binary_handler_id: {run.data('run-start')}"
                        )
                    feeder = asyncio.create_task(
                        self._feed_audio(handler_id, audio, chunk_ms, sample_rate,
                                         send_end_of_audio)
                    )
                if run.events[-1]["type"] == "run-end":
                    break
        finally:
            if feeder is not None:
                if not feeder.done():
                    feeder.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await feeder
            self._streams.pop(msg_id, None)
            self._pending.pop(msg_id, None)
        return run

    async def _feed_audio(
        self,
        handler_id: int,
        audio: bytes,
        chunk_ms: int,
        rate: int,
        send_end: bool,
    ) -> None:
        prefix = bytes([handler_id & 0xFF])
        for chunk in pcm_chunks(audio, chunk_ms, rate):
            await self.send_binary(prefix + chunk)
        if send_end:
            # A lone handler-id byte is "that is all the audio".
            await self.send_binary(prefix)

    # --- the device channel ----------------------------------------------
    async def register_device(
        self,
        device_id: str,
        name: str | None = None,
        platform: str = "linux",
        capabilities: Iterable[str] | None = None,
        actions: list[dict[str, Any]] | None = None,
        app_version: str = "harness-1.0",
    ) -> dict[str, Any]:
        result = await self.command(
            "jarvis/device/register",
            device={
                "id": device_id,
                "name": name or device_id,
                "platform": platform,
                "capabilities": list(capabilities) if capabilities is not None
                else ["screen", "audio", "notifications"],
                "actions": actions if actions is not None else DEFAULT_DEVICE_ACTIONS,
                "app_version": app_version,
            },
        )
        self.device_id = device_id
        return result

    async def next_device_command(
        self, timeout: float = DEFAULT_TIMEOUT, action: str | None = None
    ) -> dict[str, Any]:
        """The next ``device_command``, optionally waiting for a named action."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"no device_command"
                    + (f" for {action!r}" if action else "")
                    + f" within {timeout:g}s"
                )
            try:
                frame = await asyncio.wait_for(self.device_commands.get(), remaining)
            except (asyncio.TimeoutError, TimeoutError) as err:
                raise AssertionError(
                    f"no device_command"
                    + (f" for {action!r}" if action else "")
                    + f" within {timeout:g}s"
                ) from err
            if action is None or frame.get("action") == action:
                return frame

    async def send_device_result(
        self,
        command_id: str,
        status: str = "ok",
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "device_result",
            "command_id": command_id,
            "status": status,
        }
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        await self.send_raw(payload)

    async def send_device_event(
        self, event: str, data: dict[str, Any] | None = None, trust: str = "trusted"
    ) -> None:
        await self.send_raw(
            {"type": "device_event", "event": event, "data": data or {}, "trust": trust}
        )

    async def send_presence(self, **signals: Any) -> None:
        await self.send_device_event("presence", dict(signals))

    async def next_message(self, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self.messages.get(), timeout)
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise AssertionError(f"no jarvis_message within {timeout:g}s") from err

    async def answer_message(
        self, message_id: str, status: str = "answered", answer: str | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "type": "jarvis_message_result",
            "message_id": message_id,
            "status": status,
        }
        if answer is not None:
            payload["answer"] = answer
        await self.send_raw(payload)


class EventStream:
    """One ``subscribe_events`` subscription, as an awaitable queue."""

    def __init__(self, client: JarvisClient, msg_id: int, queue: asyncio.Queue) -> None:
        self.client = client
        self.msg_id = msg_id
        self.queue = queue
        self.seen: list[dict[str, Any]] = []

    async def next(self, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        try:
            frame = await asyncio.wait_for(self.queue.get(), timeout)
        except (asyncio.TimeoutError, TimeoutError) as err:
            raise AssertionError(f"no event within {timeout:g}s") from err
        event = frame.get("event") or {}
        self.seen.append(event)
        return event

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """The next event matching ``predicate``. A condition, not a sleep."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"no matching event within {timeout:g}s (saw "
                    f"{[e.get('event_type') or e.get('type') for e in self.seen]})"
                )
            event = await self.next(remaining)
            if predicate(event):
                return event

    async def unsubscribe(self) -> None:
        with contextlib.suppress(Exception):
            await self.client.command("unsubscribe_events", subscription=self.msg_id)
        self.client._streams.pop(self.msg_id, None)


# ---------------------------------------------------------------------------
# a device on the other end of the channel
# ---------------------------------------------------------------------------
#: A manifest shaped like the real ones (jarvis-desktop's builtins, the phone's
#: ActionRegistry): one action per tier, plus one that returns fenced content.
DEFAULT_DEVICE_ACTIONS: list[dict[str, Any]] = [
    {
        "id": "get_system_state",
        "tier": 1,
        "description": "Battery, network and idle time.",
        "capability": "system",
        "params": {},
    },
    {
        "id": "focus_window",
        "tier": 2,
        "description": "Bring a window to the front.",
        "capability": "windows",
        "params": {"title": "which window"},
    },
    {
        "id": "lock_screen",
        "tier": 3,
        "description": "Lock the machine.",
        "capability": "session",
        "params": {},
    },
    {
        "id": "read_screen",
        "tier": 3,
        "description": "Read what is on screen right now.",
        "capability": "screen",
        "params": {},
        "untrusted_output": True,
    },
    {
        "id": "unavailable_action",
        "tier": 1,
        "description": "Something this build cannot do.",
        "available": False,
        "unsupported_reason": "not built on this platform",
        "params": {},
    },
]


class FakeDevice:
    """A device on the channel: registers, takes commands, answers them.

    The policy lives here, on the device, exactly as the real ones do — the
    server never decides whether something may run. ``deny`` and ``fail`` let
    a test make the device refuse, which is the case that matters: a refusal
    must come back as ``denied`` and must not read as success anywhere.
    """

    def __init__(
        self,
        client: JarvisClient,
        device_id: str = "harness-device",
        name: str | None = None,
        platform: str = "linux",
        actions: list[dict[str, Any]] | None = None,
        capabilities: Iterable[str] | None = None,
    ) -> None:
        self.client = client
        self.device_id = device_id
        self.name = name or device_id
        self.platform = platform
        self.actions = actions if actions is not None else DEFAULT_DEVICE_ACTIONS
        self.capabilities = list(capabilities) if capabilities is not None else [
            "screen", "audio", "notifications"
        ]
        self.received: list[dict[str, Any]] = []
        self.deny: set[str] = set()
        self.fail: dict[str, str] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self._auto: asyncio.Task | None = None

    async def register(self) -> dict[str, Any]:
        return await self.client.register_device(
            self.device_id,
            name=self.name,
            platform=self.platform,
            capabilities=self.capabilities,
            actions=self.actions,
        )

    async def next_command(
        self, timeout: float = DEFAULT_TIMEOUT, action: str | None = None
    ) -> dict[str, Any]:
        frame = await self.client.next_device_command(timeout=timeout, action=action)
        self.received.append(frame)
        return frame

    async def answer(
        self,
        command_id: str,
        status: str = "ok",
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        await self.client.send_device_result(command_id, status, result, error)

    def tier_of(self, action_id: str) -> int:
        for action in self.actions:
            if action.get("id") == action_id:
                tier = action.get("tier")
                return int(tier) if isinstance(tier, int) else 3
        return 3  # unknown action: CONFIRM, never AUTO

    async def serve(self) -> None:
        """Answer every command that arrives, forever. Cancel to stop."""
        while True:
            frame = await self.client.next_device_command(timeout=3600)
            self.received.append(frame)
            action = str(frame.get("action") or "")
            command_id = str(frame.get("command_id") or "")
            if action in self.deny:
                await self.answer(command_id, "denied", error="the user said no")
            elif action in self.fail:
                await self.answer(command_id, "error", error=self.fail[action])
            else:
                await self.answer(
                    command_id, "ok", self.results.get(action, {"ok": True, "action": action})
                )

    def start_serving(self) -> asyncio.Task:
        if self._auto is None or self._auto.done():
            self._auto = asyncio.create_task(self.serve())
        return self._auto

    async def stop_serving(self) -> None:
        task, self._auto = self._auto, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
