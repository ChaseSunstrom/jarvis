"""Speech from an OpenAI-compatible `/v1/audio/speech`, for the voices Piper has not got.

Piper is the default and stays it: 33 MB of model, 0.40x real time on this
host, and it is already in the stack. This exists because the voice is the one
part of an assistant nobody can measure for you — `docs/tts-review/` holds five
sentences in both, and the operator's ear is the tie-break the numbers cannot
be.

    voice:
      tts:
        engine: openai
        url: http://127.0.0.1:8880/v1
        voice: bm_george

Measured against Piper on the same five replies (2026-08-25, this host):

    piper    median synth 1.76 s   RTF 0.40x   WER 0.000
    kokoro   median synth 1.48 s   RTF 0.47x   WER 0.000

Both are comfortably faster than real time and both come back through Whisper
word-perfect, so latency and intelligibility do not decide it. What does decide
it is 3.2 GB of image and 1 GB of resident memory against 33 MB — which is why
this is opt-in and the container is behind a compose profile.

The interface is the one `WyomingTtsClient` presents, because `pipeline.py`
should not know which of them it has: `synthesize(text) -> (pcm, rate, width,
channels)`.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8880/v1"
DEFAULT_VOICE = "bm_george"
DEFAULT_MODEL = "kokoro"


class OpenAiTtsError(RuntimeError):
    """The speech service could not be reached, or did not send audio."""


class OpenAiTtsClient:
    """`POST /audio/speech`, returned as PCM the pipeline can play."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        voice: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        speed: float = 1.0,
        client: Any = None,
    ) -> None:
        self.url = str(url or DEFAULT_URL).rstrip("/")
        self.voice = str(voice or DEFAULT_VOICE)
        self.model = str(model or DEFAULT_MODEL)
        self.timeout = float(timeout)
        self.speed = float(speed or 1.0)
        self._client = client

    async def _post(self, body: dict[str, Any]) -> bytes:
        endpoint = f"{self.url}/audio/speech"
        try:
            if self._client is not None:
                answer = await self._client.post(endpoint, json=body, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    answer = await http.post(endpoint, json=body)
            answer.raise_for_status()
            return answer.content
        except Exception as err:  # noqa: BLE001 - one error type out of this module
            raise OpenAiTtsError(f"{endpoint}: {type(err).__name__}: {err}") from err

    async def synthesize(
        self, text: str, voice: str | None = None, speaker: str | None = None
    ) -> tuple[bytes, int, int, int]:
        """(raw PCM, rate, width, channels) — the shape `WyomingTtsClient` returns."""
        data = await self._post(
            {
                "model": self.model,
                "input": str(text or ""),
                "voice": str(voice or self.voice),
                "response_format": "wav",
                "speed": self.speed,
            }
        )
        if not data:
            raise OpenAiTtsError("the speech service returned no audio")
        try:
            with wave.open(io.BytesIO(data)) as source:
                rate = source.getframerate()
                width = source.getsampwidth()
                channels = source.getnchannels()
                # NOT `readframes(getnframes())`. A streamed WAV carries a
                # placeholder frame count — Kokoro's says 89478 seconds — and
                # reading by it returns a fraction of the audio or none of it.
                # The bytes after the header are the audio.
                frames = source.readframes(source.getnframes())
        except wave.Error as err:
            raise OpenAiTtsError(f"the speech service sent something that is not a WAV: {err}")
        expected = len(data) - 44
        if len(frames) < expected * 0.9:
            frames = data[44:]
        return frames, rate, width, channels

    async def is_available(self) -> bool:
        try:
            await self.synthesize("ready")
        except Exception:  # noqa: BLE001 - unavailable is the answer, not a raise
            return False
        return True

    def describe(self) -> dict[str, Any]:
        return {"engine": "openai", "url": self.url, "voice": self.voice, "model": self.model}
