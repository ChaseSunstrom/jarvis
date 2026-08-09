#!/usr/bin/env python3
"""Fake whisper / piper / openWakeWord, speaking the real Wyoming framing.

Wyoming is a newline-delimited protocol with two optional trailers:

    {"type": "audio-chunk", "data": {...}, "data_length": N, "payload_length": M}\\n
    <N bytes of JSON>          # merged over the inline "data"
    <M bytes of binary>        # raw PCM

`jarvis/voice/wyoming.py` is the client under test and this is the other end of
its socket. Three roles, each on its own port:

    stt    consumes transcribe / audio-start / audio-chunk* / audio-stop,
           answers `transcript`
    tts    consumes synthesize, answers audio-start / audio-chunk* / audio-stop
           carrying REAL 16-bit PCM (a sine, so the WAV the pipeline builds is
           a playable file rather than a buffer of zeros)
    wake   consumes detect + audio, answers `detection` (or `not-detected`)

All three answer `describe` with an `info` block, which is what
`voice.async_info()` and the Wyoming health probes ask for.

Scripted through a JSON file, re-read whenever it changes so a test can change
the next transcript without restarting anything::

    {
      "stt":  {"transcripts": ["turn on the lab lights", "thank you"]},
      "tts":  {"rate": 22050, "ms_per_char": 50},
      "wake": {"name": "hey_jarvis", "detect_after": 2}
    }

The STT role also has a length mode, which is how a test tells "the audio
arrived" apart from "nothing arrived at all":

    {"stt": {"mode": "length", "template": "heard {ms} ms of audio"}}

Run it standalone, or drive it from a test:

    python3 fake_wyoming.py --stt-port 10300 --tts-port 10200 --wake-port 10400

Stdlib only, and deliberately self-contained (no imports from the rest of this
package) so it can be copied anywhere python3 runs.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import signal
import sys
import wave
from array import array
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "1.5.0"

DEFAULT_TRANSCRIPT = "turn on the lab lights"
DEFAULT_WAKE_WORD = "hey_jarvis"

DEFAULT_TTS_RATE = 22050
DEFAULT_TTS_WIDTH = 2
DEFAULT_TTS_CHANNELS = 1
#: Piper is roughly this fast per character; the exact number only matters
#: because it makes "longer text produces longer audio" assertable.
DEFAULT_MS_PER_CHAR = 50
MIN_TTS_MS = 250
MAX_TTS_MS = 5000
TTS_CHUNK_MS = 100

#: asyncio's StreamReader default line limit is 64 KiB; a `synthesize` for a
#: long sentence, or an `info` block, can exceed that when sent inline.
READ_LIMIT = 4 * 1024 * 1024

__all__ = ["FakeWyomingServer", "FakeWyomingStack", "sine_pcm", "main"]


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------
def encode_event(event_type: str, data: dict[str, Any] | None = None,
                 payload: bytes | None = None) -> bytes:
    """One wire frame. Small `data` goes inline *and* length-prefixed."""
    header: dict[str, Any] = {"type": event_type, "version": PROTOCOL_VERSION}
    data_bytes = b""
    if data:
        data_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        header["data_length"] = len(data_bytes)
        if len(data_bytes) <= 4096:
            header["data"] = data
    payload = payload or b""
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n" + data_bytes + payload


async def read_event(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one frame, or None at clean end-of-stream."""
    line = await reader.readline()
    if not line:
        return None
    if not line.strip():
        return {"type": "", "data": {}, "payload": None}
    header = json.loads(line.decode("utf-8"))
    data: dict[str, Any] = {}
    inline = header.get("data")
    if isinstance(inline, dict):
        data.update(inline)
    length = int(header.get("data_length") or 0)
    if length:
        raw = await reader.readexactly(length)
        extra = json.loads(raw.decode("utf-8"))
        if isinstance(extra, dict):
            data.update(extra)
    payload_length = int(header.get("payload_length") or 0)
    payload = await reader.readexactly(payload_length) if payload_length else None
    return {"type": str(header.get("type") or ""), "data": data, "payload": payload}


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------
def sine_pcm(
    seconds: float,
    rate: int = DEFAULT_TTS_RATE,
    frequency: float = 440.0,
    amplitude: float = 0.35,
    channels: int = 1,
) -> bytes:
    """Signed 16-bit little-endian PCM. Deterministic — the same every run."""
    frames = max(int(rate * seconds), 1)
    peak = int(32767 * max(0.0, min(1.0, amplitude)))
    step = 2 * math.pi * frequency / rate
    samples = array("h", bytes(frames * channels * 2))
    for index in range(frames):
        value = int(peak * math.sin(step * index))
        for channel in range(channels):
            samples[index * channels + channel] = value
    if sys.byteorder != "little":  # pragma: no cover - big-endian hosts
        samples.byteswap()
    return samples.tobytes()


