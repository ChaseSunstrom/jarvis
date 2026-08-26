"""Settings a human may change from the console, and the overlay that holds them.

The configuration on disk stays the source of truth. This is a sparse overlay
merged over it at load: `{"llm.model": "qwen3:14b"}` in
`.storage/settings.json`, applied to the parsed YAML before any integration is
built.

**Why not rewrite configuration.yaml.** The parser resolves `!secret`,
`!env_var` and `!include*` on the way in and keeps no record of them, so a
round-trip writer would inline resolved secrets into a file dense with
load-bearing comments and destroy both. The overlay leaves the file exactly as
the user wrote it, which also means "reset" is a delete rather than an edit.

**Why an allowlist.** `SETTINGS` is a hardcoded tuple, checked by set
membership, mirroring `ENTITY_UPDATE_FIELDS` in `api/common.py`. A settings API
that merged arbitrary paths into the config would be a way to set
`jarvis.cors_allowed_origins`, or `llm.expose`, or anything else the safety
model reads — a config write is a privilege escalation unless the set of
writable keys is fixed in code.

**Why `apply` never raises.** It runs inside `Jarvis.async_setup`, before the
API exists. An overlay entry whose parent has since been deleted from the YAML
— someone comments out the body of `voice:` — must produce a dropped entry and
a note, not an exception. The alternative is a box that will not boot with no
surface left to fix it from.

Each spec says whether its key takes effect live or needs a restart, and the
console shows that verbatim. Saying "live" about something that is read once at
construction is the specific lie this table exists to avoid.
"""

from __future__ import annotations

import asyncio

import copy
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .store import Store

if TYPE_CHECKING:  # pragma: no cover
    from .core import Jarvis

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "settings"
STORAGE_VERSION = 1

#: How a setting takes effect.
APPLY_LIVE = "live"  # the next read sees it
APPLY_RESTART = "restart"  # baked into something built at setup
APPLY_SPLIT = "split"  # live for one consumer, stale for another until restart


class SettingsError(Exception):
    """A write was refused. Carries the sentence to show the user."""


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """One editable key: where it lives, what it accepts, how it lands."""

    key: str
    path: tuple[str, ...]
    label: str
    group: str
    type: str  # 'string' | 'number' | 'integer' | 'boolean' | 'choice'
    apply: str = APPLY_LIVE
    note: str = ""
    #: Returns the coerced value, or raises SettingsError with a sentence.
    validate: Callable[[Any], Any] | None = None
    #: Push the new value into whatever already holds a copy. Returns False when
    #: the target is not there, which is not an error — the integration may
    #: simply not be configured.
    apply_hook: Callable[["Jarvis", Any], bool] | None = None
    #: Offer the console a list to choose from, when one can be discovered.
    choices_hook: Callable[["Jarvis"], list[str]] | None = None


@dataclass(slots=True)
class Unapplied:
    """An overlay entry that did not land, and why. Rendered in the console."""

    key: str
    value: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "reason": self.reason}


# --- validators -------------------------------------------------------------
def _text(minimum: int = 1, maximum: int = 200) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        text = str(value if value is not None else "").strip()
        if len(text) < minimum:
            raise SettingsError("This cannot be empty.")
        if len(text) > maximum:
            raise SettingsError(f"Keep this under {maximum} characters.")
        # A newline in a name ends up in a log line and in a prompt.
        if any(ch.isprintable() is False for ch in text):
            raise SettingsError("This cannot contain control characters.")
        return text

    return check


def _optional_text(maximum: int = 200) -> Callable[[Any], str]:
    """`_text`, with empty allowed — for a setting whose empty means "the default"."""
    check_filled = _text(1, maximum)

    def check(value: Any) -> str:
        text = str(value if value is not None else "").strip()
        return check_filled(text) if text else ""

    return check


