"""Named voice pipelines — the user-facing configuration of the voice stack.

A pipeline says which STT engine listens, which agent thinks, which TTS voice
answers and which wake word starts the whole thing. They come from YAML and/or
the UI, are persisted through :class:`jarvis.store.Store`, and there is always
at least one: the default pipeline named "Jarvis".
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ..state import slugify
from ..store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "voice_pipelines"
DEFAULT_PIPELINE_NAME = "Jarvis"
DEFAULT_LANGUAGE = "en"
DEFAULT_STT_ENGINE = "wyoming"
DEFAULT_TTS_ENGINE = "wyoming"
DEFAULT_WAKE_ENGINE = "wyoming"
DEFAULT_CONVERSATION_ENGINE = "ollama"
DEFAULT_WAKE_WORD = "hey_jarvis"

__all__ = ["Pipeline", "PipelineStore", "DEFAULT_PIPELINE_NAME"]

# YAML/UI aliases -> field names.
_ALIASES = {
    "stt": "stt_engine",
    "stt_provider": "stt_engine",
    "tts": "tts_engine",
    "tts_provider": "tts_engine",
    "voice": "tts_voice",
    "wake": "wake_engine",
    "wake_word_engine": "wake_engine",
    "wake_word_id": "wake_word",
    "wake_word_model": "wake_word",
    "conversation_agent": "conversation_engine",
    "agent": "conversation_engine",
}


@dataclass
class Pipeline:
    """One named pipeline configuration."""

    id: str
    name: str
    language: str = DEFAULT_LANGUAGE
    stt_engine: str = DEFAULT_STT_ENGINE
    stt_language: str | None = None
    tts_engine: str = DEFAULT_TTS_ENGINE
    tts_voice: str | None = None
    tts_language: str | None = None
    wake_engine: str = DEFAULT_WAKE_ENGINE
    wake_word: str | None = DEFAULT_WAKE_WORD
    conversation_engine: str = DEFAULT_CONVERSATION_ENGINE
    conversation_language: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], pipeline_id: str | None = None) -> "Pipeline":
        normalised: dict[str, Any] = {}
        extra: dict[str, Any] = dict(data.get("extra") or {})
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        for raw_key, value in data.items():
            if raw_key == "extra":
                continue
            key = _ALIASES.get(raw_key, raw_key)
            if key in known:
                normalised[key] = value
            else:
                extra[raw_key] = value
        name = str(normalised.get("name") or DEFAULT_PIPELINE_NAME)
        normalised["name"] = name
        normalised["id"] = str(pipeline_id or normalised.get("id") or slugify(name))
        for key in ("stt_engine", "tts_engine", "wake_engine", "conversation_engine", "language"):
            if normalised.get(key) is None:
                normalised.pop(key, None)
        return cls(extra=extra, **normalised)

    def merged_with(self, data: dict[str, Any]) -> "Pipeline":
        """A copy with `data` applied on top (aliases honoured).

        An explicit ``extra`` mapping in `data` is merged into the existing
        one — dropping it would make ``PipelineStore.async_update`` silently
        ignore extra-field edits.
        """
        merged = self.as_dict()
        extra = dict(merged.get("extra") or {})
        incoming_extra = data.get("extra")
        if isinstance(incoming_extra, dict):
            extra.update(incoming_extra)
        merged.update({k: v for k, v in data.items() if k != "extra"})
        merged["extra"] = extra
        return Pipeline.from_dict(merged, pipeline_id=self.id)


class PipelineStore:
    """Holds every configured pipeline and remembers the preferred one."""

    def __init__(self, jarvis: "Jarvis | None" = None, store: Store | None = None) -> None:
        self.jarvis = jarvis
        if store is None and jarvis is not None:
            store = Store(jarvis.config_dir, STORAGE_KEY)
        self._store = store
        self._pipelines: dict[str, Pipeline] = {}
        self._preferred: str | None = None

    # --- reads ------------------------------------------------------------
    def list(self) -> list[Pipeline]:
        return list(self._pipelines.values())

    def get(self, pipeline_id: str | None) -> Pipeline | None:
        if not pipeline_id:
            return None
        return self._pipelines.get(pipeline_id)

    def get_by_name(self, name: str) -> Pipeline | None:
        if not name:
            return None
        wanted = name.strip().casefold()
        for pipeline in self._pipelines.values():
            if pipeline.name.casefold() == wanted or pipeline.id.casefold() == wanted:
                return pipeline
        return None

    def resolve(self, pipeline_id: str | None = None) -> Pipeline:
        """Best match for an id/name, falling back to the preferred pipeline."""
        if pipeline_id:
            found = self.get(pipeline_id) or self.get_by_name(pipeline_id)
            if found is not None:
                return found
        return self.preferred

    @property
    def preferred(self) -> Pipeline:
        pipeline = self._pipelines.get(self._preferred or "")
        if pipeline is not None:
            return pipeline
        if self._pipelines:
            return next(iter(self._pipelines.values()))
        return self._ensure_default()

    @property
    def preferred_id(self) -> str:
        return self.preferred.id

    def as_dict(self) -> dict[str, Any]:
        return {
            "pipelines": [pipeline.as_dict() for pipeline in self._pipelines.values()],
            "preferred_pipeline": self.preferred.id if self._pipelines else None,
        }

    # --- writes -----------------------------------------------------------
    def _unique_id(self, base: str) -> str:
        candidate = slugify(base) or uuid.uuid4().hex[:8]
        if candidate not in self._pipelines:
            return candidate
        for suffix in range(2, 100):
            attempt = f"{candidate}_{suffix}"
            if attempt not in self._pipelines:
                return attempt
        return uuid.uuid4().hex[:8]  # pragma: no cover - 98 pipelines named the same

    def add(self, pipeline: Pipeline, preferred: bool = False) -> Pipeline:
        self._pipelines[pipeline.id] = pipeline
        if preferred or self._preferred is None:
            self._preferred = pipeline.id
        return pipeline

    async def async_create(self, data: dict[str, Any], preferred: bool = False) -> Pipeline:
        payload = dict(data)
        name = str(payload.get("name") or DEFAULT_PIPELINE_NAME)
        payload["name"] = name
        payload["id"] = payload.get("id") or self._unique_id(name)
        pipeline = Pipeline.from_dict(payload)
        self.add(pipeline, preferred)
        await self.async_save()
        return pipeline

    async def async_update(self, pipeline_id: str, data: dict[str, Any]) -> Pipeline | None:
        existing = self.get(pipeline_id)
        if existing is None:
            return None
        updated = existing.merged_with(data)
        self._pipelines[updated.id] = updated
        await self.async_save()
        return updated

    async def async_delete(self, pipeline_id: str) -> bool:
        if pipeline_id not in self._pipelines:
            return False
        if len(self._pipelines) == 1:
            _LOGGER.warning("Refusing to delete the last voice pipeline")
            return False
        del self._pipelines[pipeline_id]
        if self._preferred == pipeline_id:
            self._preferred = next(iter(self._pipelines))
        await self.async_save()
        return True

    async def async_set_preferred(self, pipeline_id: str) -> bool:
        if pipeline_id not in self._pipelines:
            return False
        self._preferred = pipeline_id
        await self.async_save()
        return True

    def _ensure_default(self, defaults: dict[str, Any] | None = None) -> Pipeline:
        existing = self.get_by_name(DEFAULT_PIPELINE_NAME)
        if existing is not None:
            return existing
        payload: dict[str, Any] = {"name": DEFAULT_PIPELINE_NAME}
        payload.update(defaults or {})
        payload["name"] = DEFAULT_PIPELINE_NAME
        payload["id"] = payload.get("id") or slugify(DEFAULT_PIPELINE_NAME)
        return self.add(Pipeline.from_dict(payload), preferred=not self._pipelines)

    # --- persistence ------------------------------------------------------
    async def async_load(self) -> "PipelineStore":
        data = await self._store.load() if self._store is not None else None
        if isinstance(data, dict):
            for raw in data.get("pipelines") or []:
                if not isinstance(raw, dict):
                    continue
                pipeline = Pipeline.from_dict(raw)
                self._pipelines[pipeline.id] = pipeline
            preferred = data.get("preferred_pipeline")
            if isinstance(preferred, str) and preferred in self._pipelines:
                self._preferred = preferred
        if self._preferred is None and self._pipelines:
            self._preferred = next(iter(self._pipelines))
        return self

    async def async_save(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.save(self.as_dict())
        except OSError:  # pragma: no cover - disk trouble should not kill voice
            _LOGGER.warning("Could not persist voice pipelines", exc_info=True)

    async def async_load_config(
        self,
        configured: Any = None,
        defaults: dict[str, Any] | None = None,
    ) -> "PipelineStore":
        """Merge the `voice: pipelines:` YAML block, then guarantee a default."""
        defaults = {k: v for k, v in (defaults or {}).items() if v is not None}
        entries: list[dict[str, Any]] = []
        if isinstance(configured, dict):
            # mapping form: {jarvis: {...}, guest: {...}}
            for key, value in configured.items():
                item = dict(value or {})
                item.setdefault("name", str(key).replace("_", " ").title())
                item.setdefault("id", slugify(str(key)))
                entries.append(item)
        elif isinstance(configured, list):
            for value in configured:
                if isinstance(value, dict):
                    entries.append(dict(value))
                elif isinstance(value, str):
                    entries.append({"name": value})
                else:
                    _LOGGER.warning("voice: ignoring pipeline entry %r", value)
        if not entries:
            # No YAML pipelines: the default one still picks up the engine defaults.
            entries = [{"name": DEFAULT_PIPELINE_NAME}]

        for entry in entries:
            merged = dict(defaults or {})
            merged.update(entry)
            name = str(merged.get("name") or DEFAULT_PIPELINE_NAME)
            merged["name"] = name
            existing = self.get(str(merged.get("id") or "")) or self.get_by_name(name)
            if existing is not None:
                self._pipelines[existing.id] = existing.merged_with(merged)
            else:
                merged["id"] = merged.get("id") or self._unique_id(name)
                self.add(Pipeline.from_dict(merged), preferred=not self._pipelines)

        self._ensure_default(defaults)
        if self._preferred is None and self._pipelines:
            self._preferred = next(iter(self._pipelines))
        await self.async_save()
        return self
