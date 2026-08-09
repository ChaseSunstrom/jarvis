"""Voice integration: wires the Wyoming containers into Jarvis.

    voice:
      stt:  {host: 127.0.0.1, port: 10300}
      tts:  {host: 127.0.0.1, port: 10200, voice: en_GB-alan-medium}
      wake: {host: 127.0.0.1, port: 10400, model: hey_jarvis}
      language: en
      pipelines:
        - name: Jarvis
          voice: en_GB-alan-medium
          wake_word: hey_jarvis

Everything lands in ``jarvis.data["voice"]`` as a :class:`VoiceData`:

    data.stt / data.tts / data.wake   Wyoming clients (lazy — they connect per call)
    data.pipelines                    the :class:`PipelineStore`
    data.async_create_run(...)        a ready-to-execute :class:`PipelineRun`

Synthesised audio is cached at ``jarvis.data["tts_cache"][token] = (wav, mime)``
for the API layer to serve at ``/api/tts_proxy/<token>.wav``.

Tests inject fakes by presetting ``jarvis.data["voice_stt_client"]``,
``["voice_tts_client"]``, ``["voice_wake_client"]`` or ``["conversation_agent"]``
before calling :func:`async_setup`.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...voice.audio import wav_bytes
from ...voice.pipeline import (
    DATA_TTS_CACHE,
    TTS_MIME_TYPE,
    PipelineError,
    PipelineRun,
    store_tts_audio,
)
from ...voice.pipelines import DEFAULT_WAKE_WORD, Pipeline, PipelineStore
from ...voice.wyoming import (
    WyomingError,
    WyomingSttClient,
    WyomingTtsClient,
    WyomingWakeClient,
    wyoming_info,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...services import ServiceCall

_LOGGER = logging.getLogger(__name__)

DOMAIN = "voice"
DEPENDENCIES: list[str] = []

DATA_VOICE = "voice"
DATA_STT_CLIENT = "voice_stt_client"
DATA_TTS_CLIENT = "voice_tts_client"
DATA_WAKE_CLIENT = "voice_wake_client"
DATA_CONVERSATION_AGENT = "conversation_agent"

SERVICE_SAY = "say"
SERVICE_GET_PIPELINES = "get_pipelines"

EVENT_VOICE_SAID = "voice_said"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_STT_PORT = 10300
DEFAULT_TTS_PORT = 10200
DEFAULT_WAKE_PORT = 10400
DEFAULT_LANGUAGE = "en"

NO_AGENT_REPLY = "Sorry, no conversation agent is configured yet."

__all__ = [
    "DOMAIN",
    "VoiceData",
    "async_create_run",
    "async_say",
    "async_setup",
    "get_tts_audio",
    "get_voice_data",
]


@dataclass
class VoiceData:
    """Everything the voice integration hangs on to."""

    jarvis: "Jarvis"
    pipelines: PipelineStore
    stt: Any = None
    tts: Any = None
    wake: Any = None
    config: dict[str, Any] = field(default_factory=dict)
    language: str = DEFAULT_LANGUAGE
    tts_voice: str | None = None
    wake_word: str | None = DEFAULT_WAKE_WORD

    def async_create_run(
        self,
        pipeline: Pipeline | str | None = None,
        *,
        start_stage: str = "stt",
        end_stage: str = "tts",
        conversation_id: str | None = None,
        converse: Any = None,
        **kwargs: Any,
    ) -> PipelineRun:
        """Build a :class:`PipelineRun` bound to the configured services."""
        resolved = (
            pipeline
            if isinstance(pipeline, Pipeline)
            else self.pipelines.resolve(pipeline)
        )
        return PipelineRun(
            self.jarvis,
            pipeline=resolved,
            stt=self.stt,
            tts=self.tts,
            wake=self.wake,
            converse=converse or resolve_conversation_agent(self.jarvis),
            start_stage=start_stage,
            end_stage=end_stage,
            conversation_id=conversation_id,
            language=resolved.language or self.language,
            tts_voice=resolved.tts_voice or self.tts_voice,
            wake_word=resolved.wake_word or self.wake_word,
            **kwargs,
        )

    async def async_info(self) -> dict[str, Any]:
        """Ask each configured Wyoming service to describe itself."""
        info: dict[str, Any] = {}
        for name, client in (("stt", self.stt), ("tts", self.tts), ("wake", self.wake)):
            if client is None:
                continue
            host = getattr(client, "host", None)
            port = getattr(client, "port", None)
            if host is None or port is None:
                continue
            try:
                info[name] = await wyoming_info(host, port)
            except (WyomingError, OSError) as err:
                info[name] = {"error": str(err)}
        return info


# --- helpers other integrations use -----------------------------------------
def get_voice_data(jarvis: "Jarvis") -> VoiceData | None:
    data = jarvis.data.get(DATA_VOICE)
    return data if isinstance(data, VoiceData) else None


def get_tts_audio(jarvis: "Jarvis", token: str) -> tuple[bytes, str] | None:
    """Look up cached TTS audio by token (used by /api/tts_proxy/<token>.wav)."""
    if token.endswith(".wav"):
        token = token[: -len(".wav")]
    entry = jarvis.data.get(DATA_TTS_CACHE, {}).get(token)
    if entry is None:
        return None
    return entry


def async_create_run(
    jarvis: "Jarvis",
    pipeline: Pipeline | str | None = None,
    **kwargs: Any,
) -> PipelineRun:
    data = get_voice_data(jarvis)
    if data is None:
        raise PipelineError("voice-not-set-up", "the voice integration is not set up")
    return data.async_create_run(pipeline, **kwargs)


def resolve_conversation_agent(jarvis: "Jarvis") -> Any:
    """Find something that can hold a conversation, else a polite stand-in.

    Looked up per run so the llm integration can be set up in any order.
    """
    candidate = jarvis.data.get(DATA_CONVERSATION_AGENT)
    if callable(candidate):
        return candidate

    llm = jarvis.data.get("llm")
    for attr in ("async_converse", "converse", "async_process", "process"):
        method = getattr(llm, attr, None)
        if callable(method):
            return method

    if jarvis.services.has_service("conversation", "process"):

        async def _via_service(text: str, conversation_id: str | None = None) -> str:
            result = await jarvis.services.async_call(
                "conversation",
                "process",
                {"text": text, "conversation_id": conversation_id},
                blocking=True,
                return_response=True,
            )
            if isinstance(result, dict):
                speech = (
                    result.get("response", {})
                    .get("speech", {})
                    .get("plain", {})
                    .get("speech")
                )
                if isinstance(speech, str):
                    return speech
                for key in ("speech", "text", "response"):
                    if isinstance(result.get(key), str):
                        return str(result[key])
            return str(result or "")

        return _via_service

    async def _no_agent(text: str, conversation_id: str | None = None) -> str:
        _LOGGER.warning("No conversation agent configured; voice run got %r", text)
        return NO_AGENT_REPLY

    return _no_agent


async def async_say(
    jarvis: "Jarvis",
    text: str,
    voice: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Synthesise `text`, cache the WAV and return {token, url, mime_type}."""
    data = get_voice_data(jarvis)
    if data is None or data.tts is None:
        raise PipelineError("tts-provider-missing", "no text-to-speech service configured")
    result = data.tts.synthesize(text, voice=voice or data.tts_voice)
    if inspect.isawaitable(result):
        result = await result
    pcm, rate, width, channels = result
    audio = wav_bytes(pcm, rate, width, channels)
    token, url = store_tts_audio(jarvis, audio, TTS_MIME_TYPE)
    return {
        "token": token,
        "url": url,
        "mime_type": TTS_MIME_TYPE,
        "text": text,
        "voice": voice or data.tts_voice,
        "language": language or data.language,
    }


