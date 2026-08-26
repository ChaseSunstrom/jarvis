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
import contextlib
import inspect
import itertools
import logging
import math
import re
import secrets
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..const import EVENT_VOICE_PIPELINE as _EVENT_VOICE_PIPELINE
from ..const import VOICE_WAKE_END as _VOICE_WAKE_END
from .speech_text import spoken_form
from .audio import DEFAULT_CHANNELS, DEFAULT_RATE, DEFAULT_WIDTH, rms, wav_bytes

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis
    from .pipelines import Pipeline

_LOGGER = logging.getLogger(__name__)

# --- event names ------------------------------------------------------------
EVENT_RUN_START = "run-start"
EVENT_RUN_END = "run-end"
EVENT_WAKE_START = "wake_word-start"
EVENT_WAKE_END = _VOICE_WAKE_END
EVENT_STT_START = "stt-start"
EVENT_STT_VAD_START = "stt-vad-start"
EVENT_STT_VAD_END = "stt-vad-end"
EVENT_STT_END = "stt-end"
EVENT_INTENT_START = "intent-start"
EVENT_INTENT_PROGRESS = "intent-progress"
EVENT_INTENT_END = "intent-end"
#: A sentence of the reply, synthesised while the model is still writing the
#: rest (M60). Carries `index`, `text` and `tts_output` like `tts-end`; a client
#: that plays chunks skips the whole-reply audio `tts-end` still delivers, and
#: one that does not (the phone, today) plays that as it always did.
EVENT_TTS_CHUNK = "tts-chunk"
#: What the turn is doing between `intent-start` and `intent-end`, for a
#: surface that shows the working rather than only the answer.
#:
#: These carry the SAME payloads as the `jarvis_tool_started` /
#: `jarvis_tool_finished` bus events and are not a replacement for them: the bus
#: is the house-wide broadcast every subscriber sees, and these are scoped to
#: one run on one socket. A chat client needs the second kind — with only the
#: bus it cannot tell which of two concurrent turns a tool row belongs to, and
#: putting somebody else's `unlock_door` in your transcript is not a cosmetic
#: mistake.
EVENT_INTENT_TOOL_START = "intent-tool-start"
EVENT_INTENT_TOOL_END = "intent-tool-end"
#: The model wrote a tool call out as text instead of making one, and is being
#: asked to do it properly. Surfaced rather than only logged so a client can
#: say "still working" instead of appearing to stall for an extra round.
EVENT_INTENT_TOOL_NARRATED = "intent-tool-narrated"
#: A slice of the model's reasoning. Never part of `intent-progress`, because
#: that is the text the TTS speaks and the HUD renders as the reply.
EVENT_INTENT_THINKING = "intent-thinking"
EVENT_TTS_START = "tts-start"
EVENT_TTS_END = "tts-end"
EVENT_ERROR = "error"
#: Emitted between `stt-end` and `intent-start` whenever a speaker gate is
#: active — in `observe` as well as `enforce`, because a mode that produced no
#: events would give you nothing to set a threshold from.
EVENT_SPEAKER_END = "speaker-end"

#: The verdict, on the house bus, for surfaces that did not run the turn.
#: Shape and field names: `tests/contracts/speaker_verdict.json`.
EVENT_SPEAKER_VERDICT = "jarvis_speaker_verdict"

#: The turn was refused because the voice was not an enrolled person's.
#: Distinct from every stt code: "I heard you and you are not who this belongs
#: to" is a different thing from "I could not make out what you said", and a
#: client that shows them the same way is lying to whichever one it is.
ERROR_NOT_RECOGNISED = "speaker-not-recognised"

# Bus event mirroring every pipeline event (handy for the API/websocket layer).
#: Imported rather than retyped: `automation/triggers.py` listens for this
#: and a second copy of the string is a rename that half-lands.
EVENT_VOICE_PIPELINE = _EVENT_VOICE_PIPELINE

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