def _number(low: float, high: float, integer: bool = False) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        try:
            number = int(value) if integer else float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"Expected a {'whole ' if integer else ''}number.") from None
        if not low <= number <= high:
            raise SettingsError(f"Must be between {low} and {high}.")
        return number

    return check


def _bool(value: Any) -> bool:
    """A switch. Accepts what a form, a yaml file or a model sends for one —
    true/false, yes/no, on/off, 1/0 — and refuses the rest, so "maybe" cannot
    land in a config file as a truthy string."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "yes", "on", "1", "enabled"):
        return True
    if text in ("false", "no", "off", "0", "disabled"):
        return False
    raise SettingsError("Expected on or off.")


def _one_of(*allowed: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text not in allowed:
            raise SettingsError(f"Must be one of: {', '.join(allowed)}.")
        return text

    return check


def _time_zone(value: Any) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # noqa: PLC0415

    name = str(value or "").strip()
    if not name:
        raise SettingsError("A timezone is required.")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise SettingsError(
            f"{name!r} is not a timezone this system knows. Use an IANA name "
            "like Europe/London or America/New_York."
        ) from None
    return name


# --- apply hooks ------------------------------------------------------------
# Every hook resolves its target lazily through `jarvis.data`. Importing an
# integration from here would make core depend on an optional one, and create
# an import cycle on the way.
def _llm_agent(jarvis: "Jarvis") -> Any:
    return jarvis.data.get("llm")


def _apply_model(jarvis: "Jarvis", value: Any) -> bool:
    agent = _llm_agent(jarvis)
    if agent is None:
        return False
    agent.model = value
    # The client keeps its own default, and `chat()` falls back to it whenever a
    # caller does not pass one. Setting only the agent leaves half the calls on
    # the old model, which looks like the setting working intermittently.
    client = getattr(agent, "client", None)
    if client is not None:
        client.model = value
    return True


def _apply_agent_attr(name: str) -> Callable[["Jarvis", Any], bool]:
    def hook(jarvis: "Jarvis", value: Any) -> bool:
        agent = _llm_agent(jarvis)
        if agent is None:
            return False
        setattr(agent, name, value)
        return True

    return hook


def _apply_vision_model(jarvis: "Jarvis", value: Any) -> bool:
    """Point the running vision analyser at another model.

    `VisionConfig` is frozen and the analyser holds it whole, so this replaces
    the record rather than poking a field — and sets the client's default too,
    for the same reason `_apply_model` does: the call names `cfg.model`, the
    fallback names the client's, and one of them stale is a setting that
    works on alternate frames.
    """
    import dataclasses

    store = jarvis.data.get("vision")
    manager = store.get("manager") if isinstance(store, dict) else None
    analyser = getattr(manager, "model", None)
    config = getattr(analyser, "config", None)
    if analyser is None or config is None:
        return False
    analyser.config = dataclasses.replace(config, model=value)
    client = getattr(analyser, "ollama", None)
    if client is not None:
        client.model = value
    return True


def _apply_agent_option(name: str) -> Callable[["Jarvis", Any], bool]:
    def hook(jarvis: "Jarvis", value: Any) -> bool:
        agent = _llm_agent(jarvis)
        if agent is None:
            return False
        agent.options = {**getattr(agent, "options", {}), name: value}
        return True

    return hook


def _apply_voice_attr(name: str) -> Callable[["Jarvis", Any], bool]:
    def hook(jarvis: "Jarvis", value: Any) -> bool:
        voice = jarvis.data.get("voice")
        if voice is None:
            return False
        setattr(voice, name, value)
        return True

    return hook


def _apply_log_level(jarvis: "Jarvis", value: Any) -> bool:
    logging.getLogger().setLevel(str(value).upper())
    return True


def _apply_approval_ttl(jarvis: "Jarvis", value: Any) -> bool:
    registry = jarvis.data.get("llm_tools")
    if registry is None:
        return False
    registry.approval_ttl = value
    return True


def _apply_demo_enabled(jarvis: "Jarvis", value: Any) -> bool:
    """Demo mode, live (M80): off removes every demo entity through the one
    delete path; on builds the fixture house again. No restart — the operator
    asked why the fake lamps were still there, and "after a restart" is not
    an answer to that."""
    from .integrations import demo as demo_integration

    async def apply() -> None:
        if value:
            await demo_integration.async_setup(jarvis, {"enabled": True})
        else:
            await demo_integration.async_remove_all(jarvis)

    jarvis.async_create_task(apply()) if hasattr(jarvis, "async_create_task") else asyncio.ensure_future(apply())
    return True


def _apply_question_ttl(jarvis: "Jarvis", value: Any) -> bool:
    registry = jarvis.data.get("llm_tools")
    if registry is None:
        return False
    registry.question_ttl = value
    return True


def _model_choices(jarvis: "Jarvis") -> list[str]:
    """Whatever Ollama says it has, when it is answering."""
    agent = _llm_agent(jarvis)
    client = getattr(agent, "client", None) if agent else None
    names = getattr(client, "known_models", None)
    return sorted(names) if isinstance(names, (list, set, tuple)) else []


def _voice_catalogue(jarvis: "Jarvis", key: str) -> list[str]:
    """What the Wyoming services said they serve, from their own `describe`.

    Empty when they have not been asked yet or are down, and empty is the
    honest answer: the console falls back to a text box rather than offering a
    list that might be wrong.
    """
    voice = jarvis.data.get("voice")
    catalogue = getattr(voice, "catalogue", None)
    if not isinstance(catalogue, dict):
        return []
    values = catalogue.get(key)
    return list(values) if isinstance(values, list) else []


def _time_zone_choices(jarvis: "Jarvis") -> list[str]:
    """Every IANA zone this Python knows about.

    A long list, and a dropdown of it is still strictly better than a text box:
    the failure mode of the text box is a typo that makes every time trigger
    fire at the wrong hour, silently, until someone notices the lights coming
    on at four in the morning.
    """
    try:
        from zoneinfo import available_timezones

        return sorted(available_timezones())
    except Exception:  # pragma: no cover - no tzdata on this box
        return []


#: ISO 4217, the ones a home assistant is plausibly priced in. Not the full
#: list of 180: this is a dropdown, and a dropdown you have to search is worse
#: than the text box it replaced.
_CURRENCIES = (
    "AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD",
    "HUF", "ILS", "INR", "JPY", "KRW", "MXN", "NOK", "NZD", "PLN", "RON",
    "SEK", "SGD", "THB", "TRY", "TWD", "USD", "ZAR",
)

#: ISO 3166-1 alpha-2, same reasoning. Used for holiday calendars and units.
_COUNTRIES = (
    "AT", "AU", "BE", "BR", "CA", "CH", "CN", "CZ", "DE", "DK", "ES", "FI",
    "FR", "GB", "HK", "HU", "IE", "IL", "IN", "IT", "JP", "KR", "MX", "NL",
    "NO", "NZ", "PL", "PT", "RO", "SE", "SG", "TH", "TR", "TW", "US", "ZA",
)

#: The languages the voice stack has models for in practice. `language` is used
#: by STT, TTS and the assistant's replies.
_LANGUAGES = (
    "ar", "ca", "cs", "da", "de", "el", "en", "es", "fa", "fi", "fr", "hi",
    "hu", "is", "it", "ja", "ka", "kk", "ko", "lb", "lv", "nl", "no", "pl",
    "pt", "ro", "ru", "sk", "sl", "sr", "sv", "sw", "tr", "uk", "vi", "zh",
)


# --- the allowlist ----------------------------------------------------------
SETTINGS: tuple[SettingSpec, ...] = (
    # --- assistant ---------------------------------------------------------
    SettingSpec(
        key="llm.model",
        path=("llm", "model"),
        label="Model",
        group="Assistant",
        type="choice",
        apply=APPLY_LIVE,
        note="The model every conversation runs on, as the server at LLM_URL "
        "names it. Behind the gateway that is an alias (`house`); the MODELS "
        "panel says which served model it stands for.",
        validate=_text(1, 120),
        apply_hook=_apply_model,
        choices_hook=_model_choices,
    ),
    SettingSpec(
        key="llm.fast_model",
        path=("llm", "fast_model"),
        label="Fast model",
        group="Assistant",
        type="choice",
        apply=APPLY_LIVE,
        note="A smaller model for the voice path, named as LLM_URL names it. "
        "Recorded on the running agent, and read by nothing yet: the fast "
        "path lands with M60, and this is where it will look. Empty means "
        "the conversation model.",
        validate=_optional_text(120),
        apply_hook=_apply_agent_attr("fast_model"),
        choices_hook=_model_choices,
    ),
    SettingSpec(
        key="vision.model",
        path=("vision", "model"),
        label="Vision model",
        group="Assistant",
        type="string",
        apply=APPLY_LIVE,
        note="The model that looks at a camera frame, named as the vision "
        "integration's own server names it. Only in effect when `vision:` is "
        "configured.",
        validate=_text(1, 120),
        apply_hook=_apply_vision_model,
    ),
    SettingSpec(
        key="llm.options.temperature",
        path=("llm", "options", "temperature"),
        label="Temperature",
        group="Assistant",
        type="number",
        note="Higher is more inventive. 0.7 is the usual place to start.",
        validate=_number(0.0, 2.0),
        apply_hook=_apply_agent_option("temperature"),
    ),
    SettingSpec(
        key="llm.options.num_ctx",
        path=("llm", "options", "num_ctx"),
        label="Context window",
        group="Assistant",
        type="integer",
        note="Tokens of history the model is given. Costs memory on the GPU.",
        validate=_number(256, 131072, integer=True),
        apply_hook=_apply_agent_option("num_ctx"),
    ),
    SettingSpec(
        key="llm.max_tool_rounds",
        path=("llm", "max_tool_rounds"),
        label="Tool rounds",
        group="Assistant",
        type="integer",
        note="How many times one turn may call tools before it must answer.",
        validate=_number(1, 20, integer=True),
        apply_hook=_apply_agent_attr("max_tool_rounds"),
    ),
    SettingSpec(
        key="llm.approval_ttl",
        path=("llm", "approval_ttl"),
        label="Approval expiry",
        group="Assistant",
        type="number",
        note="Seconds a Tier-3 request waits for a human before it lapses.",
        validate=_number(30, 3600),
        apply_hook=_apply_approval_ttl,
    ),
    SettingSpec(
        key="demo.enabled",
        path=("demo", "enabled"),
        label="Demo mode",
        group="House",
        type="boolean",
        note="The fixture house — fake lights, a lock, a garage door, sensors, a vacuum — for "
        "trying Jarvis with no hardware. Off removes them at once; a real house wants it off.",
        validate=_bool,
        apply_hook=_apply_demo_enabled,
    ),
    SettingSpec(
        key="llm.address",
        path=("llm", "address"),
        label="Form of address",
        group="Assistant",
        type="string",
        note="What Jarvis calls you — Sir, Ma'am, a name — whoever is speaking. "
        "\"none\" for no title at all.",
        validate=_text(1, 40),
        apply_hook=_apply_agent_attr("address"),
    ),
    SettingSpec(
        key="llm.question_ttl",
        path=("llm", "question_ttl"),
        label="Question expiry",
        group="Assistant",
        type="number",
        # Its own clock (M66): a question waits on a fact the person may have
        # to walk to the console for; an approval waits on a yes. The
        # operator's held question was answered after five minutes and told
        # "expired" — this is the number that decides that.
        note="Seconds a question the assistant asks waits for an answer before it "
        "lapses. Longer than approval expiry: a person may be away from the console.",
        validate=_number(30, 7200),
        apply_hook=_apply_question_ttl,
    ),
    SettingSpec(
        key="llm.timeout",
        path=("llm", "timeout"),
        label="Model timeout",
        group="Assistant",
        type="number",
        apply=APPLY_RESTART,
        note="Baked into the shared HTTP client when it is built, so this one "
        "needs a restart.",
        validate=_number(5, 600),
    ),
    # --- house -------------------------------------------------------------
    SettingSpec(
        key="jarvis.name",
        path=("jarvis", "name"),
        label="Name",
        group="House",
        type="string",
        note="What this instance calls itself.",
        validate=_text(1, 60),
    ),
    SettingSpec(
        key="jarvis.time_zone",
        path=("jarvis", "time_zone"),
        label="Timezone",
        group="House",
        type="choice",
        apply=APPLY_LIVE,
        note="An IANA name. Decides when `at: \"07:00:00\"` fires and what "
        "{{ now() }} reads.",
        validate=_time_zone,
        choices_hook=_time_zone_choices,
    ),
    SettingSpec(
        key="jarvis.unit_system",
        path=("jarvis", "unit_system"),
        label="Units",
        group="House",
        type="choice",
        note="Metric or imperial: how temperatures and distances are shown and spoken.",
        validate=_one_of("metric", "imperial"),
        choices_hook=lambda jarvis: ["metric", "imperial"],
    ),
    SettingSpec(
        key="jarvis.currency",
        path=("jarvis", "currency"),
        label="Currency",
        group="House",
        type="choice",
        note="ISO 4217 code (GBP, EUR, USD) for anything priced.",
        validate=_text(3, 3),
        choices_hook=lambda jarvis: list(_CURRENCIES),
    ),
    SettingSpec(
        key="jarvis.country",
        path=("jarvis", "country"),
        label="Country",
        group="House",
        type="choice",
        note="ISO 3166 code (GB, US, DE): holiday calendars and regional defaults.",
        validate=_text(2, 2),
        choices_hook=lambda jarvis: list(_COUNTRIES),
    ),
    SettingSpec(
        key="jarvis.language",
        path=("jarvis", "language"),
        label="Language",
        group="House",
        type="choice",
        note="The language the assistant replies in, as a two-letter code.",
        validate=_text(2, 10),
        choices_hook=lambda jarvis: list(_LANGUAGES),
    ),
    SettingSpec(
        key="jarvis.latitude",
        path=("jarvis", "latitude"),
        label="Latitude",
        group="House",
        type="number",
        apply=APPLY_SPLIT,
        note="Live for presence. The sun integration snapshots it at startup, "
        "so sunrise and sunset follow on the next restart.",
        validate=_number(-90, 90),
    ),
    SettingSpec(
        key="jarvis.longitude",
        path=("jarvis", "longitude"),
        label="Longitude",
        group="House",
        type="number",
        apply=APPLY_SPLIT,
        note="Live for presence. The sun integration snapshots it at startup, "
        "so sunrise and sunset follow on the next restart.",
        validate=_number(-180, 180),
    ),
    SettingSpec(
        key="jarvis.elevation",
        path=("jarvis", "elevation"),
        label="Elevation",
        group="House",
        type="number",
        apply=APPLY_RESTART,
        note="Metres above sea level. Read once by the sun integration.",
        validate=_number(-500, 9000),
    ),
    SettingSpec(
        key="jarvis.log_level",
        path=("jarvis", "log_level"),
        label="Log level",
        group="House",
        type="choice",
        note="How much Jarvis writes to its log: debug for everything, error for only what broke.",
        validate=_one_of("debug", "info", "warning", "error"),
        apply_hook=_apply_log_level,
        choices_hook=lambda jarvis: ["debug", "info", "warning", "error"],
    ),
    # --- voice -------------------------------------------------------------
    SettingSpec(
        key="voice.language",
        path=("voice", "language"),
        label="Speech language",
        group="Voice",
        type="choice",
        note="The language speech is recognised and spoken in, as a two-letter code.",
        validate=_text(2, 10),
        apply_hook=_apply_voice_attr("language"),
        choices_hook=lambda jarvis: list(_LANGUAGES),
    ),
    SettingSpec(
        key="voice.tts_voice",
        path=("voice", "tts_voice"),
        label="Voice",
        group="Voice",
        type="choice",
        note="The voices Piper is actually serving, from its own `describe`. "
        "Naming one it was not started with makes the first reply a download — "
        "and a failure on a box with no internet.",
        validate=_text(1, 80),
        apply_hook=_apply_voice_attr("tts_voice"),
        choices_hook=lambda jarvis: _voice_catalogue(jarvis, "tts_voices"),
    ),
    SettingSpec(
        key="voice.tts.length_scale",
        path=("voice", "tts", "length_scale"),
        label="Pace (Piper length scale)",
        group="Voice",
        type="number",
        # Restart, and not Jarvis's: Piper takes its length scale at START,
        # from PIPER_LENGTH_SCALE in .env, which docker-compose.yml hands to
        # the wyoming-piper container. The configured value is read here so
        # the screen can say what the house speaks at; the note says the one
        # thing that makes the number true, or the row would promise a change
        # the next reply does not make (the operator's 26 Aug 2026 ask).
        apply=APPLY_RESTART,
        note="A duration multiplier: 1.0 is the voice's own pace, 0.9 a tenth "
        "quicker. Piper takes it at start — set PIPER_LENGTH_SCALE in .env to "
        "the same number and restart wyoming-piper; with a Kokoro engine use "
        "`speed:` instead.",
        validate=_number(0.5, 1.5),
    ),
    SettingSpec(
        key="voice.wake_word",
        path=("voice", "wake_word"),
        label="Wake word",
        group="Voice",
        type="choice",
        note="The models openWakeWord is actually serving. A name it does not "
        "have means your name stops working, with nothing to say so.",
        validate=_text(1, 60),
        apply_hook=_apply_voice_attr("wake_word"),
        choices_hook=lambda jarvis: _voice_catalogue(jarvis, "wake_words"),
    ),
)

SETTINGS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}


def spec_for(key: str) -> SettingSpec:
    """The spec for `key`, or a refusal naming it.

    Set membership, not a prefix match: `llm.model` is editable and
    `llm.expose` — the list of entities the assistant may see at all — is not,
    and the difference between them must not be a string operation.
    """
    spec = SETTINGS_BY_KEY.get(key)
    if spec is None:
        raise SettingsError(f"{key!r} is not an editable setting.")
    return spec


def _words(text: str) -> str:
    """Lower-cased, with the separators a person or a model might use folded
    to spaces, so `voice.tts_voice`, "TTS voice" and "tts-voice" compare equal."""
    return " ".join(text.replace(".", " ").replace("_", " ").replace("-", " ").lower().split())


def matching_settings(name: Any) -> list[SettingSpec]:
    """Every spec `name` could mean, in registry order.

    The exact key alone when it is one. Otherwise the specs whose label or
    last path segment is the name — "temperature" is `llm.options.temperature`,
    "wake word" is `voice.wake_word` — because that is how a person asks for a
    setting and a model repeats it. Never a prefix or substring match: "mod"
    is not a setting, and a match that loose would let a model change a
    setting nobody named.

    Still membership in `SETTINGS`, never a path the caller composed: a name
    that matches nothing is not an editable setting, whatever the config file
    contains under it.
    """
    text = str(name if name is not None else "").strip()
    if not text:
        return []
    spec = SETTINGS_BY_KEY.get(text)
    if spec is not None:
        return [spec]
    wanted = _words(text)
    if not wanted:
        return []
    return [
        candidate
        for candidate in SETTINGS
        if wanted in (_words(candidate.label), _words(candidate.path[-1]), _words(candidate.key))
    ]


def resolve_setting(name: Any) -> SettingSpec | None:
    """The ONE spec `name` means, or None.

    None for nothing and None for more than one: "model" names three settings
    (`llm.model`, `llm.fast_model`, `vision.model`), and picking one of them
    silently would change a setting nobody asked about. A caller that wants
    to say which ones asks `matching_settings`.
    """
    matches = matching_settings(name)
    return matches[0] if len(matches) == 1 else None


def nearest_settings(name: Any, limit: int = 5) -> list[str]:
    """The keys closest to `name`, best first, for a refusal to name.

    "There is no setting called demo mode" is a dead end; "…the nearest are
    llm.model, voice.wake_word" is something the next sentence can use. Scored
    on words shared with the key, the label and the note, then on string
    similarity to the key and the label, so "think" finds a note that mentions
    thinking before a key that happens to share three letters. Deterministic —
    ties fall back to registry order — because the sentence is repeated to a
    person and must not change between two calls.
    """
    import difflib

    wanted = _words(str(name if name is not None else ""))
    if not wanted:
        return [spec.key for spec in SETTINGS[:limit]]
    wanted_words = set(wanted.split())
    scored: list[tuple[float, int, str]] = []
    for index, spec in enumerate(SETTINGS):
        haystack = f"{_words(spec.key)} {_words(spec.label)} {_words(spec.note)}"
        shared = len(wanted_words & set(haystack.split()))
        similarity = max(
            difflib.SequenceMatcher(None, wanted, _words(spec.key)).ratio(),
            difflib.SequenceMatcher(None, wanted, _words(spec.label)).ratio(),
        )
        # A shared word outweighs any amount of letter overlap: "wake word"
        # must find `voice.wake_word` before anything that merely looks alike.
        scored.append((shared * 10 + similarity, -index, spec.key))
    scored.sort(reverse=True)
    return [key for _score, _order, key in scored[: max(1, limit)]]


# --- the overlay ------------------------------------------------------------
class SettingsOverlay:
    """The stored patches, and the merge that applies them."""

    def __init__(self, config_dir: Any) -> None:
        self._store = Store(config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.values: dict[str, Any] = {}
        #: Entries that did not land on the last apply, for the console.
        self.unapplied: list[Unapplied] = []

    async def async_load(self) -> dict[str, Any]:
        """Read the store, dropping anything that is no longer acceptable.

        The stored file is untrusted input: it is on disk, and write access to
        one JSON file must not be a way around the allowlist the API enforces.
        So every entry goes back through the spec table and its validator on
        the way in, and anything unknown, out of range or wrong-typed is
        dropped with a log line rather than merged.
        """
        stored = await self._store.load() or {}
        raw = stored.get("values") if isinstance(stored, dict) else None
        clean: dict[str, Any] = {}
        for key, value in (raw or {}).items():
            spec = SETTINGS_BY_KEY.get(str(key))
            if spec is None:
                _LOGGER.warning(
                    "settings: dropping %r from the store — not an editable key", key
                )
                continue
            try:
                clean[spec.key] = spec.validate(value) if spec.validate else value
            except SettingsError as err:
                _LOGGER.warning(
                    "settings: dropping %s=%r from the store — %s", key, value, err
                )
        self.values = clean
        return clean

    async def _async_save(self) -> None:
        await self._store.save({"values": self.values})

    async def async_set(self, key: str, value: Any) -> Any:
        """Validate and store one setting. Returns the coerced value."""
        spec = spec_for(key)
        coerced = spec.validate(value) if spec.validate else value
        self.values[spec.key] = coerced
        await self._async_save()
        return coerced

    async def async_reset(self, key: str) -> bool:
        """Forget an override, so the file's value shows through again."""
        spec = spec_for(key)
        if spec.key not in self.values:
            return False
        del self.values[spec.key]
        await self._async_save()
        return True

    def apply(
        self, raw: dict[str, Any], package_provenance: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], list[Unapplied]]:
        """Merge the overlay over `raw` and return the result plus what failed.

        **Never raises.** This runs inside `Jarvis.async_setup`, before there is
        an API to fix anything from, and the inputs it can be given include a
        YAML file the user has just edited. Commenting out the body of `voice:`
        turns `voice: null`, which the old overlay would have walked into; an
        exception there is a box that will not boot.

        Does not mutate `raw`: the caller keeps it as the record of what is
        actually in the file, which is what the console's "reset" needs to
        display.
        """
        merged = copy.deepcopy(raw)
        unapplied: list[Unapplied] = []

        for key, value in self.values.items():
            spec = SETTINGS_BY_KEY.get(key)
            if spec is None:  # pragma: no cover - async_load filters these
                unapplied.append(Unapplied(key, value, "not an editable setting"))
                continue

            owner = _package_owner(spec, package_provenance or {})
            if owner is not None:
                # The user edits that file. Winning over it silently would mean
                # their edit stops taking effect with nothing to explain why.
                unapplied.append(
                    Unapplied(
                        key,
                        value,
                        f"packages/{owner}.yaml sets this; edit that file instead",
                    )
                )
                continue

            parent: Any = merged
            ok = True
            for part in spec.path[:-1]:
                child = parent.get(part) if isinstance(parent, dict) else None
                if child is None:
                    child = {}
                    if isinstance(parent, dict):
                        parent[part] = child
                    else:
                        ok = False
                        break
                if not isinstance(child, dict):
                    unapplied.append(
                        Unapplied(
                            key,
                            value,
                            f"configuration.yaml has {part!r} as "
                            f"{type(child).__name__}, not a section",
                        )
                    )
                    ok = False
                    break
                parent = child
            if not ok:
                continue
            if not isinstance(parent, dict):  # pragma: no cover - defensive
                unapplied.append(Unapplied(key, value, "no section to write into"))
                continue
            parent[spec.path[-1]] = value

        self.unapplied = unapplied
        for entry in unapplied:
            _LOGGER.warning("settings: %s not applied — %s", entry.key, entry.reason)
        return merged, unapplied

    def describe(
        self,
        raw: dict[str, Any],
        package_provenance: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """One row per editable setting, for the console.

        `source` is the whole point: someone looking at a value needs to know
        whether it came from their override, the file, a package, or a default,
        because the answer decides where they go to change it.
        """
        provenance = package_provenance or {}
        unapplied = {entry.key: entry.reason for entry in self.unapplied}
        rows: list[dict[str, Any]] = []
        for spec in SETTINGS:
            yaml_value = _dig(raw, spec.path)
            overridden = spec.key in self.values
            if spec.key in unapplied:
                source = "unapplied"
            elif overridden:
                source = "overlay"
            elif _package_owner(spec, provenance) is not None:
                source = "package"
            elif yaml_value is not None:
                source = "yaml"
            else:
                source = "default"
            rows.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "group": spec.group,
                    "type": spec.type,
                    "apply": spec.apply,
                    "note": spec.note,
                    "value": self.values.get(spec.key, yaml_value),
                    "yaml_value": yaml_value,
                    "source": source,
                    "unapplied_reason": unapplied.get(spec.key),
                    "package": _package_owner(spec, provenance),
                }
            )
        return rows


def _dig(config: dict[str, Any], path: Iterable[str]) -> Any:
    node: Any = config
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _package_owner(spec: SettingSpec, provenance: dict[str, str]) -> str | None:
    """The package that supplied this key, if one did.

    Checked at both granularities `merge_packages` records: a package that
    supplied the whole `llm:` block, and a package that merged `model` into an
    `llm:` block configuration.yaml already had.
    """
    dotted = ".".join(spec.path)
    if dotted in provenance:
        return provenance[dotted]
    # The recorded key for a merged subkey is `section.subkey`, and for a whole
    # block just `section`.
    for depth in range(len(spec.path) - 1, 0, -1):
        prefix = ".".join(spec.path[:depth])
        if prefix in provenance:
            return provenance[prefix]
    return None
