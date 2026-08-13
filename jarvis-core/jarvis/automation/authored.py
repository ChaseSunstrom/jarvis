"""Automations created from the console, stored beside the ones in YAML.

`automations.yaml` is the user's file: hand-written, commented, version
controlled if they like. Anything created from a UI has to live somewhere else,
because a round-trip writer would reformat the file and lose the comments — the
same reason the settings overlay does not rewrite `configuration.yaml`.

So authored automations live in `.storage/automations.json` and are loaded
alongside the YAML ones at setup and on every reload. The engine cannot tell
the difference and does not need to: `AutomationManager.async_setup_automations`
takes a list of configs, and this contributes to it.

Two things are deliberate.

**Ids are namespaced.** Every authored automation gets an id prefixed with
`ui_`, so a console-created automation can never collide with, shadow or
silently replace one the user wrote by hand. Deleting by id therefore cannot
delete a YAML automation even if something asks it to.

**Validation happens before storage, not at load.** A malformed automation that
reaches the store is a malformed automation that fails on every subsequent
start, and by then the console that created it has moved on. `validate` is the
same shape check the engine will apply, run early enough that the error can be
returned to whoever typed it.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ..store import Store
from .triggers import TRIGGER_PLATFORMS

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "automations"
STORAGE_VERSION = 1

#: Prefix on every id this store mints. See the module docstring.
ID_PREFIX = "ui_"

DATA_AUTHORED = "authored_automations"

#: Fields this store keeps for itself. They are written alongside the
#: automation, so they have to be stripped both on the way out to the engine
#: and on the way back in through [validate] — which refuses unknown fields and
#: would otherwise drop every automation it had just saved.
BOOKKEEPING = ("id", "created_at", "updated_at")

#: Fields an authored automation may carry. Anything else is dropped rather
#: than stored: the engine ignores unknown keys, so an accepted-but-ignored
#: field is a setting that appears to work and does nothing.
ALLOWED_FIELDS = frozenset(
    {
        "id",
        "alias",
        "description",
        "mode",
        "max",
        "trigger",
        "condition",
        "action",
        # `variables:` is read by the engine (`Automation._async_trigger_fired`
        # merges it into every run's variable scope) and was missing here, so a
        # templated automation could be written in YAML and **not** through the
        # console or by the model — the two surfaces silently refused a field
        # the engine supports. That is the inverse of the rule this list exists
        # for: an accepted-but-ignored field is a setting that does nothing,
        # and a rejected-but-supported one is a capability nobody can reach.
        "variables",
        # Same shape. The engine reads it when deciding whether an automation
        # comes back enabled after a restart.
        "initial_state",
        # The plural spellings `Automation.__init__` accepts. `reach.part_of`
        # exists precisely because the engine reads either, and a validator
        # that took only the singular refused automations the engine would
        # have run — which is the bug `reach.part_of`'s docstring describes
        # from the other side.
        "triggers",
        "conditions",
        "actions",
    }
)

MAX_ALIAS = 120
MAX_DESCRIPTION = 500
#: A cap on how much one automation may be. Not a security boundary — the API
#: is authenticated — but a runaway paste should fail loudly and early rather
#: than becoming a storage file nothing can load.
MAX_STEPS = 100


class AuthoredError(Exception):
    """A proposed automation was refused. The message is shown to the user."""


def validate(config: Any) -> dict[str, Any]:
    """Check and normalise one automation. Raises [AuthoredError] with a reason.

    Deliberately shape-only. Whether `light.turn_on` exists, or the entity it
    names is real, is the engine's business at run time and changes as
    integrations come and go — refusing here on that basis would make a
    perfectly good automation unsaveable because the light is unplugged.

    Trigger platforms are the one exception, because they are not like that:
    [TRIGGER_PLATFORMS] is a closed table in the engine that no integration
    adds to, so an unknown platform is knowably wrong now rather than merely
    unavailable now. It is also the worst thing to let through — the automation
    saves, appears in the list, and never fires — so it is checked here, from
    the engine's own table so the two cannot drift apart.
    """
    if not isinstance(config, dict):
        raise AuthoredError("An automation must be an object.")

    unknown = set(config) - ALLOWED_FIELDS
    if unknown:
        raise AuthoredError(f"Unknown field(s): {', '.join(sorted(unknown))}.")

    alias = str(config.get("alias") or "").strip()
    if not alias:
        raise AuthoredError("Give it a name.")
    if len(alias) > MAX_ALIAS:
        raise AuthoredError(f"The name must be under {MAX_ALIAS} characters.")

    description = str(config.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION:
        raise AuthoredError(f"The description must be under {MAX_DESCRIPTION} characters.")

    triggers = _as_list(config.get("trigger"))
    if not triggers:
        raise AuthoredError("Give it at least one trigger, or nothing will ever run it.")
    actions = _as_list(config.get("action"))
    if not actions:
        raise AuthoredError("Give it at least one action, or it will run and do nothing.")
    conditions = _as_list(config.get("condition"))

    for label, steps in (("trigger", triggers), ("action", actions), ("condition", conditions)):
        if len(steps) > MAX_STEPS:
            raise AuthoredError(f"That is more than {MAX_STEPS} {label} steps.")
        for step in steps:
            if not isinstance(step, dict):
                raise AuthoredError(f"Each {label} must be an object.")

    for step in triggers:
        # The engine accepts `trigger:` as a synonym for `platform:`; take both
        # so an automation copied out of newer documentation is not refused for
        # a spelling the engine would have honoured.
        platform = str(step.get("platform") or step.get("trigger") or "").strip().lower()
        if not platform:
            raise AuthoredError("Every trigger needs a `platform`.")
        if platform not in TRIGGER_PLATFORMS:
            known = ", ".join(sorted(TRIGGER_PLATFORMS))
            raise AuthoredError(
                f"There is no `{platform}` trigger. Available: {known}."
            )

    mode = str(config.get("mode") or "single").strip().lower()
    if mode not in ("single", "restart", "queued", "parallel"):
        raise AuthoredError("Mode must be single, restart, queued or parallel.")

    clean: dict[str, Any] = {
        "alias": alias,
        "mode": mode,
        "trigger": triggers,
        "action": actions,
    }
    if description:
        clean["description"] = description
    if conditions:
        clean["condition"] = conditions
    if config.get("max") is not None:
        try:
            clean["max"] = max(1, int(config["max"]))
        except (TypeError, ValueError):
            raise AuthoredError("`max` must be a whole number.") from None
    return clean


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


class AuthoredStore:
    """The authored automations, persisted."""

    def __init__(self, config_dir: Any) -> None:
        self._store = Store(config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.items: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> list[dict[str, Any]]:
        """Read the store, dropping anything that no longer validates.

        A stored automation that fails validation is dropped with a log line
        rather than raising: one bad record from an older format must not stop
        every other automation — and the box — from starting.
        """
        stored = await self._store.load() or {}
        raw = stored.get("items") if isinstance(stored, dict) else None
        items: dict[str, dict[str, Any]] = {}
        for entry in (raw or []):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id") or "")
            if not entry_id.startswith(ID_PREFIX):
                _LOGGER.warning("automations: dropping %r — not an authored id", entry_id)
                continue
            try:
                clean = validate(
                    {k: v for k, v in entry.items() if k not in BOOKKEEPING}
                )
            except AuthoredError as err:
                _LOGGER.warning("automations: dropping %s — %s", entry_id, err)
                continue
            clean["id"] = entry_id
            clean["created_at"] = entry.get("created_at")
            clean["updated_at"] = entry.get("updated_at")
            items[entry_id] = clean
        self.items = items
        return self.configs()

    def configs(self) -> list[dict[str, Any]]:
        """What the engine wants: a list of automation configs.

        Bookkeeping fields are stripped — the engine has no use for them and
        would carry them into the entity's attributes.
        """
        return [
            {k: v for k, v in item.items() if k not in ("created_at", "updated_at")}
            for item in self.items.values()
        ]

    def entries(self) -> list[dict[str, Any]]:
        """What the console wants: the automations with their timestamps."""
        return list(self.items.values())

    async def _async_save(self) -> None:
        await self._store.save({"items": list(self.items.values())})

    async def async_create(self, config: Any) -> dict[str, Any]:
        clean = validate(config)
        entry_id = f"{ID_PREFIX}{uuid.uuid4().hex[:12]}"
        now = time.time()
        clean["id"] = entry_id
        clean["created_at"] = now
        clean["updated_at"] = now
        self.items[entry_id] = clean
        await self._async_save()
        return clean

    async def async_update(self, entry_id: str, config: Any) -> dict[str, Any]:
        existing = self.items.get(entry_id)
        if existing is None:
            raise AuthoredError(f"{entry_id} is not an automation this console created.")
        clean = validate(config)
        clean["id"] = entry_id
        clean["created_at"] = existing.get("created_at")
        clean["updated_at"] = time.time()
        self.items[entry_id] = clean
        await self._async_save()
        return clean

    async def async_delete(self, entry_id: str) -> bool:
        """Forget an authored automation.

        Returns False rather than raising for an unknown id: a delete of
        something that is already gone has achieved what the caller wanted. An
        id that is not ours is a different matter and refuses, because the
        caller is asking to delete a YAML automation and silently doing nothing
        would look like it worked.
        """
        if not entry_id.startswith(ID_PREFIX):
            raise AuthoredError(
                f"{entry_id} comes from your YAML, not from the console. "
                "Edit automations.yaml to change it."
            )
        if entry_id not in self.items:
            return False
        del self.items[entry_id]
        await self._async_save()
        return True


def get_authored(jarvis: "Jarvis") -> AuthoredStore:
    """The shared store, created on first use."""
    store = jarvis.data.get(DATA_AUTHORED)
    if not isinstance(store, AuthoredStore):
        store = AuthoredStore(jarvis.config_dir)
        jarvis.data[DATA_AUTHORED] = store
    return store