def chunked(data: bytes, size: int):
    for offset in range(0, len(data), size):
        chunk = data[offset : offset + size]
        if chunk:
            yield chunk


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------
DEFAULT_SCRIPT: dict[str, Any] = {
    "stt": {
        "mode": "script",
        "transcript": DEFAULT_TRANSCRIPT,
        "transcripts": [],
        "template": "heard {ms} ms of audio",
        "error_on": None,
    },
    "tts": {
        "rate": DEFAULT_TTS_RATE,
        "width": DEFAULT_TTS_WIDTH,
        "channels": DEFAULT_TTS_CHANNELS,
        "ms_per_char": DEFAULT_MS_PER_CHAR,
        "seconds": None,
        "frequency": 440.0,
        "error_on": None,
    },
    "wake": {
        "name": DEFAULT_WAKE_WORD,
        "detect": True,
        "detect_after": 2,
        "error_on": None,
    },
}


def _merge(base: dict[str, Any], overlay: Any) -> dict[str, Any]:
    out = {key: dict(value) for key, value in base.items()}
    if isinstance(overlay, dict):
        for role, values in overlay.items():
            if role in out and isinstance(values, dict):
                out[role].update(values)
    return out


class _ScriptFile:
    """A JSON script re-read whenever the file on disk changes.

    ``version`` counts how many times the script has actually changed. It is
    what lets a role reset its per-run counters: a new script means a new run,
    so a queue of transcripts starts from its first entry again rather than
    from wherever the last test happened to leave the cursor.
    """

    def __init__(self, path: str | None, overrides: dict[str, Any] | None = None) -> None:
        self.path = path
        self.overrides = overrides or {}
        #: (st_mtime_ns, st_size, st_ino) of the copy currently loaded.
        #: mtime alone is not enough: a coarse-granularity filesystem can give
        #: two rewrites the same stamp, and a same-size edit the same size. The
        #: harness replaces the file (a new inode every write), so the inode
        #: settles it either way.
        self._stamp: tuple[int, int, int] | None = None
        self.version = 0
        self.data = _merge(DEFAULT_SCRIPT, self.overrides)
        self.reload()

    def reload(self) -> dict[str, Any]:
        if not self.path:
            return self.data
        try:
            info = os.stat(self.path)
        except OSError:
            return self.data
        stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        if stamp == self._stamp:
            return self.data
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as err:
            # A half-written file: leave the stamp alone so the next call
            # retries rather than pinning the old script forever.
            sys.stderr.write(f"fake-wyoming: ignoring bad script ({err})\n")
            return self.data
        self._stamp = stamp
        # CLI overrides win over the file: they are the more explicit request.
        merged = _merge(_merge(DEFAULT_SCRIPT, loaded), self.overrides)
        if merged != self.data:
            self.version += 1
        self.data = merged
        return self.data

    def role(self, name: str) -> dict[str, Any]:
        return self.reload().get(name, {})

    def update(self, role: str, **values: Any) -> None:
        """In-process tweak (tests): does not touch the file."""
        self.overrides.setdefault(role, {}).update(values)
        merged = _merge(self.data, {role: values})
        if merged != self.data:
            self.version += 1
        self.data = merged


