"""Wyoming protocol client, implemented from scratch over asyncio TCP.

Wyoming is a very small newline-delimited protocol. Every message is:

    {"type": "audio-chunk", "data": {...}, "data_length": N, "payload_length": M}\\n
    <N bytes of JSON>        # optional, overrides/extends the inline "data"
    <M bytes of binary>      # optional, raw PCM for audio-chunk

Servers we talk to (all already running as containers on the user's box):

    whisper / sherpa STT   tcp://host:10300
    piper TTS              tcp://host:10200
    openWakeWord           tcp://host:10400

Nothing here imports anything from Jarvis core, so the clients can be unit
tested against a plain ``asyncio.start_server`` fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.5.0"

DEFAULT_RATE = 16000
DEFAULT_WIDTH = 2
DEFAULT_CHANNELS = 1
DEFAULT_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0

# The header is a single `readline()` on the peer, and asyncio's StreamReader
# defaults to a 64 KiB line limit. We duplicate small `data` blocks inline for
# servers that only read the inline form, but anything bigger goes *only* in
# the length-prefixed block — otherwise a long `synthesize` request produces a
# header line the server physically cannot read.
MAX_INLINE_DATA = 4096
# ...and symmetrically, give our own reader room for servers that send a large
# `info` event using the inline form (piper with a few hundred voices).
READ_LIMIT = 4 * 1024 * 1024
# A stray blank line is a keep-alive, not a protocol violation. Bounded so a
# babbling peer cannot spin us forever.
MAX_BLANK_LINES = 32

# TTS servers (piper) usually answer at 22050 Hz; only used if the server
# never tells us the format it is sending.
FALLBACK_TTS_RATE = 22050

# --- message types ----------------------------------------------------------
TYPE_DESCRIBE = "describe"
TYPE_INFO = "info"
TYPE_TRANSCRIBE = "transcribe"
TYPE_TRANSCRIPT = "transcript"
TYPE_AUDIO_START = "audio-start"
TYPE_AUDIO_CHUNK = "audio-chunk"
TYPE_AUDIO_STOP = "audio-stop"
TYPE_SYNTHESIZE = "synthesize"
TYPE_DETECT = "detect"
TYPE_DETECTION = "detection"
TYPE_NOT_DETECTED = "not-detected"
TYPE_ERROR = "error"

__all__ = [
    "WyomingError",
    "WyomingEvent",
    "WyomingSttClient",
    "WyomingTtsClient",
    "WyomingWakeClient",
    "WyomingConnection",
    "decode_header",
    "encode_event",
    "async_read_event",
    "async_write_event",
    "wyoming_info",
]


class WyomingError(Exception):
    """Any protocol/transport level failure talking to a Wyoming service."""


class WyomingTimeoutError(WyomingError, TimeoutError):
    """The service did not answer in time."""


class WyomingConnectionError(WyomingError):
    """Could not reach the service at all."""


@dataclass(slots=True)
class WyomingEvent:
    """One Wyoming message."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


# --- framing ----------------------------------------------------------------
def encode_event(event: WyomingEvent) -> bytes:
    """Serialise one event to wire bytes (header line + data + payload).

    Small `data` blocks are written twice — inline in the header *and* in the
    length-prefixed block — so either flavour of reader understands us. Large
    ones are written only length-prefixed: the header must stay comfortably
    inside the peer's `readline()` limit or the message is unreadable.
    """
    data_bytes = b""
    header: dict[str, Any] = {"type": event.type, "version": PROTOCOL_VERSION}
    if event.data:
        data_bytes = json.dumps(event.data, ensure_ascii=False).encode("utf-8")
        header["data_length"] = len(data_bytes)
        if len(data_bytes) <= MAX_INLINE_DATA:
            header["data"] = event.data
    payload = event.payload or b""
    if payload:
        header["payload_length"] = len(payload)
    line = json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n"
    return line + data_bytes + payload


def decode_header(line: bytes) -> dict[str, Any]:
    try:
        header = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise WyomingError(f"invalid Wyoming header: {line!r}") from err
    if not isinstance(header, dict) or "type" not in header:
        raise WyomingError(f"invalid Wyoming header: {line!r}")
    return header


