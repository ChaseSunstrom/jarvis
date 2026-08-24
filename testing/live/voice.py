"""Synthesise the user; hear Jarvis.

Two halves of one loop, both real:

* **Mouth** — local Piper (`piper-tts`), speaking `en_US-amy-low`. Runs in this
  process rather than through a second Wyoming container, because the point is
  to produce a WAV, and a container we cannot restart on this host cannot be
  given a second voice.
* **Ears** — the *real* Wyoming Whisper Jarvis itself uses. Deliberately the
  same service: if Jarvis's STT is having a bad day, the rig's transcript of
  its reply should have a bad day too, and a scenario that passed because the
  rig used a better recogniser than the system under test would be a lie.

Cached: synthesising the same sentence twice a run is pure cost, and the cache
key is the text plus every knob that changes the audio.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import LiveError
from .fetch_voice import present as voice_present
from .fetch_voice import voice_path

_LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "jarvis-core") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "jarvis-core"))

#: Where synthesised utterances are kept between runs.
CACHE_DIR = Path(os.environ.get("LIVE_AUDIO_CACHE") or REPO_ROOT / ".verify" / "live" / "audio")

#: The voice services the live rig talks to. These are the ones this host
#: actually runs; a different box overrides them in the environment.
STT_HOST = os.environ.get("LIVE_STT_HOST", "127.0.0.1")
STT_PORT = int(os.environ.get("LIVE_STT_PORT", "10300"))
TTS_HOST = os.environ.get("LIVE_TTS_HOST", "127.0.0.1")
TTS_PORT = int(os.environ.get("LIVE_TTS_PORT", "10200"))
WAKE_HOST = os.environ.get("LIVE_WAKE_HOST", "127.0.0.1")
WAKE_PORT = int(os.environ.get("LIVE_WAKE_PORT", "10400"))

#: Jarvis's own voice, for the record. The rig must never synthesise with it.
JARVIS_VOICE = "en_GB-alan-medium"


@dataclass
class Utterance:
    """One synthesised thing the user says."""

    text: str
    pcm: bytes
    rate: int
    width: int = 2
    channels: int = 1

    @property
    def seconds(self) -> float:
        return len(self.pcm) / max(self.rate * self.width * self.channels, 1)

    def wav_bytes(self) -> bytes:
        import io

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(self.channels)
            out.setsampwidth(self.width)
            out.setframerate(self.rate)
            out.writeframes(self.pcm)
        return buffer.getvalue()

    def write_wav(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.wav_bytes())
        return path

    def chunks(self, size: int = 4096):
        for start in range(0, len(self.pcm), size):
            yield self.pcm[start : start + size]


class Mouth:
    """The synthetic user's voice."""

    def __init__(self, voice: str | Path | None = None, threads: int = 1) -> None:
        if voice is None and not voice_present():
            raise LiveError(
                "the user's voice is not here — run: python3 testing/live/fetch_voice.py"
            )
        self.model_path = Path(voice) if voice else voice_path()
        if self.model_path.name.startswith(JARVIS_VOICE):
            raise LiveError(
                f"the user must not speak in Jarvis's own voice ({JARVIS_VOICE}): "
                "a transcript would no longer say who said it"
            )
        self._threads = threads
        self._voice: Any = None

    def _load(self) -> Any:
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as err:  # pragma: no cover - install is a prerequisite
                raise LiveError(f"piper-tts is not installed in this venv: {err}") from err
            # Two red `pthread_setaffinity_np failed` lines arrive on stderr
            # here, once per process: onnxruntime pins thread affinity and this
            # LXC refuses it (EINVAL). They are noise, not a failure —
            # synthesis works — and they cannot be turned off from this side:
            # the fix onnxruntime suggests is an explicit `intra_op_num_threads`
            # on the session, and `PiperVoice.load` does not expose session
            # options. OMP_NUM_THREADS does not reach it either; that was
            # tried. Silencing them would mean raising the library's log level
            # past ERROR, which would also hide a real one.
            os.environ.setdefault("OMP_NUM_THREADS", str(self._threads))
            self._voice = PiperVoice.load(str(self.model_path), use_cuda=False)
        return self._voice

    def say(self, text: str) -> Utterance:
        """Synthesise `text`, from cache when possible."""
        text = " ".join(str(text).split())
        if not text:
            raise LiveError("nothing to say")
        key = hashlib.sha256(f"{self.model_path.name}|{text}".encode()).hexdigest()[:16]
        cached = CACHE_DIR / f"{key}.wav"
        if cached.is_file():
            with wave.open(str(cached)) as source:
                return Utterance(
                    text=text,
                    pcm=source.readframes(source.getnframes()),
                    rate=source.getframerate(),
                    width=source.getsampwidth(),
                    channels=source.getnchannels(),
                )

        voice = self._load()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temp = cached.with_suffix(".wav.tmp")
        with wave.open(str(temp), "wb") as out:
            voice.synthesize_wav(text, out)
        os.replace(temp, cached)
        return self.say(text)


class Ears:
    """Whatever Jarvis said, as text — through the real Whisper."""

    def __init__(self, host: str = STT_HOST, port: int = STT_PORT) -> None:
        self.host = host
        self.port = port

    async def hear(self, pcm: bytes, rate: int = 22050, width: int = 2, channels: int = 1) -> str:
        from jarvis.voice.wyoming import WyomingSttClient

        client = WyomingSttClient(self.host, self.port, timeout=120.0)

        async def stream():
            for start in range(0, len(pcm), 4096):
                yield pcm[start : start + 4096]

        try:
            return await client.transcribe(stream(), rate=rate, width=width, channels=channels)
        except Exception as err:  # noqa: BLE001 - the rig must say which half broke
            raise LiveError(f"the STT service at {self.host}:{self.port} failed: {err}") from err

    async def hear_wav(self, data: bytes) -> str:
        import io

        with wave.open(io.BytesIO(data)) as source:
            return await self.hear(
                source.readframes(source.getnframes()),
                rate=source.getframerate(),
                width=source.getsampwidth(),
                channels=source.getnchannels(),
            )


async def services_are_up(timeout: float = 5.0) -> dict[str, bool]:
    """Which of the three voice services answer. Used to fail with a reason."""
    out: dict[str, bool] = {}
    for name, host, port in (
        ("stt", STT_HOST, STT_PORT),
        ("tts", TTS_HOST, TTS_PORT),
        ("wake", WAKE_HOST, WAKE_PORT),
    ):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 - closing a probe is not news
                pass
            out[name] = True
        except Exception:  # noqa: BLE001 - down is the answer, not an error
            out[name] = False
    return out
