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

import copy
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
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


def _model_choices(jarvis: "Jarvis") -> list[str]:
    """Whatever Ollama says it has, when it is answering."""
    agent = _llm_agent(jarvis)
    client = getattr(agent, "client", None) if agent else None
    names = getattr(client, "known_models", None)
    return sorted(names) if isinstance(names, (list, set, tuple)) else []


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
        note="The Ollama model every conversation runs on.",
        validate=_text(1, 120),
        apply_hook=_apply_model,
        choices_hook=_model_choices,
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
        type="string",
        apply=APPLY_LIVE,
        note="An IANA name. Decides when `at: \"07:00:00\"` fires and what "
        "{{ now() }} reads.",
        validate=_time_zone,
    ),
    SettingSpec(
        key="jarvis.unit_system",
        path=("jarvis", "unit_system"),
        label="Units",
        group="House",
        type="choice",
        validate=_one_of("metric", "imperial"),
        choices_hook=lambda jarvis: ["metric", "imperial"],
    ),
    SettingSpec(
        key="jarvis.currency",
        path=("jarvis", "currency"),
        label="Currency",
        group="House",
        type="string",
        validate=_text(3, 3),
    ),
    SettingSpec(
        key="jarvis.country",
        path=("jarvis", "country"),
        label="Country",
        group="House",
        type="string",
        validate=_text(2, 2),
    ),
    SettingSpec(
        key="jarvis.language",
        path=("jarvis", "language"),
        label="Language",
        group="House",
        type="string",
        validate=_text(2, 10),
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
        type="string",
        validate=_text(2, 10),
        apply_hook=_apply_voice_attr("language"),
    ),
    SettingSpec(
        key="voice.tts_voice",
        path=("voice", "tts_voice"),
        label="Voice",
        group="Voice",
        type="string",
        note="A Piper voice name, e.g. en_GB-alan-medium. Piper must have been "
        "given it at startup.",
        validate=_text(1, 80),
        apply_hook=_apply_voice_attr("tts_voice"),
    ),
    SettingSpec(
        key="voice.wake_word",
        path=("voice", "wake_word"),
        label="Wake word",
        group="Voice",
        type="string",
        note="A model openWakeWord is serving.",
        validate=_text(1, 60),
        apply_hook=_apply_voice_attr("wake_word"),
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