async def async_read_event(
    reader: asyncio.StreamReader, timeout: float | None = DEFAULT_TIMEOUT
) -> WyomingEvent | None:
    """Read one event. Returns None at clean end-of-stream."""

    async def _read() -> WyomingEvent | None:
        for _ in range(MAX_BLANK_LINES + 1):
            line = await reader.readline()
            if not line:
                return None
            if line.strip():
                break
        else:
            raise WyomingError("peer sent nothing but blank lines")
        header = decode_header(line)
        data: dict[str, Any] = {}
        inline = header.get("data")
        if isinstance(inline, dict):
            data.update(inline)
        data_length = header.get("data_length") or 0
        if data_length:
            raw = await reader.readexactly(int(data_length))
            try:
                extra = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                raise WyomingError("invalid Wyoming data block") from err
            if isinstance(extra, dict):
                data.update(extra)
        payload_length = header.get("payload_length") or 0
        payload = await reader.readexactly(int(payload_length)) if payload_length else None
        return WyomingEvent(str(header["type"]), data, payload)

    try:
        if timeout is None:
            return await _read()
        return await asyncio.wait_for(_read(), timeout)
    except asyncio.IncompleteReadError:
        return None
    except (TimeoutError, asyncio.TimeoutError) as err:
        raise WyomingTimeoutError(f"timed out after {timeout}s waiting for a message") from err
    except (ConnectionError, OSError) as err:
        raise WyomingConnectionError(str(err)) from err
    except WyomingError:
        raise
    except ValueError as err:
        # StreamReader.readline() raises LimitOverrunError/ValueError when a
        # header line is longer than the reader's limit. That is a protocol
        # failure, not a programming error — do not leak it to callers.
        raise WyomingError(f"oversized Wyoming header line: {err}") from err


async def async_write_event(writer: asyncio.StreamWriter, event: WyomingEvent) -> None:
    try:
        writer.write(encode_event(event))
        await writer.drain()
    except (ConnectionError, OSError) as err:
        raise WyomingConnectionError(str(err)) from err


# --- connection -------------------------------------------------------------
class WyomingConnection:
    """An open TCP connection to a Wyoming service (async context manager)."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.connect_timeout = (
            connect_timeout if connect_timeout is not None else min(timeout, DEFAULT_CONNECT_TIMEOUT)
        )
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> "WyomingConnection":
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, limit=READ_LIMIT),
                self.connect_timeout,
            )
        except (TimeoutError, asyncio.TimeoutError) as err:
            raise WyomingTimeoutError(
                f"timed out connecting to {self.host}:{self.port}"
            ) from err
        except OSError as err:
            raise WyomingConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        return self

    async def close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        try:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, asyncio.CancelledError):
                await writer.wait_closed()
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("Error closing Wyoming connection", exc_info=True)

    async def __aenter__(self) -> "WyomingConnection":
        return await self.connect()

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def write(self, event: WyomingEvent) -> None:
        if self._writer is None:
            raise WyomingConnectionError("connection is not open")
        await async_write_event(self._writer, event)

    async def read(self, timeout: float | None = -1.0) -> WyomingEvent | None:
        if self._reader is None:
            raise WyomingConnectionError("connection is not open")
        effective = self.timeout if timeout == -1.0 else timeout
        event = await async_read_event(self._reader, effective)
        if event is not None and event.type == TYPE_ERROR:
            raise WyomingError(
                str(event.data.get("text") or event.data.get("message") or "wyoming error")
            )
        return event

    # convenience constructors for the audio sub-protocol
    async def write_audio_start(
        self, rate: int, width: int, channels: int, timestamp: int = 0
    ) -> None:
        await self.write(
            WyomingEvent(
                TYPE_AUDIO_START,
                {
                    "rate": int(rate),
                    "width": int(width),
                    "channels": int(channels),
                    "timestamp": int(timestamp),
                },
            )
        )

    async def write_audio_chunk(
        self, chunk: bytes, rate: int, width: int, channels: int, timestamp: int = 0
    ) -> None:
        await self.write(
            WyomingEvent(
                TYPE_AUDIO_CHUNK,
                {
                    "rate": int(rate),
                    "width": int(width),
                    "channels": int(channels),
                    "timestamp": int(timestamp),
                },
                payload=chunk,
            )
        )

    async def write_audio_stop(self, timestamp: int = 0) -> None:
        await self.write(WyomingEvent(TYPE_AUDIO_STOP, {"timestamp": int(timestamp)}))


AudioSource = Iterable[bytes] | AsyncIterator[bytes] | Any


async def _aiter_audio(source: AudioSource) -> AsyncIterator[bytes]:
    """Normalise sync iterables, async iterables and awaitables to an async iterator."""
    if hasattr(source, "__aiter__"):
        async for chunk in source:  # type: ignore[union-attr]
            if chunk:
                yield bytes(chunk)
        return
    if asyncio.iscoroutine(source):
        source = await source
    if isinstance(source, (bytes, bytearray, memoryview)):
        if source:
            yield bytes(source)
        return
    for chunk in source:  # type: ignore[union-attr]
        if chunk:
            yield bytes(chunk)


class _WyomingClient:
    """Shared host/port/timeout plumbing."""

    def __init__(self, host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.host}:{self.port}>"

    def connection(self) -> WyomingConnection:
        return WyomingConnection(self.host, self.port, self.timeout)

    async def async_info(self) -> dict[str, Any]:
        return await wyoming_info(self.host, self.port, self.timeout)


async def wyoming_info(
    host: str, port: int, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """Send `describe` and return the service's `info` payload."""
    async with WyomingConnection(host, port, timeout) as conn:
        await conn.write(WyomingEvent(TYPE_DESCRIBE))
        while True:
            event = await conn.read()
            if event is None:
                raise WyomingError(f"{host}:{port} closed the connection before sending info")
            if event.type == TYPE_INFO:
                return event.data