# ---------------------------------------------------------------------------
# one role on one port
# ---------------------------------------------------------------------------
class FakeWyomingServer:
    """One Wyoming role (``stt``, ``tts`` or ``wake``) on one TCP port."""

    def __init__(
        self,
        role: str,
        script: _ScriptFile | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        audio_dir: str | Path | None = None,
        verbose: bool = False,
    ) -> None:
        if role not in ("stt", "tts", "wake"):
            raise ValueError(f"unknown role {role!r}")
        self.role = role
        self.script = script if script is not None else _ScriptFile(None)
        self.host = host
        self._port = int(port)
        self.audio_dir = Path(audio_dir) if audio_dir else None
        self.verbose = verbose

        #: Everything this role was sent, in order — the test's evidence.
        self.events: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self.audio_chunks = 0
        self.transcripts_served: list[str] = []
        self.synthesized: list[str] = []
        self.detections: list[str] = []
        self.connections = 0

        self._server: asyncio.AbstractServer | None = None
        #: How many transcripts this role has served since the script last
        #: changed, and the script version that count belongs to.
        self._served = 0
        self._served_version = self.script.version

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> "FakeWyomingServer":
        self._server = await asyncio.start_server(
            self._handle, self.host, self._port, limit=READ_LIMIT
        )
        self._port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

    @property
    def port(self) -> int:
        return self._port

    def _log(self, message: str) -> None:
        if self.verbose:
            sys.stderr.write(f"fake-wyoming[{self.role}] {message}\n")

    @property
    def event_types(self) -> list[str]:
        return [event["type"] for event in self.events]

    # --- the protocol -----------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        state: dict[str, Any] = {
            "transcribing": False,
            "detecting": False,
            "audio": bytearray(),
            "chunks": 0,
            "rate": 16000,
            "width": 2,
            "channels": 1,
        }
        try:
            while True:
                event = await read_event(reader)
                if event is None:
                    break
                if not event["type"]:
                    continue
                self.events.append(
                    {"type": event["type"], "data": event["data"],
                     "payload_length": len(event["payload"] or b"")}
                )
                self._log(f"<- {event['type']}")
                if await self._dispatch(event, state, writer):
                    break
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - a broken peer, not a test failure
            sys.stderr.write(f"fake-wyoming[{self.role}] connection error: {err!r}\n")
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _write(self, writer: asyncio.StreamWriter, event_type: str,
                     data: dict[str, Any] | None = None, payload: bytes | None = None) -> None:
        writer.write(encode_event(event_type, data, payload))
        await writer.drain()
        self._log(f"-> {event_type}")

    async def _dispatch(self, event: dict[str, Any], state: dict[str, Any],
                        writer: asyncio.StreamWriter) -> bool:
        """Handle one event. Returns True to close the connection."""
        options = self.script.role(self.role)
        kind = event["type"]

        if options.get("error_on") == kind:
            await self._write(writer, "error", {"text": "scripted failure", "code": "test"})
            return True

        if kind == "describe":
            await self._write(writer, "info", self._info())
            return False

        if kind == "audio-start":
            state["audio"] = bytearray()
            state["chunks"] = 0
            for key in ("rate", "width", "channels"):
                if event["data"].get(key):
                    state[key] = int(event["data"][key])
            return False

        if kind == "audio-chunk":
            payload = event["payload"] or b""
            state["audio"].extend(payload)
            state["chunks"] += 1
            self.audio_bytes += len(payload)
            self.audio_chunks += 1
            if self.role == "wake" and state["detecting"]:
                after = int(options.get("detect_after") or 2)
                if options.get("detect", True) and state["chunks"] >= after:
                    name = str(options.get("name") or DEFAULT_WAKE_WORD)
                    await self._write(
                        writer, "detection", {"name": name, "timestamp": state["chunks"] * 20}
                    )
                    self.detections.append(name)
                    state["detecting"] = False
            return False

        if kind == "audio-stop":
            if self.role == "stt" and state["transcribing"]:
                text = self._transcript_for(state)
                self._save_audio(state)
                await self._write(writer, "transcript", {"text": text})
                self.transcripts_served.append(text)
                state["transcribing"] = False
            elif self.role == "wake" and state["detecting"]:
                await self._write(writer, "not-detected", {})
                state["detecting"] = False
            return False

        if kind == "transcribe" and self.role == "stt":
            state["transcribing"] = True
            self.requests.append(dict(event["data"]))
            return False

        if kind == "detect" and self.role == "wake":
            state["detecting"] = True
            self.requests.append(dict(event["data"]))
            return False

        if kind == "synthesize" and self.role == "tts":
            await self._synthesize(event["data"], writer, options)
            return False

        return False

    # --- role behaviour ---------------------------------------------------
    def _info(self) -> dict[str, Any]:
        common = {
            "name": f"fake-{self.role}",
            "attribution": {"name": "jarvis test harness", "url": ""},
            "installed": True,
            "description": f"fake Wyoming {self.role}",
            "version": "1.0.0",
        }
        if self.role == "stt":
            return {"asr": [{**common, "models": [{**common, "languages": ["en"]}]}]}
        if self.role == "tts":
            options = self.script.role("tts")
            voice = {**common, "languages": ["en"], "speakers": []}
            return {
                "tts": [
                    {
                        **common,
                        "voices": [voice],
                        "sample_rate": int(options.get("rate") or DEFAULT_TTS_RATE),
                    }
                ]
            }
        return {"wake": [{**common, "models": [{**common, "languages": ["en"]}]}]}

    def _transcript_for(self, state: dict[str, Any]) -> str:
        options = self.script.role("stt")
        # A new script is a new run. Without this the cursor into `transcripts`
        # keeps climbing across tests, so a queue set after any earlier
        # utterance would serve its LAST entry from the very first run — the
        # opposite of what "one per run" says, and silent about it.
        if self.script.version != self._served_version:
            self._served_version = self.script.version
            self._served = 0
        if str(options.get("mode") or "script") == "length":
            audio = state["audio"]
            rate = max(int(state["rate"]) * int(state["width"]) * int(state["channels"]), 1)
            template = str(options.get("template") or "heard {ms} ms of audio")
            return template.format(
                bytes=len(audio),
                ms=int(len(audio) * 1000 / rate),
                chunks=state["chunks"],
                samples=len(audio) // max(int(state["width"]), 1),
            )
        transcripts = options.get("transcripts")
        if isinstance(transcripts, list) and transcripts:
            index = min(self._served, len(transcripts) - 1)
            self._served += 1
            return str(transcripts[index])
        self._served += 1
        return str(options.get("transcript") if options.get("transcript") is not None
                   else DEFAULT_TRANSCRIPT)

    async def _synthesize(self, data: dict[str, Any], writer: asyncio.StreamWriter,
                          options: dict[str, Any]) -> None:
        text = str(data.get("text") or "")
        self.synthesized.append(text)
        self.requests.append(dict(data))

        rate = int(options.get("rate") or DEFAULT_TTS_RATE)
        width = int(options.get("width") or DEFAULT_TTS_WIDTH)
        channels = int(options.get("channels") or DEFAULT_TTS_CHANNELS)
        if options.get("seconds"):
            seconds = float(options["seconds"])
        else:
            ms = len(text) * float(options.get("ms_per_char") or DEFAULT_MS_PER_CHAR)
            seconds = max(MIN_TTS_MS, min(MAX_TTS_MS, ms)) / 1000.0
        pcm = sine_pcm(seconds, rate, float(options.get("frequency") or 440.0),
                       channels=channels)

        await self._write(writer, "audio-start",
                          {"rate": rate, "width": width, "channels": channels, "timestamp": 0})
        chunk_size = max(int(rate * channels * width * TTS_CHUNK_MS / 1000), width * channels)
        for chunk in chunked(pcm, chunk_size):
            await self._write(writer, "audio-chunk",
                              {"rate": rate, "width": width, "channels": channels}, chunk)
        await self._write(writer, "audio-stop", {"timestamp": int(seconds * 1000)})

    def _save_audio(self, state: dict[str, Any]) -> None:
        """Dump what STT received, so a CI failure has something to listen to."""
        if self.audio_dir is None or not state["audio"]:
            return
        try:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            path = self.audio_dir / f"stt-{len(self.transcripts_served):03d}.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(int(state["channels"]))
                handle.setsampwidth(int(state["width"]))
                handle.setframerate(int(state["rate"]))
                handle.writeframes(bytes(state["audio"]))
        except Exception:  # pragma: no cover - diagnostics must never fail a run
            sys.stderr.write("fake-wyoming[stt]: could not save received audio\n")