#: Turn events buffered between two drains.
#:
#: Reasoning arrives token by token, so this fills fastest when a model is
#: thinking hard — which is also when the client most wants to see something.
#: Consecutive reasoning slices are coalesced into one frame on the way out
#: (see `_drain_turn_events`), so in practice the queue holds tool events and a
#: few hundred characters of thought, not one entry per token.
#:
#: The bound is a memory guard, not a schedule. It used to be both by accident:
#: the only drain hung off the intent loop, which does not tick while the model
#: is thinking or a tool is running, so a turn that queued more than this
#: before its first token lost the oldest — the tool rows, and the reasoning
#: before them, evicted from the left with nothing said. `TURN_EVENT_DRAIN_SECONDS`
#: is the schedule now, and `test_a_tool_row_survives_a_turn_that_thinks_before_it_speaks`
#: is what keeps the two separate.
MAX_QUEUED_TURN_EVENTS = 512

#: How often the intent stage flushes what the agent has reported.
#:
#: Fast enough that a tool row reaches the console while the tool is still
#: running, slow enough that a turn spent entirely in reasoning costs a few
#: dozen wakeups rather than one per token.
TURN_EVENT_DRAIN_SECONDS = 0.05

#: How much reasoning goes out in one frame. A thousand characters is a
#: paragraph — enough that the collapsed block on the client grows visibly,
#: small enough that no single frame is worth chunking.
MAX_THINKING_FRAME_CHARS = 1000

#: Agent turn-event name -> the pipeline event it is re-emitted as.
_TURN_EVENT_NAMES = {
    "tool-start": EVENT_INTENT_TOOL_START,
    "tool-end": EVENT_INTENT_TOOL_END,
    "tool-narrated": EVENT_INTENT_TOOL_NARRATED,
    "thinking": EVENT_INTENT_THINKING,
}

_HANDLER_IDS = itertools.count(1)

