"""`script:` — named action sequences that double as LLM tools.

    script:
      goodnight:
        alias: Good night
        description: Lock up and turn everything off.
        fields:
          delay_minutes:
            description: Wait this long before locking up.
            example: 5
        mode: single
        sequence:
          - service: light.turn_off
            target: {entity_id: all}
          - variables:
              result: {"locked": true}
          - stop: "done"
            response_variable: result

Each entry becomes three things: a ``script.<name>`` entity, a
``script.<name>`` service that declares ``supports_response=True``, and — when
it carries a ``description:`` — a tool called ``script_<name>`` that the model
can call directly, with the ``fields:`` as its arguments and the ``stop``
response as its result.

That third one is how a household adds abilities without writing Python, and
it is the cheap way to make a repeated job fast and consistent: six service
calls the model reasons out afresh every time become one call, in an order
somebody tuned.

**The tool's tier is the script's own reach.** Decided by the same
``needs_approval`` the automation surface uses, on this script's actual
sequence — a goodnight that locks the front door is held for a human, one
that dims the lounge is not. An operator's YAML is not a reason to skip a
gate the same call would hit anywhere else.

``description:`` is the opt-in. A script without one is a private routine:
still an entity, still a service, still reachable through the generic
``run_script`` — just not put in front of the model as a thing to reach for.

The same metadata is published at ``jarvis.data["scripts"]``, which is what
the console reads.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...automation.actions import ScriptRunner, collect_domains
from ...automation.engine import ModeController, async_await_run
from ...automation.util import as_list, render_complex
from ...bus import Context
from ...const import STATE_OFF, STATE_ON
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "script"
DATA_SCRIPTS = "scripts"
DATA_OBJECTS = "script_objects"

SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_TOGGLE = "toggle"
SERVICE_RELOAD = "reload"

RESERVED_SERVICES = {SERVICE_TURN_ON, SERVICE_TURN_OFF, SERVICE_TOGGLE, SERVICE_RELOAD}


class ScriptEntity(Entity):
    """`script.<name>` — `on` while the sequence is running."""

    def __init__(self, script: "Script") -> None:
        self._script = script
        # `name` drives the entity_id slug, so keep it equal to the YAML key.
        self._attr_name = script.object_id
        self._attr_unique_id = f"script_{script.object_id}"
        self._attr_icon = "mdi:script-text"

    @property
    def state(self) -> str:
        return STATE_ON if self._script.is_running else STATE_OFF

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        script = self._script
        return {
            "friendly_name": script.alias,
            "last_triggered": script.last_triggered,
            "mode": script.mode,
            "current": script.current,
            "max": script.max_runs,
            "description": script.description or None,
            "fields": script.fields or None,
        }


class Script:
    """One `script:` entry: metadata + a sequence, with run-mode handling."""

    def __init__(self, jarvis: "Jarvis", object_id: str, config: dict[str, Any]) -> None:
        self.jarvis = jarvis
        self.object_id = str(object_id)
        self.config = dict(config or {})
        self.alias = str(self.config.get("alias") or self.object_id.replace("_", " ").title())
        self.description = str(self.config.get("description") or "")
        self.fields: dict[str, Any] = dict(self.config.get("fields") or {})
        self.sequence = as_list(self.config.get("sequence"))
        self.mode = str(self.config.get("mode", "single")).lower()
        self.max_runs = int(self.config.get("max", 10) or 10)
        self.base_variables = self.config.get("variables") or {}
        self.last_triggered: str | None = None

        self.entity = ScriptEntity(self)
        self.runner = ModeController(
            jarvis, f"script {self.object_id}", self.mode, self.max_runs, self._write_state
        )
        # Mirror the controller's validated values (a typo'd mode runs as
        # `single`, so the entity attribute must not claim otherwise).
        self.mode = self.runner.mode
        self.max_runs = self.runner.max_runs

    # --- properties -------------------------------------------------------
    @property
    def entity_id(self) -> str:
        return self.entity.entity_id

    @property
    def current(self) -> int:
        return self.runner.current

    @property
    def is_running(self) -> bool:
        return self.runner.is_running

    def _write_state(self) -> None:
        self.entity.async_write_state()

    # --- running ----------------------------------------------------------
    def async_start(
        self, variables: dict[str, Any] | None = None, context: Context | None = None
    ) -> Any:
        """Admit and schedule a run; returns the task (None when skipped)."""
        run_variables: dict[str, Any] = {}
        if self.base_variables:
            rendered = render_complex(self.jarvis, self.base_variables, dict(variables or {}))
            if isinstance(rendered, dict):
                run_variables.update(rendered)
        run_variables.update(variables or {})
        run_variables.setdefault(
            "this", {"entity_id": self.entity_id, "alias": self.alias}
        )
        run_context = Context(
            parent_id=context.id if context is not None else None, origin="automation"
        )
        return self.runner.async_start(lambda: self._async_execute(run_variables, run_context))

    async def async_run(
        self, variables: dict[str, Any] | None = None, context: Context | None = None
    ) -> Any:
        """Run to completion and return the `stop` response (if any)."""
        return await async_await_run(self.async_start(variables, context))

    async def _async_execute(self, variables: dict[str, Any], context: Context) -> Any:
        self.last_triggered = datetime.now().astimezone().isoformat()
        self._write_state()
        runner = ScriptRunner(self.jarvis, variables, context, f"script {self.object_id}")
        return await runner.async_run(self.sequence)

    def stop(self) -> None:
        self.runner.cancel()

    # --- metadata ---------------------------------------------------------
    @property
    def domains(self) -> list[str]:
        """Service domains this script could call (``"*"`` = not statically
        knowable). Lets the LLM tool layer decide whether running it needs
        approval without having to execute it first."""
        return sorted(collect_domains(self.sequence))

    def as_tool_dict(self) -> dict[str, Any]:
        return {
            "name": self.object_id,
            "entity_id": self.entity_id,
            "service": f"{DOMAIN}.{self.object_id}",
            "alias": self.alias,
            "description": self.description,
            "fields": self.fields,
            "mode": self.mode,
            "domains": self.domains,
        }


def _resolve(jarvis: "Jarvis", entity_ids: Any) -> list[Script]:
    """Entity ids (or an explicit ``"all"``/``"*"``) -> Script objects.

    An empty target matches nothing: `script.turn_on` with no `entity_id`
    must not run every script in the house (nor `turn_off` stop them all).
    """
    scripts: dict[str, Script] = jarvis.data.get(DATA_OBJECTS, {})
    wanted = [str(e) for e in as_list(entity_ids) if str(e).strip()]
    if not wanted:
        _LOGGER.warning(
            "script service called without entity_id; "
            "pass entity_id: all to mean every script"
        )
        return []
    if any(w in ("all", "*") for w in wanted):
        return list(scripts.values())
    found = []
    for entity_id in wanted:
        script = scripts.get(entity_id)
        if script is None:
            # tolerate a bare object_id ("goodnight" instead of "script.goodnight")
            script = next(
                (s for s in scripts.values() if s.object_id == entity_id), None
            )
        if script is None:
            _LOGGER.warning("No script %s", entity_id)
            continue
        found.append(script)
    return found


#: A script's tool is named `script_<object_id>`, not `<object_id>`.
#:
#: `tests/test_tool_names.py` pins that no two integrations claim a name, and
#: a script called `search` or `remember` would otherwise shadow a built-in —
#: silently, with the operator's YAML winning, which is the worst way round.
TOOL_PREFIX = "script_"


def _register_tool(jarvis: "Jarvis", script: "Script") -> None:
    """Offer one described script to the model as a tool of its own.

    ## What was here before

    Nothing. `jarvis.data["scripts"]` was filled with `as_tool_dict()` for "the
    tool/LLM layer to enumerate", six places in the docs said a script with a
    `description:` and `fields:` becomes an LLM tool automatically, and no code
    anywhere read either. The model got one generic `run_script`, which takes
    no arguments and discards the response — so a script's `fields:` were
    unreachable and a script ending in `stop:` with a `response_variable:`
    returned its data to nobody.

    That gap matters more than a wrong docstring, because this is the cheap way
    to make a repeated job fast and consistent: six service calls the model
    reasons out every time become one tool call, in an order somebody tuned.

    ## Only a described script

    `description:` is the opt-in. A script without one is a private routine —
    it is still an entity, still a service, and still reachable through
    `run_script` — and promoting every script in the file would put a model's
    attention on internals nobody wrote for it.

    ## Its tier is its own reach

    Decided from what the script's sequence actually calls, using the same
    `needs_approval` the automation surface uses. A goodnight script that locks
    the front door is held for a human; one that dims the lounge is not. The
    alternative — one fixed tier for every script — would either hold the
    trivial ones or wave the locks through, and an operator's YAML is not a
    reason to skip a gate the same call would hit anywhere else.
    """
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        return
    if not script.description:
        return

    from ...automation.reach import describe_reach, needs_approval
    from ...llm.tools import TIER_APPROVAL, TIER_DIRECT

    gated = needs_approval(script.sequence)

    async def handler(args: dict[str, Any], context: Any = None) -> Any:
        try:
            result = await jarvis.services.async_call(
                DOMAIN,
                script.object_id,
                dict(args or {}),
                context=context,
                return_response=True,
            )
        except Exception as err:  # a broken script is a tool result, not a crash
            _LOGGER.exception("script.%s failed", script.object_id)
            return {"status": "error", "error": str(err)[:400]}
        # The `stop:`/`response_variable:` payload, which `run_script` threw
        # away — this is the half that lets a script answer a question rather
        # than only perform an action.
        return {"status": "ok", "script": script.object_id, "result": result}

    registry.register(
        name=f"{TOOL_PREFIX}{script.object_id}",
        description=(
            f"{script.description.strip()} "
            f"(A saved routine: {script.alias}.)"
            + (f" Reach: {describe_reach(script.sequence)}." if gated else "")
        ),
        parameters=_schema(script.fields),
        handler=handler,
        tier=TIER_APPROVAL if gated else TIER_DIRECT,
    )
    _LOGGER.debug(
        "script.%s offered to the model as %s%s (tier %d)",
        script.object_id,
        TOOL_PREFIX,
        script.object_id,
        TIER_APPROVAL if gated else TIER_DIRECT,
    )


def _schema(fields: dict[str, Any]) -> dict[str, Any]:
    """A script's `fields:` as a JSON schema.

    `example:` becomes the type hint, because that is the only type
    information a script field carries and a model given no type sends a
    string for a number every time.
    """
    from ...llm.tools import schema_object

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, raw in (fields or {}).items():
        spec = raw if isinstance(raw, dict) else {}
        entry: dict[str, Any] = {"type": _json_type(spec.get("example"))}
        described = str(spec.get("description") or "").strip()
        if described:
            entry["description"] = described
        if spec.get("selector") or spec.get("example") is not None:
            example = spec.get("example")
            if example is not None:
                entry.setdefault("description", "")
                entry["description"] = (
                    f"{entry['description']} e.g. {example}".strip()
                )
        properties[str(name)] = entry
        if spec.get("required"):
            required.append(str(name))
    return schema_object(properties, required)


def _json_type(example: Any) -> str:
    if isinstance(example, bool):
        return "boolean"
    if isinstance(example, int):
        return "integer"
    if isinstance(example, float):
        return "number"
    if isinstance(example, (list, tuple)):
        return "array"
    if isinstance(example, dict):
        return "object"
    return "string"


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}

    platform: EntityPlatform = jarvis.data.get("script_platform") or EntityPlatform(
        jarvis, DOMAIN, DOMAIN
    )
    jarvis.data["script_platform"] = platform
    scripts: dict[str, Script] = jarvis.data.setdefault(DATA_OBJECTS, {})
    metadata: dict[str, Any] = jarvis.data.setdefault(DATA_SCRIPTS, {})

    for object_id, raw in config.items():
        if isinstance(raw, list):  # `script: {name: [ ...sequence ]}` shorthand
            raw = {"sequence": raw}
        if not isinstance(raw, dict):
            _LOGGER.warning("script %s: expected a mapping, got %r", object_id, raw)
            continue
        script = Script(jarvis, str(object_id), raw)
        await platform.async_add_entities([script.entity])
        scripts[script.entity_id] = script
        metadata[script.object_id] = script.as_tool_dict()

        if script.object_id in RESERVED_SERVICES:
            _LOGGER.warning(
                "script %s shadows script.%s; skipping service registration",
                script.object_id,
                script.object_id,
            )
            continue

        def _make_handler(target: Script) -> Any:
            async def _handler(call: ServiceCall) -> Any:
                return await target.async_run(dict(call.data), call.context)

            return _handler

        jarvis.services.register(
            DOMAIN,
            script.object_id,
            _make_handler(script),
            description=script.description or f"Run the {script.alias} script.",
            fields=script.fields,
            supports_response=True,
        )
        _register_tool(jarvis, script)

    # --- shared services --------------------------------------------------
    async def _handle_turn_on(call: ServiceCall) -> None:
        variables = call.get("variables") or {}
        for script in _resolve(jarvis, call.get("entity_id")):
            script.async_start(dict(variables), call.context)

    async def _handle_turn_off(call: ServiceCall) -> None:
        for script in _resolve(jarvis, call.get("entity_id")):
            script.stop()

    async def _handle_toggle(call: ServiceCall) -> None:
        variables = call.get("variables") or {}
        for script in _resolve(jarvis, call.get("entity_id")):
            if script.is_running:
                script.stop()
            else:
                script.async_start(dict(variables), call.context)

    async def _handle_reload(call: ServiceCall) -> None:
        try:
            from ...config import load_config

            # load_config walks the config dir (!include/!secret) — real,
            # blocking file I/O, so keep it off the event loop.
            fresh = await asyncio.to_thread(load_config, jarvis.config_dir)
        except Exception:
            _LOGGER.exception("Could not re-read configuration; keeping scripts")
            return
        jarvis.config = fresh
        for script in list(scripts.values()):
            script.stop()
            jarvis.services.remove(DOMAIN, script.object_id)
            jarvis.states.remove(script.entity_id)
            jarvis.data.get("entity_objects", {}).pop(script.entity_id, None)
        scripts.clear()
        metadata.clear()
        platform.entities.clear()
        await async_setup(jarvis, fresh.get(DOMAIN))

    jarvis.services.register(
        DOMAIN,
        SERVICE_TURN_ON,
        _handle_turn_on,
        description="Start a script without waiting for it to finish.",
        fields={
            "entity_id": {"description": "Script(s) to run.", "required": True},
            "variables": {"description": "Variables passed into the sequence."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_TURN_OFF,
        _handle_turn_off,
        description="Stop a running script.",
        fields={"entity_id": {"description": "Script(s) to stop."}},
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_TOGGLE,
        _handle_toggle,
        description="Start a script, or stop it if it is already running.",
        fields={"entity_id": {"description": "Script(s) to toggle."}},
    )
    jarvis.services.register(
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
        description="Re-read scripts from the configuration directory.",
    )
    return True


__all__ = ["DOMAIN", "Script", "async_setup"]
