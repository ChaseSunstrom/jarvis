"""The voice pipeline runner: audio in, spoken answer out.

A run walks four stages — ``wake`` -> ``stt`` -> ``intent`` -> ``tts`` — and
reports progress through ``event_cb(event_type, data)``. The event names and
payload shapes below are a contract with the clients that already exist
(ESP32 satellites, the web chat UI), so they are not up for creative
reinterpretation:

    run-start        {"pipeline": id, "language": "en",
                      "runner_data": {"stt_binary_handler_id": int, "timeout": 300}}
    wake_word-start  {"engine": ..., "metadata": {...}}      (only when start_stage="wake")
    wake_word-end    {"wake_word_output": {"wake_word_id": ..., "timestamp": ms}}
    stt-start        {"engine": "wyoming", "metadata": {...}}
    stt-vad-start    {"timestamp": ms}
    stt-vad-end      {"timestamp": ms}
    stt-end          {"stt_output": {"text": "..."}}
    intent-start     {"engine": "ollama", "language": "en"}
    intent-progress  {"chat_log_delta": {"role": "assistant", "content": "<delta>"}}
    intent-end       {"intent_output": {"response": {...}, "conversation_id": "..."}}
    tts-start        {"engine": "wyoming", "language": "en", "voice": ..., "tts_input": ...}
    tts-end          {"tts_output": {"url": "/api/tts_proxy/<token>.wav", "mime_type": "audio/wav"}}
    run-end          {}
    error            {"code": "...", "message": "..."}

Everything the runner talks to is injected, so a full run can be exercised
with fakes and no network:

    stt      .transcribe(audio_iter, rate=...) -> str
    tts      .synthesize(text, voice=...)      -> (pcm, rate, width, channels)
    wake     .detect(audio_iter)               -> str | None
    converse(text, conversation_id)            -> async iterator of deltas (or a string)
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import math
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .audio import DEFAULT_CHANNELS, DEFAULT_RATE, DEFAULT_WIDTH, rms, wav_bytes

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis
    from .pipelines import Pipeline

_LOGGER = logging.getLogger(__name__)

# --- event names ------------------------------------------------------------
EVENT_RUN_START = "run-start"
EVENT_RUN_END = "run-end"
EVENT_WAKE_START = "wake_word-start"
EVENT_WAKE_END = "wake_word-end"
EVENT_STT_START = "stt-start"
EVENT_STT_VAD_START = "stt-vad-start"
EVENT_STT_VAD_END = "stt-vad-end"
EVENT_STT_END = "stt-end"
EVENT_INTENT_START = "intent-start"
EVENT_INTENT_PROGRESS = "intent-progress"
EVENT_INTENT_END = "intent-end"
EVENT_TTS_START = "tts-start"
EVENT_TTS_END = "tts-end"
EVENT_ERROR = "error"
#: Emitted between `stt-end` and `intent-start` whenever a speaker gate is
#: active — in `observe` as well as `enforce`, because a mode that produced no
#: events would give you nothing to set a threshold from.
EVENT_SPEAKER_END = "speaker-end"

#: The turn was refused because the voice was not the enrolled owner's.
#: Distinct from every stt code: "I heard you and you are not who this belongs
#: to" is a different thing from "I could not make out what you said", and a
#: client that shows them the same way is lying to whichever one it is.
ERROR_NOT_RECOGNISED = "speaker-not-recognised"

# Bus event mirroring every pipeline event (handy for the API/websocket layer).
EVENT_VOICE_PIPELINE = "voice_pipeline_event"

STAGES = ("wake", "stt", "intent", "tts")
STAGE_ORDER = {name: index for index, name in enumerate(STAGES)}

DATA_TTS_CACHE = "tts_cache"
TTS_URL_TEMPLATE = "/api/tts_proxy/{token}.wav"
TTS_MIME_TYPE = "audio/wav"
MAX_CACHED_TTS = 64

DEFAULT_TIMEOUT = 300.0
# `assist_pipeline/run` lets a websocket client name its own timeout, so this
# is a trust boundary: nothing a client sends may produce a run with no
# deadline at all (a run holds a Wyoming connection and a driver task open).
MAX_TIMEOUT = 3600.0
# RMS in int16 units, so 80 is ~0.0024 of full scale — deliberately the same
# place the clients' 0.002 start edge sits, because the two disagreeing means
# the orb says "speaking" at a different moment from the surface that is
# actually deciding when the turn ends.
#
# Safe to lower: this VAD only EMITS stt-vad-start/end. Every chunk is yielded
# to the recogniser either way, so a threshold that is too low costs an early
# event and never a lost word.
DEFAULT_VAD_THRESHOLD = 80.0
DEFAULT_VAD_SILENCE_MS = 900

#: Ceiling on the audio held in memory for speaker verification: 20 s at 16 kHz
#: mono 16-bit. The verifier stops improving long before this (it samples at
#: most 6.4 s of voiced frames), so this is a memory bound rather than a
#: quality one — a client that streams for an hour must not grow the process by
#: an hour of PCM.
MAX_VERIFY_BYTES = 16000 * 2 * 20

_HANDLER_IDS = itertools.count(1)

__all__ = [
    "PipelineError",
    "PipelineEvent",
    "PipelineRun",
    "STAGES",
    "store_tts_audio",
]


class PipelineError(Exception):
    """A stage failed; carries the machine-readable code sent to clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class PipelineEvent:
    """One emitted event, kept on the run for tests and debugging."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "timestamp": self.timestamp}


def _sane_timeout(timeout: Any) -> float:
    """Clamp a caller-supplied run timeout into something bounded.

    Zero, negative, NaN and nonsense values fall back to the default rather
    than disabling the deadline: every run must end.
    """
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT
    return min(value, MAX_TIMEOUT)


def store_tts_audio(
    jarvis: "Jarvis | None",
    audio: bytes,
    mime_type: str = TTS_MIME_TYPE,
    token: str | None = None,
    cache: dict[str, tuple[bytes, str]] | None = None,
) -> tuple[str, str]:
    """Cache WAV bytes under a token; returns (token, url).

    The API layer serves ``jarvis.data["tts_cache"][token] == (wav, mime)``
    at ``/api/tts_proxy/<token>.wav``.
    """
    if cache is None:
        if jarvis is None:
            raise PipelineError("tts-failed", "no place to store TTS audio")
        cache = jarvis.data.setdefault(DATA_TTS_CACHE, {})
    token = token or secrets.token_hex(16)
    cache[token] = (bytes(audio), mime_type)
    while len(cache) > MAX_CACHED_TTS:
        cache.pop(next(iter(cache)))
    return token, TTS_URL_TEMPLATE.format(token=token)


class PipelineRun:
    """One execution of a voice pipeline."""

    def __init__(
        self,
        jarvis: "Jarvis | None" = None,
        *,
        pipeline: "Pipeline | None" = None,
        stt: Any = None,
        tts: Any = None,
        wake: Any = None,
        speaker: Any = None,
        converse: Callable[..., Any] | None = None,
        start_stage: str = "stt",
        end_stage: str = "tts",
        conversation_id: str | None = None,
        language: str | None = None,
        tts_voice: str | None = None,
        wake_word: str | None = None,
        binary_handler_id: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        run_id: str | None = None,
        sample_rate: int = DEFAULT_RATE,
        sample_width: int = DEFAULT_WIDTH,
        channels: int = DEFAULT_CHANNELS,
        vad_enabled: bool = True,
        vad_threshold: float = DEFAULT_VAD_THRESHOLD,
        vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS,
        tts_cache: dict[str, tuple[bytes, str]] | None = None,
    ) -> None:
        if start_stage not in STAGE_ORDER:
            raise ValueError(f"unknown start_stage: {start_stage!r}")
        if end_stage not in STAGE_ORDER:
            raise ValueError(f"unknown end_stage: {end_stage!r}")
        if STAGE_ORDER[start_stage] > STAGE_ORDER[end_stage]:
            raise ValueError(f"start_stage {start_stage!r} is after end_stage {end_stage!r}")

        self.jarvis = jarvis
        self.pipeline = pipeline
        self.stt = stt
        self.tts = tts
        self.wake = wake
        self.speaker = speaker
        self.converse = converse
        self.start_stage = start_stage
        self.end_stage = end_stage
        self.run_id = run_id or uuid.uuid4().hex
        self.conversation_id = conversation_id or uuid.uuid4().hex
        self.timeout = _sane_timeout(timeout)
        self.binary_handler_id = (
            int(binary_handler_id) if binary_handler_id is not None else next(_HANDLER_IDS)
        )
        self.sample_rate = int(sample_rate)
        self.sample_width = int(sample_width)
        self.channels = int(channels)
        self.vad_enabled = bool(vad_enabled)
        self.vad_threshold = float(vad_threshold)
        self.vad_silence_ms = int(vad_silence_ms)
        self._tts_cache = tts_cache

        self.pipeline_id = getattr(pipeline, "id", None) or "jarvis"
        self.language = language or getattr(pipeline, "language", None) or "en"
        self.stt_engine = getattr(pipeline, "stt_engine", None) or "wyoming"
        self.tts_engine = getattr(pipeline, "tts_engine", None) or "wyoming"
        self.conversation_engine = (
            getattr(pipeline, "conversation_engine", None) or "ollama"
        )
        self.wake_engine = getattr(pipeline, "wake_engine", None) or "wyoming"
        self.tts_voice = tts_voice if tts_voice is not None else getattr(pipeline, "tts_voice", None)
        self.wake_word = wake_word if wake_word is not None else getattr(pipeline, "wake_word", None)

        # results, filled in as the run progresses
        self.events: list[PipelineEvent] = []
        self.detected_wake_word: str | None = None
        self.stt_text: str = ""
        self.response_text: str = ""
        self.tts_url: str | None = None
        self.tts_token: str | None = None
        self.error: PipelineError | None = None
        #: The speaker verdict, once there is one. `None` means no gate was
        #: active — never "it failed".
        self.speaker_verdict: Any = None

        self._event_cb: Callable[[str, dict[str, Any]], Any] | None = None
        self._audio_ms = 0.0
        #: The turn's audio, kept only while a gate is active and only up to
        #: :data:`MAX_VERIFY_BYTES`. Nothing is written to disk and it is
        #: dropped the moment the verdict is in — a voice assistant that
        #: accumulated recordings in order to check who was talking would have
        #: given up more privacy than the check buys back.
        self._verify_pcm: list[bytes] = []
        self._verify_bytes = 0
        self._verify_task: "asyncio.Task[Any] | None" = None

    # --- public API -------------------------------------------------------
    async def execute(
        self,
        audio_queue: "asyncio.Queue[bytes | None] | None" = None,
        event_cb: Callable[[str, dict[str, Any]], Any] | None = None,
        *,
        text: str | None = None,
    ) -> "PipelineRun":
        """Run the pipeline. `None` in `audio_queue` means end-of-audio."""
        self._event_cb = event_cb
        await self._emit(
            EVENT_RUN_START,
            {
                "pipeline": self.pipeline_id,
                "language": self.language,
                "runner_data": {
                    "stt_binary_handler_id": self.binary_handler_id,
                    "timeout": int(self.timeout),
                },
            },
        )
        try:
            if self.timeout and self.timeout > 0:
                await asyncio.wait_for(self._execute(audio_queue, text), self.timeout)
            else:
                await self._execute(audio_queue, text)
        except PipelineError as err:
            await self._fail(err)
        except (TimeoutError, asyncio.TimeoutError):
            await self._fail(PipelineError("timeout", f"pipeline timed out after {self.timeout}s"))
        except asyncio.CancelledError:
            await self._fail(PipelineError("cancelled", "pipeline run was cancelled"))
            raise
        except Exception as err:  # pragma: no cover - genuinely unexpected
            _LOGGER.exception("Unexpected error in pipeline run %s", self.run_id)
            await self._fail(PipelineError("unknown", str(err) or type(err).__name__))
        finally:
            # A run that timed out or was cancelled may still have a verifier
            # in a worker thread. Nobody is going to read its answer, and a
            # thread holding a copy of somebody's audio after the turn it
            # belonged to has ended is the one thing this feature must not do.
            self._discard_verification()
            await self._emit(EVENT_RUN_END, {})
        return self

    def _discard_verification(self) -> None:
        task, self._verify_task = self._verify_task, None
        if task is not None and not task.done():
            task.cancel()
        self._verify_pcm = []
        self._verify_bytes = 0

    async def execute_text(
        self,
        text: str,
        event_cb: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> "PipelineRun":
        """Text-only run (chat UI): straight into the conversation agent."""
        return await self.execute(None, event_cb, text=text)

    def runs_stage(self, stage: str) -> bool:
        return STAGE_ORDER[self.start_stage] <= STAGE_ORDER[stage] <= STAGE_ORDER[self.end_stage]

    @property
    def event_types(self) -> list[str]:
        return [event.type for event in self.events]

    # --- stages -----------------------------------------------------------
    async def _execute(
        self, audio_queue: "asyncio.Queue[bytes | None] | None", text: str | None
    ) -> None:
        if self.start_stage == "tts":
            # "speak this" run: the text *is* the response.
            self.response_text = (text or "").strip()
            if not self.response_text:
                raise PipelineError("tts-failed", "a tts-only run needs text to speak")
            await self._run_tts(self.response_text)
            return

        if text is not None:
            # A text run (chat UI) skips wake/stt whatever start_stage says.
            self.stt_text = text.strip()
        else:
            if self.runs_stage("wake"):
                await self._run_wake(self._require_queue(audio_queue))
            if self.runs_stage("stt"):
                self.stt_text = await self._run_stt(self._require_queue(audio_queue))

        if not self.runs_stage("intent"):
            return

        # Whose voice it was, before anything is done about what it said.
        # Deliberately ahead of the empty-transcript check: "somebody who is not
        # you said something I could not make out" and "you said something I
        # could not make out" are different events, and the gate is the only
        # thing that can tell them apart.
        refusal = await self._settle_speaker()
        if refusal is not None:
            # Refused. The turn ends here — the transcript is never handed to
            # the agent, so nothing a stranger said can reach a tool — but it
            # ends OUT LOUD by default. An assistant that goes silent is
            # indistinguishable from one that did not hear you, and a false
            # reject is the failure this feature will actually produce; the
            # person it locks out has to be told why. `on_reject: silent` is
            # there for anyone who would rather a stranger learn nothing.
            self.response_text = refusal
            if refusal and self.runs_stage("tts"):
                await self._run_tts(refusal)
            return

        if not self.stt_text:
            raise PipelineError("stt-no-text-recognized", "no text recognised")

        self.response_text = await self._run_intent(self.stt_text)

        if not self.runs_stage("tts"):
            return
        if not self.response_text:
            _LOGGER.debug("Pipeline %s: empty response, skipping TTS", self.run_id)
            return
        await self._run_tts(self.response_text)

    def _require_queue(
        self, audio_queue: "asyncio.Queue[bytes | None] | None"
    ) -> "asyncio.Queue[bytes | None]":
        if audio_queue is None:
            raise PipelineError("audio-missing", "this pipeline run needs an audio queue")
        return audio_queue

    async def _run_wake(self, audio_queue: "asyncio.Queue[bytes | None]") -> str | None:
        if self.wake is None:
            raise PipelineError("wake-provider-missing", "no wake word service configured")
        await self._emit(
            EVENT_WAKE_START,
            {"engine": self.wake_engine, "metadata": self._audio_metadata()},
        )
        try:
            detected = await self.wake.detect(self._audio_stream(audio_queue, vad=False))
        except PipelineError:
            raise
        except TimeoutError as err:
            # The wake service has its own deadline; a client waiting for the
            # wake word wants "nobody said it", not a generic stream failure.
            raise PipelineError(
                "wake-word-timeout", str(err) or "wake word was not detected"
            ) from err
        except Exception as err:
            raise PipelineError("wake-stream-failed", str(err) or type(err).__name__) from err
        if not detected:
            raise PipelineError("wake-word-timeout", "wake word was not detected")
        self.detected_wake_word = detected
        await self._emit(
            EVENT_WAKE_END,
            {
                "wake_word_output": {
                    "wake_word_id": detected,
                    "timestamp": int(self._audio_ms),
                }
            },
        )
        return detected

    async def _run_stt(self, audio_queue: "asyncio.Queue[bytes | None]") -> str:
        if self.stt is None:
            raise PipelineError("stt-provider-missing", "no speech-to-text service configured")
        await self._emit(
            EVENT_STT_START,
            {"engine": self.stt_engine, "metadata": self._audio_metadata()},
        )
        try:
            text = await self.stt.transcribe(
                self._audio_stream(audio_queue), rate=self.sample_rate
            )
        except PipelineError:
            raise
        except Exception as err:
            raise PipelineError("stt-stream-failed", str(err) or type(err).__name__) from err
        text = (text or "").strip()
        await self._emit(EVENT_STT_END, {"stt_output": {"text": text}})
        return text

    # --- speaker ----------------------------------------------------------
    def _gate_active(self) -> bool:
        gate = self.speaker
        if gate is None:
            return False
        try:
            return bool(gate.active)
        except Exception:  # pragma: no cover - a broken gate must not eat turns
            _LOGGER.exception("Speaker gate could not report whether it is active")
            return False

    def _start_verification(self) -> None:
        """Kick the verifier off the moment the audio ends.

        Placed here rather than after `stt-end` on purpose. The recogniser's
        work does not finish when the audio does — Whisper transcribes what it
        has been given, which is most of the round trip — so starting
        verification at end-of-audio hides it entirely behind a wait that was
        already happening. Doing it afterwards would add its whole cost to
        every turn instead.

        In a worker thread because the embedding is CPU-bound pure Python and
        would otherwise block the event loop for long enough to stall every
        other client on this server.
        """
        if not self._verify_pcm or self._verify_task is not None:
            return
        pcm = b"".join(self._verify_pcm)
        self._verify_pcm = []
        gate = self.speaker
        rate, width = self.sample_rate, self.sample_width
        self._verify_task = asyncio.create_task(
            asyncio.to_thread(gate.check, pcm, rate, width)
        )

    async def _settle_speaker(self) -> str | None:
        """Wait for the verdict and decide what happens to the turn.

        Returns the line to say when the turn is refused, `""` for a silent
        refusal, and `None` when the turn may proceed — which includes every
        case where no gate is configured, the gate is in `observe`, or the
        audio was unverifiable and the policy allows those through.
        """
        task, self._verify_task = self._verify_task, None
        if task is None:
            return None
        try:
            verdict = await task
        except asyncio.CancelledError:
            raise
        except Exception:
            # A verifier that crashed has not said "this is a stranger", and
            # treating a bug as a refusal would lock the owner out of their own
            # house on a traceback. It is reported and the turn proceeds; the
            # tier system still stands in front of anything dangerous.
            _LOGGER.exception("Speaker verification failed; letting the turn through")
            return None

        self.speaker_verdict = verdict
        payload = verdict.as_dict() if hasattr(verdict, "as_dict") else {"verdict": str(verdict)}
        gate = self.speaker
        payload["mode"] = getattr(gate, "mode", "unknown")
        payload["enforced"] = bool(gate.blocks(verdict))
        await self._emit(EVENT_SPEAKER_END, {"speaker_output": payload})

        if not gate.blocks(verdict):
            return None

        _LOGGER.info(
            "Refusing a turn: speaker score %.2f against threshold %.2f (%s)",
            getattr(verdict, "score", float("nan")),
            getattr(verdict, "threshold", float("nan")),
            getattr(verdict, "reason", "?"),
        )
        await self._fail(
            PipelineError(ERROR_NOT_RECOGNISED, "that voice is not the enrolled owner's")
        )
        if getattr(gate, "on_reject", "speak") != "speak":
            return ""
        return str(getattr(gate, "refusal", "") or "")

    async def _run_intent(self, text: str) -> str:
        await self._emit(
            EVENT_INTENT_START, {"engine": self.conversation_engine, "language": self.language}
        )
        reply = ""
        try:
            async for delta in self._converse_deltas(text):
                if not delta:
                    continue
                reply += delta
                await self._emit(
                    EVENT_INTENT_PROGRESS,
                    {"chat_log_delta": {"role": "assistant", "content": delta}},
                )
        except PipelineError:
            raise
        except Exception as err:
            raise PipelineError("intent-failed", str(err) or type(err).__name__) from err

        await self._emit(
            EVENT_INTENT_END,
            {
                "intent_output": {
                    "response": {
                        "speech": {"plain": {"speech": reply, "extra_data": None}},
                        "response_type": "action_done",
                        "data": {},
                    },
                    "conversation_id": self.conversation_id,
                }
            },
        )
        return reply

    async def _run_tts(self, text: str) -> str:
        if self.tts is None:
            raise PipelineError("tts-provider-missing", "no text-to-speech service configured")
        await self._emit(
            EVENT_TTS_START,
            {
                "engine": self.tts_engine,
                "language": self.language,
                "voice": self.tts_voice,
                "tts_input": text,
            },
        )
        try:
            result = await self._synthesize(text)
            pcm, rate, width, channels = result
            audio = wav_bytes(pcm, rate, width, channels)
        except PipelineError:
            raise
        except Exception as err:
            raise PipelineError("tts-failed", str(err) or type(err).__name__) from err

        self.tts_token, self.tts_url = store_tts_audio(
            self.jarvis, audio, TTS_MIME_TYPE, cache=self._tts_cache
        )
        await self._emit(
            EVENT_TTS_END,
            {"tts_output": {"url": self.tts_url, "mime_type": TTS_MIME_TYPE}},
        )
        return self.tts_url

    async def _synthesize(self, text: str) -> tuple[bytes, int, int, int]:
        try:
            result = self.tts.synthesize(text, voice=self.tts_voice)
        except TypeError:
            result = self.tts.synthesize(text)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, tuple) or len(result) != 4:
            raise PipelineError(
                "tts-failed", "synthesize() must return (pcm, rate, width, channels)"
            )
        return result

    # --- audio ------------------------------------------------------------
    def _audio_metadata(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "format": "wav",
            "codec": "pcm",
            "bit_rate": self.sample_width * 8,
            "sample_rate": self.sample_rate,
            "channel": self.channels,
        }

    async def _audio_stream(
        self, audio_queue: "asyncio.Queue[bytes | None]", vad: bool = True
    ) -> AsyncIterator[bytes]:
        """Yield chunks off the queue until None, emitting VAD events."""
        bytes_per_second = max(self.sample_rate * self.sample_width * self.channels, 1)
        speaking = False
        silence_ms = 0.0
        # Only the stt leg is kept: `vad=False` is the wake stage, which is the
        # room before anybody addressed us and is not this turn's speaker.
        keeping = vad and self._gate_active()
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                break
            chunk = bytes(chunk)
            if not chunk:
                continue
            if keeping and self._verify_bytes < MAX_VERIFY_BYTES:
                self._verify_pcm.append(chunk)
                self._verify_bytes += len(chunk)
            chunk_ms = len(chunk) * 1000 / bytes_per_second
            self._audio_ms += chunk_ms
            if vad and self.vad_enabled:
                level = rms(chunk, self.sample_width)
                if level >= self.vad_threshold:
                    silence_ms = 0.0
                    if not speaking:
                        speaking = True
                        await self._emit(
                            EVENT_STT_VAD_START, {"timestamp": int(self._audio_ms)}
                        )
                elif speaking:
                    silence_ms += chunk_ms
                    if silence_ms >= self.vad_silence_ms:
                        speaking = False
                        silence_ms = 0.0
                        await self._emit(
                            EVENT_STT_VAD_END, {"timestamp": int(self._audio_ms)}
                        )
            yield chunk
        if speaking:
            await self._emit(EVENT_STT_VAD_END, {"timestamp": int(self._audio_ms)})
        if keeping:
            # End of audio, and the recogniser has not answered yet: this is
            # the window the verification is meant to hide inside.
            self._start_verification()

    # --- conversation -----------------------------------------------------
    async def _converse_deltas(self, text: str) -> AsyncIterator[str]:
        if self.converse is None:
            raise PipelineError("intent-failed", "no conversation agent configured")

        result = self._call_converse(text)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, str):
            yield result
            return
        if hasattr(result, "__aiter__"):
            async for item in result:
                delta = _delta_text(item)
                if delta:
                    yield delta
            return
        if hasattr(result, "__iter__"):
            for item in result:
                delta = _delta_text(item)
                if delta:
                    yield delta
            return
        delta = _delta_text(result)
        if delta:
            yield delta

    def _call_converse(self, text: str) -> Any:
        assert self.converse is not None
        try:
            params = inspect.signature(self.converse).parameters
        except (TypeError, ValueError):  # builtins, C callables
            return self.converse(text, self.conversation_id)
        positional = [
            p
            for p in params.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        takes_var = any(p.kind is p.VAR_POSITIONAL for p in params.values())
        if len(positional) >= 2 or takes_var:
            return self.converse(text, self.conversation_id)
        if "conversation_id" in params:
            return self.converse(text, conversation_id=self.conversation_id)
        return self.converse(text)

    # --- events -----------------------------------------------------------
    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = PipelineEvent(event_type, data)
        self.events.append(event)
        if self.jarvis is not None:
            try:
                self.jarvis.bus.fire(
                    EVENT_VOICE_PIPELINE,
                    {"run_id": self.run_id, "type": event_type, "data": data},
                )
            except Exception:  # pragma: no cover - a bad listener must not kill a run
                _LOGGER.debug("Could not mirror %s onto the bus", event_type, exc_info=True)
        if self._event_cb is None:
            return
        try:
            result = self._event_cb(event_type, data)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error in pipeline event callback for %s", event_type)

    async def _fail(self, err: PipelineError) -> None:
        self.error = err
        _LOGGER.debug("Pipeline run %s failed: %s (%s)", self.run_id, err.message, err.code)
        await self._emit(EVENT_ERROR, {"code": err.code, "message": err.message})


def _delta_text(item: Any) -> str:
    """Pull the text out of whatever a conversation agent yields."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("content", "delta", "text", "response", "speech"):
            value = item.get(key)
            if isinstance(value, str):
                return value
        nested = item.get("chat_log_delta")
        if isinstance(nested, dict):
            return _delta_text(nested)
        return ""
    for attr in ("content", "delta", "text"):
        value = getattr(item, attr, None)
        if isinstance(value, str):
            return value
    return str(item)
