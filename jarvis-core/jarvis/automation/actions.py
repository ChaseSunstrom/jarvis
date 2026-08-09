"""The script executor — runs an HA-shaped action sequence.

Supported steps::

    {service|action: "light.turn_on", target: {...}, data: {...},
     response_variable: "result", continue_on_error: false}
    {delay: 5 | "00:00:05" | {minutes: 2}}
    {wait_template: "{{ ... }}", timeout: ..., continue_on_timeout: true}
    {wait_for_trigger: [...], timeout: ..., continue_on_timeout: true}
    {condition: ...}                      # false stops the script
    {choose: [{conditions: [...], sequence: [...]}], default: [...]}
    {if: [...], then: [...], else: [...]}
    {repeat: {count|while|until|for_each: ..., sequence: [...]}}
    {variables: {name: value}}
    {stop: "why", response_variable: "result", error: false}
    {event: "my_event", event_data: {...}}
    {parallel: [ {sequence: [...]}, {...step} ]}
    {sequence: [...]}                     # plain nesting
    {scene: "scene.movie_time"}           # shorthand for scene.turn_on

Everything in ``data``/``target``/``delay``/… is rendered through
``jarvis.helpers.template.render_complex`` with the caller's variables, so
``{{ trigger.to_state.state }}`` works exactly as it does in HA.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..bus import Context
from ..const import EVENT_STATE_CHANGED
from .conditions import async_check, async_check_all
from .util import (
    as_list,
    get_clock,
    is_template,
    parse_duration,
    render_bool,
    render_complex,
    render_template,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

# Keys that mark what a step *is*, checked in this order.
_TARGET_KEYS = ("entity_id", "area_id", "device_id", "label_id", "floor_id")

MAX_WHILE_ITERATIONS = 5000


class ScriptError(Exception):
    """A step failed hard (or a `stop` step asked for an error)."""


class StopScript(Exception):
    """Internal: unwinds the sequence when a `stop`/failed condition hits."""

    def __init__(self, response: Any = None, reason: str = "") -> None:
        super().__init__(reason or "script stopped")
        self.response = response
        self.reason = reason


class ScriptRunner:
    """Executes one sequence. One runner per run (holds the variable scope)."""

    def __init__(
        self,
        jarvis: "Jarvis",
        variables: dict[str, Any] | None = None,
        context: Context | None = None,
        name: str = "script",
    ) -> None:
        self.jarvis = jarvis
        self.variables: dict[str, Any] = dict(variables or {})
        self.context = context or Context(origin="automation")
        self.name = name
        self.variables.setdefault("context", self.context)

    # --- entry point ------------------------------------------------------
    async def async_run(self, sequence: Any) -> Any:
        try:
            await self._async_run_sequence(as_list(sequence))
        except StopScript as stop:
            return stop.response
        return None

    # --- sequence / step --------------------------------------------------
    async def _async_run_sequence(self, steps: list[Any]) -> None:
        for index, step in enumerate(steps):
            await self._async_run_step(step, index)

    async def _async_run_step(self, step: Any, index: int = 0) -> None:
        if step is None:
            return
        if isinstance(step, str):
            # bare "light.turn_on" shorthand
            step = {"service": step}
        if not isinstance(step, dict):
            _LOGGER.warning("%s: step %d is not a mapping (%r)", self.name, index, step)
            return

        try:
            await self._async_dispatch(step)
        except (StopScript, asyncio.CancelledError):
            raise
        except Exception as err:
            if step.get("continue_on_error"):
                _LOGGER.warning(
                    "%s: step %d failed (%s); continuing", self.name, index, err
                )
                return
            raise

    async def _async_dispatch(self, step: dict[str, Any]) -> None:
        if "service" in step or "action" in step:
            await self._async_call_service(step)
        elif "delay" in step:
            await self._async_delay(step)
        elif "wait_template" in step:
            await self._async_wait_template(step)
        elif "wait_for_trigger" in step:
            await self._async_wait_for_trigger(step)
        elif "choose" in step:
            await self._async_choose(step)
        elif "if" in step:
            await self._async_if(step)
        elif "repeat" in step:
            await self._async_repeat(step)
        elif "condition" in step:
            await self._async_condition(step)
        elif "variables" in step:
            self._set_variables(step["variables"])
        elif "stop" in step:
            await self._async_stop(step)
        elif "event" in step:
            await self._async_fire_event(step)
        elif "parallel" in step:
            await self._async_parallel(step)
        elif "sequence" in step:
            await self._async_run_sequence(as_list(step["sequence"]))
        elif "scene" in step:
            await self.jarvis.async_call_service(
                "scene",
                "turn_on",
                {"entity_id": self._render(step["scene"])},
                context=self.context,
            )
        elif set(step) <= {"alias", "enabled", "continue_on_error"}:
            return  # documentation-only step
        else:
            _LOGGER.warning("%s: unsupported step %r", self.name, sorted(step))

    # --- rendering --------------------------------------------------------
    def _render(self, value: Any) -> Any:
        return render_complex(self.jarvis, value, self.variables)

    # --- steps ------------------------------------------------------------
    async def _async_call_service(self, step: dict[str, Any]) -> None:
        raw_service = step.get("service", step.get("action"))
        service = self._render(raw_service)
        if not isinstance(service, str) or "." not in service:
            raise ScriptError(f"invalid service {raw_service!r}")
        domain, _, name = service.partition(".")

        data: dict[str, Any] = {}
        for key in ("data", "data_template", "service_data"):
            block = step.get(key)
            if isinstance(block, dict):
                data.update(self._render(block))

        target = step.get("target")
        if isinstance(target, dict):
            data.update(self._render(target))
        elif isinstance(target, str):
            data["entity_id"] = self._render(target)

        for key in _TARGET_KEYS:
            if key in step:
                data[key] = self._render(step[key])

        response_variable = step.get("response_variable")
        result = await self.jarvis.services.async_call(
            domain,
            name,
            data,
            blocking=True,
            context=self.context,
            return_response=bool(response_variable),
        )
        if response_variable:
            self.variables[str(response_variable)] = result

    async def _async_delay(self, step: dict[str, Any]) -> None:
        seconds = parse_duration(self._render(step["delay"]))
        if seconds is None:
            _LOGGER.warning("%s: unparsable delay %r", self.name, step["delay"])
            return
        await get_clock(self.jarvis).sleep(seconds)

    async def _async_wait_template(self, step: dict[str, Any]) -> None:
        template = step["wait_template"]
        timeout = parse_duration(self._render(step.get("timeout")))
        continue_on_timeout = step.get("continue_on_timeout", True)

        if render_bool(self.jarvis, template, self.variables):
            self.variables["wait"] = {"completed": True, "remaining": timeout}
            return

        done = asyncio.Event()

        def _listener(event: Any) -> None:
            if render_bool(self.jarvis, template, self.variables):
                done.set()

        unsub = self.jarvis.bus.listen(EVENT_STATE_CHANGED, _listener)
        completed = True
        try:
            if timeout is None:
                await done.wait()
            else:
                try:
                    await asyncio.wait_for(done.wait(), timeout)
                except asyncio.TimeoutError:
                    completed = False
        finally:
            unsub()

        self.variables["wait"] = {"completed": completed, "remaining": 0 if timeout else None}
        if not completed and not continue_on_timeout:
            raise StopScript(None, "wait_template timed out")

    async def _async_wait_for_trigger(self, step: dict[str, Any]) -> None:
        from .triggers import async_attach_triggers  # local: avoids import cycle

        timeout = parse_duration(self._render(step.get("timeout")))
        continue_on_timeout = step.get("continue_on_timeout", True)
        done = asyncio.Event()
        captured: dict[str, Any] = {}

        async def _fire(trigger: dict[str, Any], context: Context | None = None) -> None:
            captured["trigger"] = trigger
            done.set()

        detach = await async_attach_triggers(
            self.jarvis, step["wait_for_trigger"], _fire
        )
        completed = True
        try:
            if timeout is None:
                await done.wait()
            else:
                try:
                    await asyncio.wait_for(done.wait(), timeout)
                except asyncio.TimeoutError:
                    completed = False
        finally:
            detach()

        self.variables["wait"] = {
            "completed": completed,
            "trigger": captured.get("trigger"),
            "remaining": 0 if timeout else None,
        }
        if completed:
            self.variables["trigger"] = captured.get("trigger")
        elif not continue_on_timeout:
            raise StopScript(None, "wait_for_trigger timed out")

    async def _async_condition(self, step: dict[str, Any]) -> None:
        value = step["condition"]
        config: Any = value if isinstance(value, (dict, list, tuple, bool)) else step
        if not await async_check(self.jarvis, config, self.variables):
            raise StopScript(None, "condition not met")

    async def _async_choose(self, step: dict[str, Any]) -> None:
        for option in as_list(step.get("choose")):
            if not isinstance(option, dict):
                continue
            conditions = option.get("conditions", option.get("condition"))
            if await async_check_all(self.jarvis, conditions, self.variables):
                await self._async_run_sequence(as_list(option.get("sequence")))
                return
        default = step.get("default")
        if default:
            await self._async_run_sequence(as_list(default))

    async def _async_if(self, step: dict[str, Any]) -> None:
        if await async_check_all(self.jarvis, step.get("if"), self.variables):
            await self._async_run_sequence(as_list(step.get("then")))
        elif step.get("else"):
            await self._async_run_sequence(as_list(step["else"]))

    async def _async_repeat(self, step: dict[str, Any]) -> None:
        config = step.get("repeat") or {}
        if not isinstance(config, dict):
            _LOGGER.warning("%s: repeat must be a mapping, got %r", self.name, config)
            return
        sequence = as_list(config.get("sequence"))
        previous = self.variables.get("repeat")
        try:
            if "count" in config:
                await self._async_repeat_count(config, sequence)
            elif "for_each" in config:
                await self._async_repeat_for_each(config, sequence)
            elif "while" in config:
                await self._async_repeat_while(config, sequence)
            elif "until" in config:
                await self._async_repeat_until(config, sequence)
            else:
                _LOGGER.warning("%s: repeat needs count/while/until/for_each", self.name)
        finally:
            if previous is None:
                self.variables.pop("repeat", None)
            else:
                self.variables["repeat"] = previous

    async def _async_repeat_count(self, config: dict[str, Any], sequence: list[Any]) -> None:
        raw = self._render(config["count"])
        try:
            count = int(float(raw))
        except (TypeError, ValueError):
            _LOGGER.warning("%s: repeat count %r is not a number", self.name, raw)
            return
        for index in range(max(0, count)):
            self.variables["repeat"] = {
                "first": index == 0,
                "index": index + 1,
                "last": index == count - 1,
            }
            await self._async_run_sequence(sequence)

    async def _async_repeat_for_each(
        self, config: dict[str, Any], sequence: list[Any]
    ) -> None:
        items = self._render(config["for_each"])
        items = list(items) if isinstance(items, (list, tuple)) else as_list(items)
        for index, item in enumerate(items):
            self.variables["repeat"] = {
                "first": index == 0,
                "index": index + 1,
                "last": index == len(items) - 1,
                "item": item,
            }
            await self._async_run_sequence(sequence)

    async def _async_repeat_while(self, config: dict[str, Any], sequence: list[Any]) -> None:
        index = 0
        while index < MAX_WHILE_ITERATIONS:
            self.variables["repeat"] = {
                "first": index == 0,
                "index": index + 1,
                "last": False,
            }
            if not await async_check_all(self.jarvis, config["while"], self.variables):
                return
            await self._async_run_sequence(sequence)
            index += 1
            # A body with no suspension point (empty sequence, pure template
            # conditions) would otherwise hold the event loop for the whole
            # 5000-iteration cap and make the run uncancellable.
            await asyncio.sleep(0)
        _LOGGER.warning("%s: repeat-while hit the iteration cap", self.name)

    async def _async_repeat_until(self, config: dict[str, Any], sequence: list[Any]) -> None:
        index = 0
        while index < MAX_WHILE_ITERATIONS:
            self.variables["repeat"] = {
                "first": index == 0,
                "index": index + 1,
                "last": False,
            }
            await self._async_run_sequence(sequence)
            index += 1
            if await async_check_all(self.jarvis, config["until"], self.variables):
                return
            await asyncio.sleep(0)  # stay cancellable (see repeat-while)
        _LOGGER.warning("%s: repeat-until hit the iteration cap", self.name)

    def _set_variables(self, block: Any) -> None:
        if not isinstance(block, dict):
            _LOGGER.warning("%s: variables must be a mapping, got %r", self.name, block)
            return
        for key, value in block.items():
            # Render one at a time so later entries can use earlier ones.
            self.variables[str(key)] = self._render(value)

    async def _async_stop(self, step: dict[str, Any]) -> None:
        reason = step.get("stop")
        if isinstance(reason, str) and is_template(reason):
            reason = render_template(self.jarvis, reason, self.variables)
        response = None
        response_variable = step.get("response_variable")
        if response_variable:
            response = self.variables.get(str(response_variable))
        if step.get("error"):
            raise ScriptError(str(reason or "script stopped with error"))
        raise StopScript(response, str(reason or ""))

    async def _async_fire_event(self, step: dict[str, Any]) -> None:
        event_type = self._render(step["event"])
        data: dict[str, Any] = {}
        for key in ("event_data", "event_data_template"):
            block = step.get(key)
            if isinstance(block, dict):
                data.update(self._render(block))
        await self.jarvis.bus.async_fire(str(event_type), data, self.context)

    async def _async_parallel(self, step: dict[str, Any]) -> None:
        branches = as_list(step.get("parallel"))
        if not branches:
            return
        tasks: list[asyncio.Task] = []
        for branch in branches:
            if isinstance(branch, dict) and "sequence" in branch:
                sequence = as_list(branch["sequence"])
            else:
                sequence = [branch]
            runner = ScriptRunner(
                self.jarvis, dict(self.variables), self.context, f"{self.name}:parallel"
            )
            tasks.append(asyncio.ensure_future(runner.async_run(sequence)))
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # A bare `gather` leaves the siblings of a failing branch running
            # detached — a half-cancelled `parallel:` would keep actuating the
            # house after the script it belongs to was already unwound.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise


async def async_execute_script(
    jarvis: "Jarvis",
    sequence: Any,
    variables: dict[str, Any] | None = None,
    context: Context | None = None,
    name: str = "script",
) -> Any:
    """Run `sequence`; returns the `stop` response variable, if any."""
    runner = ScriptRunner(jarvis, variables, context, name)
    return await runner.async_run(sequence)


# ---------------------------------------------------------------------------
# static analysis (for callers that need to know what a sequence touches)
# ---------------------------------------------------------------------------
#: Yielded by :func:`collect_domains` when a step's target cannot be known
#: until run time (a templated service name, an `area_id`-only target).
#: Callers deciding whether something needs approval must treat it as "could
#: be anything" and fail closed.
DOMAIN_UNKNOWN = "*"

_NESTED_KEYS = ("sequence", "then", "else", "default")


def collect_domains(sequence: Any) -> set[str]:
    """Every service domain a sequence could call, without running it.

    Lets a caller answer "does running this touch a gated domain?" *before*
    it starts. Templated service names and area/device-only targets are
    reported as :data:`DOMAIN_UNKNOWN` rather than silently omitted.
    """
    found: set[str] = set()

    def _walk(node: Any, depth: int = 0) -> None:
        if depth > 20:  # cyclical/pathological YAML
            found.add(DOMAIN_UNKNOWN)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item, depth + 1)
            return
        if isinstance(node, str):
            if "." in node and not is_template(node):
                found.add(node.partition(".")[0])
            elif is_template(node):
                found.add(DOMAIN_UNKNOWN)
            return
        if not isinstance(node, dict):
            return

        service = node.get("service", node.get("action"))
        if isinstance(service, str):
            if is_template(service):
                found.add(DOMAIN_UNKNOWN)
            elif "." in service:
                found.add(service.partition(".")[0])
        if "scene" in node:
            found.add("scene")
        for key in ("area_id", "device_id", "label_id", "floor_id"):
            if node.get(key):
                found.add(DOMAIN_UNKNOWN)

        target = node.get("target")
        if isinstance(target, dict):
            for key in ("area_id", "device_id", "label_id", "floor_id"):
                if target.get(key):
                    found.add(DOMAIN_UNKNOWN)

        for key in _NESTED_KEYS:
            if key in node:
                _walk(node[key], depth + 1)
        for key in ("choose", "parallel"):
            if key in node:
                _walk(node[key], depth + 1)
        repeat = node.get("repeat")
        if isinstance(repeat, dict):
            _walk(repeat.get("sequence"), depth + 1)

    _walk(as_list(sequence))
    return found


__all__ = [
    "DOMAIN_UNKNOWN",
    "ScriptError",
    "ScriptRunner",
    "StopScript",
    "async_execute_script",
    "collect_domains",
]