class WyomingSttClient(_WyomingClient):
    """Speech-to-text (whisper / sherpa / faster-whisper containers)."""

    def __init__(
        self,
        host: str,
        port: int = 10300,
        timeout: float = 60.0,
        language: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(host, port, timeout)
        self.language = language
        self.model = model

    async def transcribe(
        self,
        audio_iter: AudioSource,
        rate: int = DEFAULT_RATE,
        width: int = DEFAULT_WIDTH,
        channels: int = DEFAULT_CHANNELS,
        language: str | None = None,
    ) -> str:
        """Stream PCM to the STT service and return the recognised text."""
        request: dict[str, Any] = {}
        if self.model:
            request["name"] = self.model
        if language or self.language:
            request["language"] = language or self.language

        async with self.connection() as conn:
            await conn.write(WyomingEvent(TYPE_TRANSCRIBE, request))
            await conn.write_audio_start(rate, width, channels)
            samples = 0
            async for chunk in _aiter_audio(audio_iter):
                timestamp = int(samples * 1000 / max(rate * width * channels, 1))
                await conn.write_audio_chunk(chunk, rate, width, channels, timestamp)
                samples += len(chunk)
            await conn.write_audio_stop(
                int(samples * 1000 / max(rate * width * channels, 1))
            )

            while True:
                event = await conn.read()
                if event is None:
                    raise WyomingError("STT service closed the connection before transcribing")
                if event.type == TYPE_TRANSCRIPT:
                    return str(event.data.get("text") or "").strip()


class WyomingTtsClient(_WyomingClient):
    """Text-to-speech (piper container)."""

    def __init__(
        self,
        host: str,
        port: int = 10200,
        timeout: float = 60.0,
        voice: str | None = None,
        speaker: str | None = None,
    ) -> None:
        super().__init__(host, port, timeout)
        self.voice = voice
        self.speaker = speaker

    async def synthesize(
        self, text: str, voice: str | None = None, speaker: str | None = None
    ) -> tuple[bytes, int, int, int]:
        """Return (raw PCM, rate, width, channels) for `text`."""
        request: dict[str, Any] = {"text": text}
        name = voice or self.voice
        chosen_speaker = speaker or self.speaker
        if name or chosen_speaker:
            voice_block: dict[str, Any] = {}
            if name:
                voice_block["name"] = name
            if chosen_speaker:
                voice_block["speaker"] = chosen_speaker
            request["voice"] = voice_block

        chunks: list[bytes] = []
        rate = width = channels = 0

        async with self.connection() as conn:
            await conn.write(WyomingEvent(TYPE_SYNTHESIZE, request))
            while True:
                event = await conn.read()
                if event is None:
                    if chunks:
                        break
                    raise WyomingError("TTS service closed the connection before speaking")
                if event.type == TYPE_AUDIO_START:
                    rate = int(event.data.get("rate") or rate)
                    width = int(event.data.get("width") or width)
                    channels = int(event.data.get("channels") or channels)
                elif event.type == TYPE_AUDIO_CHUNK:
                    rate = int(event.data.get("rate") or rate)
                    width = int(event.data.get("width") or width)
                    channels = int(event.data.get("channels") or channels)
                    if event.payload:
                        chunks.append(event.payload)
                elif event.type == TYPE_AUDIO_STOP:
                    break

        if not chunks:
            # Say what was asked for. "returned no audio" names the symptom and
            # points at Piper, and the two causes that actually produce it are
            # both on this side of the wire: an empty or whitespace-only string
            # (nothing to synthesise), and a voice the service does not have
            # loaded. Neither is visible from the old message, so the first
            # thing anyone did with it was go and read Piper's logs, where
            # everything looks healthy because it is.
            asked = (text or "").strip()
            detail = (
                f"nothing to say (the text was {len(text or '')} characters of whitespace)"
                if not asked
                else f"voice={name or self.voice or 'default'!r}, {len(asked)} characters"
            )
            raise WyomingError(f"TTS service returned no audio: {detail}")
        return (
            b"".join(chunks),
            rate or FALLBACK_TTS_RATE,
            width or DEFAULT_WIDTH,
            channels or DEFAULT_CHANNELS,
        )


class WyomingWakeClient(_WyomingClient):
    """Wake word detection (openWakeWord container)."""

    def __init__(
        self,
        host: str,
        port: int = 10400,
        timeout: float = 30.0,
        model: str | None = "hey_jarvis",
    ) -> None:
        super().__init__(host, port, timeout)
        self.model = model

    async def detect(
        self,
        audio_iter: AudioSource,
        rate: int = DEFAULT_RATE,
        width: int = DEFAULT_WIDTH,
        channels: int = DEFAULT_CHANNELS,
        timeout: float | None = -1.0,
    ) -> str | None:
        """Stream audio until the wake word fires. Returns its name, or None.

        ``timeout`` bounds the *whole* call, streaming included — a satellite
        that keeps pushing audio into a service that never answers must not
        pin this coroutine forever. Pass ``None`` to listen indefinitely.
        """
        request: dict[str, Any] = {}
        if self.model:
            request["names"] = [self.model]

        deadline = self.timeout if timeout == -1.0 else timeout
        if deadline is not None and deadline <= 0:
            deadline = None

        async with self.connection() as conn:
            await conn.write(WyomingEvent(TYPE_DETECT, request))
            await conn.write_audio_start(rate, width, channels)

            waiter = asyncio.ensure_future(self._wait_for_detection(conn))
            try:
                if deadline is None:
                    return await self._stream_until_detected(
                        conn, waiter, audio_iter, rate, width, channels
                    )
                return await asyncio.wait_for(
                    self._stream_until_detected(
                        conn, waiter, audio_iter, rate, width, channels
                    ),
                    deadline,
                )
            except (TimeoutError, asyncio.TimeoutError) as err:
                raise WyomingTimeoutError("timed out waiting for wake word") from err
            finally:
                if not waiter.done():
                    waiter.cancel()
                # gather(return_exceptions=True) collects the reader's outcome
                # so asyncio does not log "exception was never retrieved"; an
                # *outer* cancellation still propagates from here, as it must.
                await asyncio.gather(waiter, return_exceptions=True)

    async def _stream_until_detected(
        self,
        conn: WyomingConnection,
        waiter: "asyncio.Future[str | None]",
        audio_iter: AudioSource,
        rate: int,
        width: int,
        channels: int,
    ) -> str | None:
        samples = 0
        async for chunk in _aiter_audio(audio_iter):
            if waiter.done():
                break
            timestamp = int(samples * 1000 / max(rate * width * channels, 1))
            await conn.write_audio_chunk(chunk, rate, width, channels, timestamp)
            samples += len(chunk)
        if not waiter.done():
            with contextlib.suppress(WyomingError):
                await conn.write_audio_stop(
                    int(samples * 1000 / max(rate * width * channels, 1))
                )
        return await waiter

    @staticmethod
    async def _wait_for_detection(conn: WyomingConnection) -> str | None:
        while True:
            event = await conn.read(timeout=None)
            if event is None:
                return None
            if event.type == TYPE_DETECTION:
                return str(event.data.get("name") or "")
            if event.type == TYPE_NOT_DETECTED:
                return None


def now_ms() -> int:
    return int(time.monotonic() * 1000)