# ---------------------------------------------------------------------------
# all three at once
# ---------------------------------------------------------------------------
class FakeWyomingStack:
    """STT + TTS + wake, sharing one script.

    In-process::

        stack = FakeWyomingStack(script={"stt": {"transcript": "hello"}})
        await stack.start()
        ...  stack.stt.port / stack.tts.port / stack.wake.port
        await stack.stop()
    """

    def __init__(
        self,
        script: Any = None,
        script_file: str | None = None,
        host: str = "127.0.0.1",
        stt_port: int = 0,
        tts_port: int = 0,
        wake_port: int = 0,
        audio_dir: str | Path | None = None,
        verbose: bool = False,
    ) -> None:
        self.script = _ScriptFile(script_file, script if isinstance(script, dict) else None)
        self.stt = FakeWyomingServer("stt", self.script, host, stt_port, audio_dir, verbose)
        self.tts = FakeWyomingServer("tts", self.script, host, tts_port, None, verbose)
        self.wake = FakeWyomingServer("wake", self.script, host, wake_port, None, verbose)

    @property
    def servers(self) -> list[FakeWyomingServer]:
        return [self.stt, self.tts, self.wake]

    async def start(self) -> "FakeWyomingStack":
        for server in self.servers:
            await server.start()
        return self

    async def stop(self) -> None:
        for server in self.servers:
            await server.stop()

    async def __aenter__(self) -> "FakeWyomingStack":
        return await self.start()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.stop()

    def ports(self) -> dict[str, int]:
        return {"stt": self.stt.port, "tts": self.tts.port, "wake": self.wake.port}

    def set_transcript(self, text: str) -> None:
        self.script.update("stt", mode="script", transcript=text, transcripts=[])

    def set_transcripts(self, texts: list[str]) -> None:
        self.script.update("stt", mode="script", transcripts=list(texts))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def _serve(args: argparse.Namespace) -> int:
    overrides: dict[str, Any] = {}
    if args.transcript is not None:
        overrides["stt"] = {"mode": "script", "transcript": args.transcript}
    if args.stt_mode:
        overrides.setdefault("stt", {})["mode"] = args.stt_mode
    if args.wake_word:
        overrides["wake"] = {"name": args.wake_word}
    if args.no_detect:
        overrides.setdefault("wake", {})["detect"] = False

    stack = FakeWyomingStack(
        script=overrides or None,
        script_file=args.script,
        host=args.host,
        stt_port=args.stt_port,
        tts_port=args.tts_port,
        wake_port=args.wake_port,
        audio_dir=args.audio_dir,
        verbose=args.verbose,
    )
    await stack.start()

    info = {"kind": "fake-wyoming", "host": args.host, **{f"{k}_port": v
                                                          for k, v in stack.ports().items()}}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
    print(json.dumps(info), flush=True)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stopping.set)
    try:
        await stopping.wait()
    finally:
        await stack.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fake Wyoming STT/TTS/wake servers.")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    parser.add_argument("--stt-port", type=int, default=0)
    parser.add_argument("--tts-port", type=int, default=0)
    parser.add_argument("--wake-port", type=int, default=0)
    parser.add_argument("--script", default=None, help="JSON script (re-read when it changes)")
    parser.add_argument("--transcript", default=None, help="what STT always returns")
    parser.add_argument("--stt-mode", default=None, choices=["script", "length"],
                        help="'length' derives the transcript from how much audio arrived")
    parser.add_argument("--wake-word", default=None)
    parser.add_argument("--no-detect", action="store_true",
                        help="never fire the wake word (answer not-detected)")
    parser.add_argument("--audio-dir", default=None, help="save received STT audio as WAV here")
    parser.add_argument("--json-out", default=None, help="write the chosen ports here")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