__all__ = [
    "EVENT_INTENT_TOOL_NARRATED",
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


#: A chunk with nothing in it a synthesiser can pronounce.
#:
#: Piper splits its input into sentences and synthesises each one. A leading
#: fragment with no letters or digits in it — "...?", "—", "!!" — phonemises to
#: nothing, its wav writer closes having written no frames, and **the whole
#: request fails**: `wave.Error: # channels not specified`, no audio for any of
#: the text. Measured against `wyoming-piper:2.3.1` on this host: the reply
#: "...? Shall I fetch something, Sir?" produced silence and an error, while the
#: same sentence without the ellipsis produced 183 KB.
#:
#: A model reacting to a noise it could not make out opens with an ellipsis
#: often, so this is not an edge case — it is what Jarvis says when the room is
#: quiet and something rustles.
_SPEAKABLE = re.compile(r"[0-9A-Za-z\u00c0-\u024f]")


def speakable(text: str) -> str:
    """`text` with the fragments no synthesiser can say removed.

    Whitespace collapsed (a reply that begins with a blank line is common and
    means nothing out loud) and any leading or trailing chunk that contains no
    letter or digit dropped. Returns "" when nothing is left to say, which the
    caller treats as "do not speak", never as an error.
    """
    # Said, not shown (M73): the markdown and the symbols the model writes for
    # a screen become words here, and only here — the transcript, the console
    # and the archive keep the reply as written.
    collapsed = " ".join(spoken_form(str(text or "")).split())
    if not collapsed:
        return ""
    parts = [part for part in re.split(r"(?<=[.!?])\s+", collapsed) if part]
    kept = [part for part in parts if _SPEAKABLE.search(part)]
    return " ".join(kept)


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
        early_speech: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        run_id: str | None = None,
        sample_rate: int = DEFAULT_RATE,
        sample_width: int = DEFAULT_WIDTH,
        channels: int = DEFAULT_CHANNELS,
        audio_derived: bool = False,
        vad_enabled: bool = True,
        vad_threshold: float = DEFAULT_VAD_THRESHOLD,
        vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS,
        tts_cache: dict[str, tuple[bytes, str]] | None = None,
        device_id: str | None = None,
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
        #: True when this run's TEXT came out of a microphone on the client
        #: rather than off a keyboard. See :meth:`_settle_speaker`.
        self.audio_derived = bool(audio_derived)
        self.vad_enabled = bool(vad_enabled)
        self.vad_threshold = float(vad_threshold)
        self.vad_silence_ms = int(vad_silence_ms)
        self._tts_cache = tts_cache

        #: Which satellite this run belongs to, when it belongs to one. Empty
        #: for a run started by a browser or a REST call. On the bus mirror so
        #: an automation can say "the wake word, but only in the workshop" —
        #: without it every hook on a house-wide event fires for every room.
        self.device_id = str(device_id or "")

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
        #: Turn events the agent reported, waiting to be emitted.
        #:
        #: The agent calls back synchronously from inside its own streaming
        #: loop — it has to, or reporting a tool call would mean awaiting the
        #: socket in the middle of a model response — and `_emit` is a
        #: coroutine. This is the seam between the two: the callback appends,
        #: and the intent loop drains on every tick. Bounded because a runaway
        #: reasoning block must not be able to grow the process; the oldest go
        #: first, since a surface that has fallen behind wants the recent ones.
        self._turn_events: deque[tuple[str, dict[str, Any]]] = deque(
            maxlen=MAX_QUEUED_TURN_EVENTS
        )
        #: Held while draining, so the intent loop and the ticker below cannot
        #: emit at the same time.
        self._drain_lock = asyncio.Lock()
        self._audio_ms = 0.0
        #: Early speech (M60): how much of the reply has been synthesised
        #: sentence by sentence, the chunks' urls, and whether a tool has run
        #: this turn (which switches it off — see `_speak_early`).
        self.early_speech = early_speech
        self._spoken_upto = 0
        self.spoken_chunks: list[str] = []
        #: The sentences already synthesised, in order — what `_unspoken_tail`
        #: subtracts from the reply by TEXT, since after a tool call the
        #: authoritative reply is the last segment of the stream and a
        #: character index into the stream means nothing in it (M74).
        self._spoken_texts: list[str] = []
        #: Set when a tool starts: the next delta opens a new segment, and the
        #: cursor moves to its start so what the model wrote BEFORE the tool —
        #: its guess — is never spoken after it (M74).
        self._segment_reset = False
        self._tools_ran = False
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
        # `.strip()`, not just truthiness. A reasoning model whose whole turn is
        # a thinking block leaves the stripper "\n\n" to return, and "\n\n" is
        # truthy — so the guard below passed it to Piper, which synthesised
        # nothing and closed, and the turn failed with the thoroughly misleading
        # "TTS service returned no audio". The service was fine; it was asked to
        # say nothing.
        if not self.response_text.strip():
            _LOGGER.debug(
                "Pipeline %s: the reply was empty or whitespace, skipping TTS", self.run_id
            )
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
            # Nothing was verified. Usually that is right — no gate configured,
            # or a text run somebody typed — but there is one case where it is
            # a hole, and it is the one an owner is most likely to open by
            # accident.
            #
            # A client that transcribes on its own device sends WORDS, not
            # sound. `start_stage: "intent"`, no audio, nothing for the gate to
            # look at — so with `mode: enforce` and on-device transcription
            # both switched on, every turn walked past the check. Neither
            # setting looks dangerous; the combination silently disabled the
            # feature.
            #
            # It cannot be fixed by verifying on the phone, either. Android's
            # on-device recogniser OWNS the microphone — the app is handed
            # partial text and an RMS level, never samples — so there is no
            # audio on that device to embed. That is a platform fact, not a
            # gap in this codebase.
            #
            # So a run that admits its text came from a microphone is refused
            # when the gate is enforcing. Typed input is untouched: a person at
            # a keyboard holding the bearer token is authenticated by the
            # token, and this gate is about who is speaking in a room where the
            # microphone is open to whoever is standing there.
            #
            # A hostile client could simply not set the flag — but a hostile
            # client holding the token can already send any transcript it
            # likes. This closes the ACCIDENT, not the attack, and the docs say
            # so in those words.
            if self.audio_derived and self._gate_active():
                gate = self.speaker
                if getattr(gate, "mode", None) == "enforce":
                    _LOGGER.warning(
                        "Refusing a transcript from %s: it came from a microphone this "
                        "server never heard, and the speaker gate is enforcing",
                        "an on-device recogniser",
                    )
                    refused = {
                        "accepted": False,
                        "reason": "unverifiable-transcript",
                        "label": None,
                        "nearest": None,
                        "score": None,
                        "threshold": None,
                        "confidence": None,
                        "mode": gate.mode,
                        "enforced": True,
                    }
                    await self._emit(EVENT_SPEAKER_END, {"speaker_output": refused})
                    self._fire_speaker_verdict(refused)
                    await self._fail(
                        PipelineError(
                            ERROR_NOT_RECOGNISED,
                            "this text was transcribed on a device, so there is no audio "
                            "to check it against; turn off on-device transcription while "
                            "the speaker gate is enforcing",
                        )
                    )
                    if getattr(gate, "on_reject", "speak") != "speak":
                        return ""
                    return str(getattr(gate, "refusal", "") or "")
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
        # `check` may have learned from this turn (see SpeakerGate.adapt). It
        # deliberately does not write to disk — a verifier running in a worker
        # thread has no business touching the store — so persisting is here,
        # where there is an event loop and a Jarvis to save through.
        await self._persist_adapted_profile()
        payload = verdict.as_dict() if hasattr(verdict, "as_dict") else {"verdict": str(verdict)}
        gate = self.speaker
        payload["mode"] = getattr(gate, "mode", "unknown")
        payload["enforced"] = bool(gate.blocks(verdict))
        await self._emit(EVENT_SPEAKER_END, {"speaker_output": payload})
        self._fire_speaker_verdict(payload)

        if not gate.blocks(verdict):
            return None

        _LOGGER.info(
            "Refusing a turn: speaker score %.2f against threshold %.2f (%s)",
            getattr(verdict, "score", float("nan")),
            getattr(verdict, "threshold", float("nan")),
            getattr(verdict, "reason", "?"),
        )
        await self._fail(
            PipelineError(ERROR_NOT_RECOGNISED, "that voice is not an enrolled person's")
        )
        if getattr(gate, "on_reject", "speak") != "speak":
            return ""
        return str(getattr(gate, "refusal", "") or "")

    def _fire_speaker_verdict(self, payload: dict[str, Any]) -> None:
        """Put the verdict on the bus, under its own name.

        `speaker-end` already reaches the client that ran the turn. This is
        for everything else watching the house — the console's activity strip,
        the phone's — which otherwise had no way to draw "a stranger spoke to
        the kitchen satellite" because the pipeline mirror on the bus carries
        every event of every run under one type. The fields are the contract
        in `tests/contracts/speaker_verdict.json`; the console, the phone and
        `tests/test_speaker_gate.py` all read it. Never audio, never a vector.
        """
        if self.jarvis is None:
            return
        event = {
            key: payload.get(key)
            for key in (
                "accepted", "reason", "label", "nearest", "score", "threshold",
                "confidence", "mode", "enforced",
            )
        }
        event.update(
            {
                "run_id": self.run_id,
                "pipeline": self.pipeline_id,
                "device_id": self.device_id,
                "at": time.time(),
            }
        )
        try:
            self.jarvis.bus.fire(EVENT_SPEAKER_VERDICT, event)
        except Exception:  # pragma: no cover - a bad listener must not kill a run
            _LOGGER.debug("Could not fire %s", EVENT_SPEAKER_VERDICT, exc_info=True)

    def speaker_label(self) -> str | None:
        """Who this turn's voice was recognised as, or None.

        None for every turn the gate did not accept: no gate, `off`, typed
        text, a refusal let through in `observe`, and audio too short to
        judge. It is what the agent is told about the speaker, so it says
        nothing rather than guessing — "unverified" and "stranger" are not the
        same claim, and a prompt line that conflated them would have the model
        treating the owner with a cold as an intruder.
        """
        verdict = self.speaker_verdict
        if verdict is None or not getattr(verdict, "accepted", False):
            return None
        label = getattr(verdict, "label", None)
        return str(label) if label else None

    async def _persist_adapted_profile(self) -> None:
        """Write the profile back if this turn changed it.

        Best-effort on purpose. A store that would not write is a reason to log
        and carry on, not a reason to fail a turn the speaker already passed:
        the worst case is that the sample is learned again next time.
        """
        gate = self.speaker
        if not getattr(gate, "profile_dirty", False):
            return
        gate.profile_dirty = False
        profiles = getattr(gate, "profiles", None)
        if not profiles or self.jarvis is None:
            return
        try:
            from ..integrations.voice import async_save_profiles

            await async_save_profiles(self.jarvis, list(profiles))
        except Exception:
            _LOGGER.exception("Could not save the adapted voice profile")

    async def _run_intent(self, text: str) -> str:
        await self._emit(
            EVENT_INTENT_START, {"engine": self.conversation_engine, "language": self.language}
        )
        reply = ""
        # One utterance, one turn (M78). A phone's wake word and the console's
        # microphone both heard the sentence; the copy that arrives second
        # yields — no model, no tools, nothing said — and the surface is told
        # which listener is answering. The console has no device id and
        # counts as one listener of its own.
        if self.jarvis is not None:
            from ..api.devices import get_recent_listeners

            listeners = get_recent_listeners(self.jarvis)
            me = self.device_id or "console"
            other = listeners.already_heard_from(text, me)
            if other:
                _LOGGER.info(
                    "Pipeline %s: %r already heard from %s; this turn yields", self.run_id, text, other
                )
                await self._emit(
                    EVENT_INTENT_END,
                    {"chat_log": [], "duplicate_of": other, "conversation_id": self.conversation_id},
                )
                return ""
            listeners.heard(text, me)
            # Not listening while you enrol (M79): the phrases read aloud for
            # a voiceprint are sentences, and every listener in the room hears
            # them as commands. A client marks the enrolment before it records;
            # a turn that starts inside the window yields the same way.
            from ..api.speaker import enrolling

            if enrolling(self.jarvis):
                _LOGGER.info("Pipeline %s: an enrolment is in progress; this turn yields", self.run_id)
                await self._emit(
                    EVENT_INTENT_END,
                    {"chat_log": [], "enrolling": True, "conversation_id": self.conversation_id},
                )
                return ""
        # Drained by a task of its own, NOT off the back of the delta loop.
        #
        # The loop below used to carry the drain, with a comment claiming it
        # ran "on every tick, not only when there is text". It did not, and the
        # reason was one function away: `_converse_deltas` drops every falsy
        # delta, and the agent's rounds only ever yield visible text. So during
        # reasoning and during tool execution — precisely the seconds the rows
        # exist to narrate — the loop never ticked and nothing drained.
        #
        # `_turn_events` is a bounded deque that evicts from the LEFT, so a
        # turn that queued more than `MAX_QUEUED_TURN_EVENTS` events before
        # producing its first token silently lost its oldest ones: the tool
        # rows, and the reasoning that preceded them. Reproduced at 512.
        #
        # A ticker rather than a bigger bound: the bound is there to stop a
        # runaway turn growing the process, and raising it would only move the
        # cliff. The events want emitting when they happen.
        drainer = asyncio.ensure_future(self._drain_turn_events_until_done())
        try:
            async for delta in self._converse_deltas(text):
                # Still drained here as well as on the ticker, and this is the
                # half that guarantees ORDER: everything the agent queued
                # before this delta is emitted before it. Reasoning that
                # preceded a tool call has to reach the client ahead of the row
                # for that call, or the transcript reads as though the model
                # decided first and thought afterwards.
                await self._drain_turn_events()
                if not delta:
                    continue
                reply += delta
                await self._emit(
                    EVENT_INTENT_PROGRESS,
                    {"chat_log_delta": {"role": "assistant", "content": delta}},
                )
                await self._speak_early(reply, delta)
        except PipelineError:
            raise
        except Exception as err:
            raise PipelineError("intent-failed", str(err) or type(err).__name__) from err
        finally:
            # Stop the ticker before the last drain, so the two cannot be
            # emitting at once and interleave a half-merged reasoning frame.
            drainer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drainer
            # Whatever the agent reported after the last delta — the tail of a
            # tool round, the close of a reasoning block — still belongs to this
            # turn, and on an error path it is the most informative thing there
            # is.
            await self._drain_turn_events()

        # What the user is told, as distinct from everything the model said on
        # the way there. See `_authoritative_answer`.
        reply = self._authoritative_answer(reply)

        # Only when there is one: `data` carries what there is, and an empty
        # trace on every turn is a key every client has to ignore.
        trace = self._memory_trace()
        await self._emit(
            EVENT_INTENT_END,
            {
                "intent_output": {
                    "response": {
                        "speech": {"plain": {"speech": reply, "extra_data": None}},
                        "response_type": "action_done",
                        "data": {"memory_used": trace} if trace else {},
                    },
                    "conversation_id": self.conversation_id,
                }
            },
        )
        return reply

    async def _speak_early(self, reply_so_far: str, delta: str = "") -> None:
        """Synthesise each finished sentence while the model writes the next (M60).

        The wait a person notices on a voice turn is from the end of their
        sentence to the start of Jarvis's. Synthesising the whole reply after
        the model has finished puts the model's entire generation in front of
        the first word; this puts only the first sentence there. Each chunk is
        stored like the whole reply and announced as `tts-chunk`.

        Per segment (M74). Text before a tool call is the model guessing at
        what the tool will find, and `_authoritative_answer` drops it; M60
        therefore switched early speech off for the whole turn once a tool had
        run — which put a research answer's entire generation, twelve
        sentences, in front of its first word ("it took forever after it spit
        out text"). Now a tool call opens a new segment: the cursor moves to
        the first delta after it, the pending guess is never spoken, and the
        answer the tool made possible is spoken as it is written like any
        other. Off when the run has no synthesiser, and off by
        `early_speech: false`.
        """
        if not self.early_speech or self.tts is None:
            return
        if self._segment_reset:
            self._segment_reset = False
            self._spoken_upto = len(reply_so_far) - len(delta)
        pending = reply_so_far[self._spoken_upto :]
        # A sentence is finished when its end mark is followed by more text;
        # the last one is left for `tts-end`, which speaks what remains.
        for match in re.finditer(r"(.+?[.!?])(?=\s+\S)", pending, re.S):
            sentence = speakable(match.group(1).strip())
            self._spoken_upto += match.end()
            if not sentence:
                continue
            try:
                pcm, rate, width, channels = await self._synthesize(sentence)
            except Exception:  # noqa: BLE001 - early speech is a shortcut, never a failure
                _LOGGER.debug("Pipeline %s: early speech failed; the reply is spoken whole", self.run_id)
                self.early_speech = False
                return
            token, url = store_tts_audio(
                self.jarvis, wav_bytes(pcm, rate, width, channels), TTS_MIME_TYPE, cache=self._tts_cache
            )
            self.spoken_chunks.append(url)
            self._spoken_texts.append(sentence)
            await self._emit(
                EVENT_TTS_CHUNK,
                {
                    "index": len(self.spoken_chunks) - 1,
                    "text": sentence,
                    "tts_output": {"url": url, "mime_type": TTS_MIME_TYPE},
                },
            )

    def _unspoken_tail(self, spoken_text: str) -> str:
        """What the chunks did not cover, found by text rather than by index.

        Walks the sentences already said through the reply in order; the tail
        is what follows the last one found. A sentence that is not in the
        reply (the model's guess before a tool, dropped by
        `_authoritative_answer`) stops the walk where it is, so the tail can
        only ever be too long — said twice — never cut in the middle.
        """
        cursor = 0
        for said in self._spoken_texts:
            at = spoken_text.find(said, cursor)
            if at < 0:
                break
            cursor = at + len(said)
        return spoken_text[cursor:].strip()

    async def _run_tts(self, text: str) -> str:
        if self.tts is None:
            raise PipelineError("tts-provider-missing", "no text-to-speech service configured")
        # What is actually sent to the synthesiser. `self.response_text` keeps
        # the reply as written — the transcript, the console and the archive all
        # show what was said, not what was pronounceable.
        text = speakable(text)
        if not text:
            _LOGGER.debug(
                "Pipeline %s: nothing pronounceable in the reply, skipping TTS", self.run_id
            )
            return ""
        await self._emit(
            EVENT_TTS_START,
            {
                "engine": self.tts_engine,
                "language": self.language,
                "voice": self.tts_voice,
                "tts_input": text,
            },
        )
        if self.spoken_chunks:
            # The last sentence first (M74): a client playing the chunks is
            # waiting for exactly this, and behind the whole-reply clip it
            # waited for every sentence to be synthesised again.
            await self._speak_tail(text)
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
        output: dict[str, Any] = {"url": self.tts_url, "mime_type": TTS_MIME_TYPE}
        if self.spoken_chunks:
            # What the chunks did not cover — the last sentence — as its own
            # clip, so a client that played them plays only this; the whole
            # reply above stays for a client that plays only `tts-end`. Two
            # syntheses of the early sentences until every client chunks.
            output["chunks"] = len(self.spoken_chunks)
            # The tail was already sent as the last `tts-chunk` (M74), before
            # the whole-reply clip above was even asked for; a client that
            # played the chunks has nothing left to play. Kept in the payload,
            # as None, so a client written against M60's shape still reads it.
            output["remainder_url"] = None
        await self._emit(EVENT_TTS_END, {"tts_output": output})
        return self.tts_url

    async def _speak_tail(self, spoken_text: str) -> None:
        """The part of the reply after the last early sentence, as the last chunk."""
        tail = self._unspoken_tail(spoken_text)
        if not tail:
            return
        try:
            pcm, rate, width, channels = await self._synthesize(tail)
        except Exception:  # noqa: BLE001 - the whole reply follows anyway
            _LOGGER.debug("Pipeline %s: the tail failed; the whole reply follows", self.run_id)
            return
        _token, url = store_tts_audio(
            self.jarvis, wav_bytes(pcm, rate, width, channels), TTS_MIME_TYPE, cache=self._tts_cache
        )
        self.spoken_chunks.append(url)
        self._spoken_texts.append(tail)
        await self._emit(
            EVENT_TTS_CHUNK,
            {
                "index": len(self.spoken_chunks) - 1,
                "text": tail,
                "tts_output": {"url": url, "mime_type": TTS_MIME_TYPE},
            },
        )

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

    def _authoritative_answer(self, streamed: str) -> str:
        """The agent's final answer, when it has one that differs from the stream.

        The deltas are everything the model said, INCLUDING the words it wrote
        in a round that then called a tool — a guess made before the tool ran.
        Spoken, that is one breath containing both "the bed light is already
        off, sir" and "the bed light is now off, sir", and there is no screen
        out here to tell them apart.

        `ConversationAgent` already separates the two (`ConversationResult.text`
        versus `.preamble`). This asks for it, duck-typed and optional: any
        other conversation agent — the stand-in, a test's two-line coroutine —
        has no `last_result` and the stream is used unchanged. The prefix check
        is what keeps a stale result from a previous turn out of this one.
        """
        agent = getattr(self.converse, "__self__", None)
        result = getattr(agent, "last_result", None)
        preamble = str(getattr(result, "preamble", "") or "")
        answer = str(getattr(result, "text", "") or "")
        if not preamble or not answer:
            return streamed
        if not streamed.strip().startswith(preamble.strip()[:40]):
            return streamed
        _LOGGER.debug(
            "Dropping %d characters of preamble from the spoken answer", len(preamble)
        )
        return answer

    def _memory_trace(self) -> list[dict[str, str]]:
        """The remembered notes this turn was actually given.

        Sent with `intent-end` so a surface can answer "why did it say that?"
        with the entries the model READ, rather than by asking the model — which
        produces a plausible account of notes it may never have seen. Ids and
        text together: the id is what the memory page links to, and the text is
        the only part a person recognises.
        """
        agent = getattr(self.converse, "__self__", None)
        result = getattr(agent, "last_result", None)
        used = list(getattr(result, "memory_used", None) or [])
        if not used or self.jarvis is None:
            return []
        store = self.jarvis.data.get("memory")
        out: list[dict[str, str]] = []
        for entry_id in used[:8]:
            entry = store.get(entry_id) if store is not None else None
            out.append({"id": entry_id, "text": getattr(entry, "text", "") or ""})
        return out

    def _call_converse(self, text: str) -> Any:
        assert self.converse is not None
        try:
            params = inspect.signature(self.converse).parameters
        except (TypeError, ValueError):  # builtins, C callables
            return self.converse(text, self.conversation_id)
        takes_var_kw = any(p.kind is p.VAR_KEYWORD for p in params.values())
        # `on_event` is strictly opt-in. Every conversation agent this has ever
        # been handed — the service bridge, the no-agent stand-in, a test's
        # two-line coroutine — takes two arguments, and passing a third would
        # break all of them for a feature only the real agent implements.
        wants_events = "on_event" in params or takes_var_kw
        # Who is speaking, for an agent that can take it — the same opt-in
        # rule, and only when there is somebody to name: a turn the gate did
        # not accept passes nothing, so the agent cannot mistake "unverified"
        # for "a stranger" (see :meth:`speaker_label`). Not sent to a converse
        # that lacks the parameter, or a stand-in in a test would break on the
        # day the gate first recognised anyone.
        extra: dict[str, Any] = {}
        speaker = self.speaker_label()
        if speaker is not None and ("speaker" in params or takes_var_kw):
            extra["speaker"] = speaker
        # `spoken` likewise (M66): whether this run will read the reply aloud,
        # so a question the turn raises is not read aloud twice — once by the
        # reply and once by the phone. Only the real agent takes it; the voice
        # integration's wrapper forwards keywords, so it reaches the agent
        # through that too.
        wants_spoken = "spoken" in params or takes_var_kw
        if wants_spoken:
            extra["spoken"] = self.runs_stage("tts")
        positional = [
            p
            for p in params.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        takes_var = any(p.kind is p.VAR_POSITIONAL for p in params.values())
        if wants_events:
            return self.converse(
                text, self.conversation_id, on_event=self._on_turn_event, **extra
            )
        if extra:
            return self.converse(text, self.conversation_id, **extra)
        if len(positional) >= 2 or takes_var:
            return self.converse(text, self.conversation_id)
        if "conversation_id" in params:
            return self.converse(text, conversation_id=self.conversation_id)
        return self.converse(text)

    # --- turn events (tool calls and reasoning) ---------------------------
    def _on_turn_event(self, event_type: str, data: dict[str, Any]) -> None:
        """The agent reporting something mid-turn. Synchronous and cheap.

        Runs inside the model's streaming loop, so it does exactly one thing:
        put the event where the intent loop will find it. Anything slower here
        is latency between a token and the user hearing it.
        """
        name = _TURN_EVENT_NAMES.get(event_type)
        if name is None:
            return
        self._turn_events.append((name, data if isinstance(data, dict) else {}))

    async def _drain_turn_events_until_done(self) -> None:
        """Flush the agent's turn events while the turn is still running.

        Cancelled by the intent stage when the model stops streaming. The
        interval is short enough that a tool row appears while the tool is
        still running — which is the whole point of the row — and long enough
        that a quiet turn costs a handful of wakeups.
        """
        try:
            while True:
                await asyncio.sleep(TURN_EVENT_DRAIN_SECONDS)
                await self._drain_turn_events()
        except asyncio.CancelledError:
            raise

    async def _drain_turn_events(self) -> None:
        """Emit everything the agent has reported since the last tick.

        Consecutive reasoning slices are merged, because they arrive one token
        at a time: a frame per token would put thousands of websocket writes
        between "thinking" and "answered", and the client concatenates them
        into one block anyway. Tool events are never merged — each is a
        distinct row with its own identity.
        """
        # Serialised against the ticker: both call this, and two drains
        # interleaving would split a coalesced reasoning frame in half and
        # could emit a tool row between the two pieces.
        async with self._drain_lock:
            await self._drain_locked()

    async def _drain_locked(self) -> None:
        pending: str = ""
        while self._turn_events:
            name, data = self._turn_events.popleft()
            if name == EVENT_INTENT_TOOL_START:
                self._tools_ran = True
                self._segment_reset = True
            if name == EVENT_INTENT_THINKING:
                pending += str(data.get("delta") or "")
                if len(pending) < MAX_THINKING_FRAME_CHARS:
                    continue
                await self._emit(EVENT_INTENT_THINKING, {"delta": pending})
                pending = ""
                continue
            if pending:
                # Order is the point: reasoning that preceded a tool call has to
                # reach the client before the row for that call, or the
                # transcript reads as though it decided first and thought after.
                await self._emit(EVENT_INTENT_THINKING, {"delta": pending})
                pending = ""
            await self._emit(name, data)
        if pending:
            await self._emit(EVENT_INTENT_THINKING, {"delta": pending})

    # --- events -----------------------------------------------------------
    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = PipelineEvent(event_type, data)
        self.events.append(event)
        if self.jarvis is not None:
            try:
                self.jarvis.bus.fire(
                    EVENT_VOICE_PIPELINE,
                    {
                        "run_id": self.run_id,
                        "type": event_type,
                        "data": data,
                        # Identity, so a listener can filter without having to
                        # correlate run ids with a second event.
                        "pipeline": self.pipeline_id,
                        "device_id": self.device_id,
                    },
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