# --- setup ------------------------------------------------------------------
def _section(config: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Normalise a `stt:`/`tts:`/`wake:` block. `false` disables the service."""
    value = config.get(key, {})
    if value is False:
        return None
    if value is None or value == {}:
        return {}
    if isinstance(value, str):  # `stt: 192.168.1.5`
        return {"host": value}
    if not isinstance(value, dict):
        _LOGGER.warning("voice: %s: must be a mapping, got %r", key, type(value).__name__)
        return {}
    return dict(value)


def _build_clients(jarvis: "Jarvis", config: dict[str, Any]) -> tuple[Any, Any, Any]:
    host = str(config.get("host") or DEFAULT_HOST)

    stt = jarvis.data.get(DATA_STT_CLIENT)
    if stt is None:
        section = _section(config, "stt")
        if section is not None:
            stt = WyomingSttClient(
                str(section.get("host") or host),
                int(section.get("port") or DEFAULT_STT_PORT),
                float(section.get("timeout") or 60.0),
                language=section.get("language"),
                model=section.get("model"),
            )

    tts = jarvis.data.get(DATA_TTS_CLIENT)
    if tts is None:
        section = _section(config, "tts")
        if section is not None:
            tts = WyomingTtsClient(
                str(section.get("host") or host),
                int(section.get("port") or DEFAULT_TTS_PORT),
                float(section.get("timeout") or 60.0),
                voice=section.get("voice"),
                speaker=section.get("speaker"),
            )

    wake = jarvis.data.get(DATA_WAKE_CLIENT)
    if wake is None:
        section = _section(config, "wake")
        if section is not None:
            wake = WyomingWakeClient(
                str(section.get("host") or host),
                int(section.get("port") or DEFAULT_WAKE_PORT),
                float(section.get("timeout") or 30.0),
                model=str(section.get("model") or section.get("wake_word") or DEFAULT_WAKE_WORD),
            )

    return stt, tts, wake


def _entity_ids(target: Any) -> list[str]:
    """Normalise a service target to a list of entity ids.

    ``entity_id: media_player.a, media_player.b`` is the usual YAML shorthand
    for two targets; treating it as one id sends play_media a name that
    matches nothing.
    """
    if target is None:
        return []
    if isinstance(target, str):
        candidates: list[str] = target.split(",")
    elif isinstance(target, (list, tuple, set)):
        candidates = []
        for item in target:
            candidates.extend(str(item).split(","))
    else:
        candidates = [str(target)]
    return [item.strip() for item in candidates if item and item.strip()]


def _register_services(jarvis: "Jarvis", data: VoiceData) -> None:
    async def _say(call: "ServiceCall") -> dict[str, Any]:
        text = call.get("text") or call.get("message")
        if not text:
            raise ValueError("voice.say requires 'text'")
        result = await async_say(
            jarvis,
            str(text),
            voice=call.get("voice"),
            language=call.get("language"),
        )
        entity_ids = _entity_ids(call.get("entity_id") or call.get("media_player"))
        result["entity_id"] = entity_ids
        # A caller that asked for playback needs to know it did not happen —
        # logging alone would report success for a dead speaker.
        failed: dict[str, str] = {}
        for entity_id in entity_ids:
            if not jarvis.services.has_service("media_player", "play_media"):
                _LOGGER.warning("voice.say: media_player.play_media is not available")
                for pending in entity_ids:
                    failed.setdefault(pending, "media_player.play_media is not available")
                break
            try:
                await jarvis.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": entity_id,
                        "media_type": "music",
                        "media_id": result["url"],
                    },
                    blocking=True,
                    context=call.context,
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.exception("voice.say: could not play on %s", entity_id)
                failed[entity_id] = str(err) or type(err).__name__
        result["failed"] = failed
        result["played"] = [eid for eid in entity_ids if eid not in failed]
        jarvis.bus.fire(EVENT_VOICE_SAID, dict(result), call.context)
        return result

    async def _get_pipelines(call: "ServiceCall") -> dict[str, Any]:
        return data.pipelines.as_dict()

    jarvis.services.register(
        DOMAIN,
        SERVICE_SAY,
        _say,
        description="Speak text with the Wyoming TTS voice and cache the audio.",
        fields={
            "text": {"required": True, "example": "The garage door is still open."},
            "entity_id": {"example": "media_player.kitchen"},
            "voice": {"example": "en_GB-alan-medium"},
            "language": {"example": "en"},
        },
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_GET_PIPELINES,
        _get_pipelines,
        description="List configured voice pipelines and the preferred one.",
        supports_response=True,
    )


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if config is None:
        config = {}
    if not isinstance(config, dict):
        _LOGGER.error("voice: config must be a mapping, got %r", type(config).__name__)
        return False

    stt, tts, wake = _build_clients(jarvis, config)

    language = str(config.get("language") or DEFAULT_LANGUAGE)
    tts_section = _section(config, "tts") or {}
    wake_section = _section(config, "wake") or {}
    tts_voice = tts_section.get("voice") or config.get("voice")
    wake_word = wake_section.get("model") or wake_section.get("wake_word") or DEFAULT_WAKE_WORD

    pipelines = PipelineStore(jarvis)
    await pipelines.async_load()
    await pipelines.async_load_config(
        config.get("pipelines"),
        defaults={
            "language": language,
            "tts_voice": tts_voice,
            "wake_word": wake_word,
        },
    )

    data = VoiceData(
        jarvis=jarvis,
        pipelines=pipelines,
        stt=stt,
        tts=tts,
        wake=wake,
        config=config,
        language=language,
        tts_voice=tts_voice,
        wake_word=wake_word,
    )
    jarvis.data[DATA_VOICE] = data
    jarvis.data.setdefault(DATA_TTS_CACHE, {})

    _register_services(jarvis, data)

    _LOGGER.info(
        "Voice ready: stt=%s tts=%s wake=%s, %d pipeline(s), preferred %r",
        _describe(stt),
        _describe(tts),
        _describe(wake),
        len(pipelines.list()),
        pipelines.preferred.name,
    )
    return True


def _describe(client: Any) -> str:
    if client is None:
        return "disabled"
    host = getattr(client, "host", None)
    port = getattr(client, "port", None)
    if host and port:
        return f"{host}:{port}"
    return type(client).__name__
