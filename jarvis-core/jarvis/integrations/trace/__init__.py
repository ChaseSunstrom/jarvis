"""trace — why did that automation do that?

An automation that misbehaves is otherwise a mystery: it either ran or it did
not, and the log says neither. This records, per run:

* which trigger fired, and the variables the run started with;
* every condition that was evaluated, in order, with its result — so a run
  that never started names **the condition that stopped it**, not just
  "conditions not met";
* every step: what it was, how long it took, and how it ended (``ok``,
  ``stopped``, ``error``), including nesting depth through
  ``choose`` / ``if`` / ``repeat`` / ``parallel``;
* the reason a sequence unwound early — a ``condition:`` step that went false,
  a ``stop:``, or an exception.

The last N runs per automation (and per script) are kept in memory, bounded
both in count and in steps-per-run, so a runaway ``repeat`` cannot eat the
heap. Nothing is written to disk.

Configuration::

    trace:
      max_runs: 10        # traces kept per automation/script
      max_traced: 100     # how many distinct automations to track
      max_steps: 200      # steps recorded per run before truncating

Services
    ``trace.get``   (automation_id, limit) → ``{"traces": [...]}``
    ``trace.list``            → one summary line per traced automation
    ``trace.clear`` (automation_id)

Event: ``trace_recorded`` fires with the summary of every finished run — small
enough to push down a websocket, which is what a live console would subscribe
to (``subscribe_events`` with ``event_type: trace_recorded``). Nothing in
jarvis-web consumes it yet; the event is the seam, not a feature.

LLM tool: ``get_automation_trace``.

How it hooks in
---------------
The engine has no callback for this, so the integration wraps three seams at
import time — ``Automation.async_trigger``, ``Automation._async_execute`` and
``ScriptRunner._async_run_step`` (plus ``Script._async_execute``) — and each
wrapper is a pass-through unless a recorder exists *for that exact Jarvis
instance*. Behaviour is unchanged when ``trace:`` is not configured, and two
Jarvis instances in one process do not see each other's runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...automation.conditions import async_check
from ...automation.util import as_list
from ...services import ServiceCall

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "trace"
DEPENDENCIES = ["automation"]

EVENT_TRACE_RECORDED = "trace_recorded"

DEFAULT_MAX_RUNS = 10
DEFAULT_MAX_TRACED = 100
DEFAULT_MAX_STEPS = 200

#: Long strings in variables are truncated rather than stored whole.
MAX_VALUE_CHARS = 200
MAX_COLLECTION = 20
MAX_DEPTH = 4


# ---------------------------------------------------------------------------
# safe rendering of arbitrary run data
# ---------------------------------------------------------------------------
def jsonable(value: Any, depth: int = 0) -> Any:
    """Bounded, JSON-safe view of whatever a run was carrying around."""
    if depth > MAX_DEPTH:
        return "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "..."
    if hasattr(value, "as_dict"):
        try:
            return jsonable(value.as_dict(), depth + 1)
        except Exception:  # pragma: no cover - defensive
            return str(value)[:MAX_VALUE_CHARS]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION:
                out["..."] = f"{len(value) - MAX_COLLECTION} more"
                break
            out[str(key)] = jsonable(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        rendered = [jsonable(item, depth + 1) for item in items[:MAX_COLLECTION]]
        if len(items) > MAX_COLLECTION:
            rendered.append(f"... {len(items) - MAX_COLLECTION} more")
        return rendered
    return str(value)[:MAX_VALUE_CHARS]


def describe_condition(config: Any) -> str:
    """A condition in one readable line: ``state entity_id=... state=on``."""
    if isinstance(config, str):
        return config
    if not isinstance(config, dict):
        return str(config)[:MAX_VALUE_CHARS]
    bits = [str(config.get("condition") or "condition")]
    for key in (
        "entity_id", "state", "attribute", "above", "below", "value_template",
        "after", "before", "weekday", "id", "for",
    ):
        if key in config:
            bits.append(f"{key}={config[key]}")
    return " ".join(bits)[:MAX_VALUE_CHARS]


def describe_step(step: Any) -> str:
    """One short label for a step, the way a person would name it."""
    if isinstance(step, str):
        return step
    if not isinstance(step, dict):
        return type(step).__name__
    if step.get("alias"):
        return str(step["alias"])
    service = step.get("service", step.get("action"))
    if isinstance(service, str):
        return service
    for key in ("delay", "wait_template", "wait_for_trigger", "choose", "if",
                "repeat", "condition", "variables", "stop", "event", "parallel",
                "sequence", "scene"):
        if key in step:
            return key
    return ", ".join(sorted(str(k) for k in step)) or "step"


# ---------------------------------------------------------------------------
# trace records
# ---------------------------------------------------------------------------
@dataclass
class StepTrace:
    index: int
    label: str
    step: Any
    runner: str
    depth: int
    started: float
    elapsed_ms: float | None = None
    status: str = "running"
    reason: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "label": self.label,
            "depth": self.depth,
            "runner": self.runner,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "step": jsonable(self.step),
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class RunTrace:
    id: str
    kind: str                      # automation | script
    trace_id: str                  # the automation/script id
    name: str
    entity_id: str | None
    context_id: str | None
    parent_id: str | None
    started: float
    trigger: Any = None
    variables: dict[str, Any] = field(default_factory=dict)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepTrace] = field(default_factory=list)
    status: str = "running"
    reason: str | None = None
    error: str | None = None
    finished: float | None = None
    elapsed_ms: float | None = None
    truncated_steps: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    _depth: int = 0
    _started_mono: float = field(default_factory=time.perf_counter)

    # --- steps ------------------------------------------------------------
    def begin_step(self, step: Any, index: int, runner: str) -> StepTrace | None:
        if len(self.steps) >= self.max_steps:
            self.truncated_steps += 1
            self._depth += 1
            return None
        entry = StepTrace(
            index=index,
            label=describe_step(step),
            step=step,
            runner=runner,
            depth=self._depth,
            started=time.time(),
        )
        entry.__dict__["_mono"] = time.perf_counter()
        self.steps.append(entry)
        self._depth += 1
        return entry

    def end_step(
        self,
        entry: StepTrace | None,
        status: str,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        self._depth = max(0, self._depth - 1)
        if entry is None:
            return
        started = entry.__dict__.get("_mono", entry.started)
        entry.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        entry.status = status
        entry.reason = reason
        entry.error = error

    # --- lifecycle --------------------------------------------------------
    def finish(self, status: str) -> None:
        self.finished = time.time()
        self.elapsed_ms = round((time.perf_counter() - self._started_mono) * 1000, 3)
        if self.status == "running":
            self.status = status

    # --- output -----------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "kind": self.kind,
            "id": self.trace_id,
            "name": self.name,
            "entity_id": self.entity_id,
            "started": self.started,
            "elapsed_ms": self.elapsed_ms,
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "steps": len(self.steps) + self.truncated_steps,
            "context_id": self.context_id,
        }

    def as_dict(self) -> dict[str, Any]:
        payload = self.summary()
        payload.update(
            {
                "parent_id": self.parent_id,
                "trigger": jsonable(self.trigger),
                "variables": jsonable(self.variables),
                "conditions": self.conditions,
                "step_details": [s.as_dict() for s in self.steps],
            }
        )
        if self.truncated_steps:
            payload["truncated_steps"] = self.truncated_steps
        return payload


# ---------------------------------------------------------------------------
# the recorder
# ---------------------------------------------------------------------------
class TraceRecorder:
    """Bounded per-automation ring buffers of :class:`RunTrace`."""

    def __init__(
        self,
        jarvis: "Jarvis",
        max_runs: int = DEFAULT_MAX_RUNS,
        max_traced: int = DEFAULT_MAX_TRACED,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.jarvis = jarvis
        self.max_runs = max(1, int(max_runs or DEFAULT_MAX_RUNS))
        self.max_traced = max(1, int(max_traced or DEFAULT_MAX_TRACED))
        self.max_steps = max(1, int(max_steps or DEFAULT_MAX_STEPS))
        self.traces: "OrderedDict[str, deque[RunTrace]]" = OrderedDict()
        self._active: dict[str, RunTrace] = {}

    # --- storage ----------------------------------------------------------
    def _bucket(self, trace_id: str) -> "deque[RunTrace]":
        bucket = self.traces.get(trace_id)
        if bucket is None:
            bucket = deque(maxlen=self.max_runs)
            self.traces[trace_id] = bucket
            while len(self.traces) > self.max_traced:
                self.traces.popitem(last=False)
        self.traces.move_to_end(trace_id)
        return bucket

    def _store(self, run: RunTrace) -> None:
        self._bucket(run.trace_id).append(run)
        try:
            self.jarvis.bus.fire(EVENT_TRACE_RECORDED, run.summary())
        except Exception:  # pragma: no cover - a bad listener must not matter
            _LOGGER.exception("Could not fire %s", EVENT_TRACE_RECORDED)

    # --- run lifecycle ----------------------------------------------------
    def start_run(
        self,
        kind: str,
        trace_id: str,
        name: str,
        entity_id: str | None,
        variables: dict[str, Any] | None,
        context: Any,
    ) -> RunTrace:
        variables = dict(variables or {})
        # The trigger call stashed its condition verdicts in a context
        # variable; a task created from it inherits them. Take them, then
        # clear the slot so a script this run goes on to start does not
        # inherit the automation's conditions as if they were its own.
        conditions = list(_CONDITION_LOG.get() or [])
        _CONDITION_LOG.set(None)
        run = RunTrace(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            trace_id=str(trace_id),
            name=name,
            entity_id=entity_id,
            context_id=getattr(context, "id", None),
            parent_id=getattr(context, "parent_id", None),
            started=time.time(),
            trigger=variables.get("trigger"),
            variables={k: v for k, v in variables.items() if k != "context"},
            conditions=conditions,
            max_steps=self.max_steps,
        )
        if run.context_id:
            self._active[run.context_id] = run
        return run

    def finish_run(self, run: RunTrace, status: str) -> None:
        run.finish(status)
        if run.context_id:
            self._active.pop(run.context_id, None)
        self._store(run)

    def active(self, context_id: str | None) -> RunTrace | None:
        return self._active.get(context_id) if context_id else None

    def record_blocked(
        self,
        kind: str,
        trace_id: str,
        name: str,
        entity_id: str | None,
        variables: dict[str, Any] | None,
        context: Any,
        conditions: list[dict[str, Any]],
        status: str,
        reason: str,
    ) -> RunTrace:
        """A run that never started: conditions said no, or the mode did."""
        run = self.start_run(kind, trace_id, name, entity_id, variables, context)
        run.conditions = conditions
        run.status = status
        run.reason = reason
        run.finish(status)
        if run.context_id:
            self._active.pop(run.context_id, None)
        self._store(run)
        return run

    # --- reading ----------------------------------------------------------
    def resolve(self, wanted: Any) -> list[str]:
        """Match an automation id, entity id or alias against what we have."""
        text = str(wanted or "").strip().lower()
        if not text or text in ("all", "*"):
            return list(self.traces)
        if text in self.traces:
            return [text]
        hits = []
        for trace_id, bucket in self.traces.items():
            if trace_id.lower() == text:
                hits.append(trace_id)
                continue
            last = bucket[-1] if bucket else None
            if last is None:
                continue
            if text in ((last.entity_id or "").lower(), last.name.lower()):
                hits.append(trace_id)
        return hits

    def get(self, wanted: Any, limit: int | None = None) -> list[RunTrace]:
        runs: list[RunTrace] = []
        for trace_id in self.resolve(wanted):
            runs.extend(self.traces.get(trace_id, ()))
        runs.sort(key=lambda r: r.started, reverse=True)
        if limit:
            runs = runs[: int(limit)]
        return runs

    def overview(self) -> list[dict[str, Any]]:
        out = []
        for trace_id, bucket in self.traces.items():
            if not bucket:
                continue
            last = bucket[-1]
            out.append(
                {
                    "id": trace_id,
                    "kind": last.kind,
                    "name": last.name,
                    "entity_id": last.entity_id,
                    "runs": len(bucket),
                    "last_status": last.status,
                    "last_run": last.started,
                    "last_reason": last.reason,
                }
            )
        out.sort(key=lambda item: item["last_run"], reverse=True)
        return out


# ---------------------------------------------------------------------------
# instrumentation
# ---------------------------------------------------------------------------
_RECORDERS: list[TraceRecorder] = []
_PATCHED = False

#: Set for the duration of ``Automation.async_trigger`` so the patched
#: ``async_check_all`` can report each condition's result back to it.
_CONDITION_LOG: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "jarvis_trace_conditions", default=None
)


def _recorder_for(jarvis: Any) -> TraceRecorder | None:
    for recorder in _RECORDERS:
        if recorder.jarvis is jarvis:
            return recorder
    return None


def _install_instrumentation() -> None:
    """Wrap the engine seams once per process. Idempotent."""
    global _PATCHED
    if _PATCHED:
        return

    from ...automation import actions as actions_module
    from ...automation import engine as engine_module

    original_check_all = engine_module.async_check_all
    original_trigger = engine_module.Automation.async_trigger
    original_automation_execute = engine_module.Automation._async_execute
    original_step = actions_module.ScriptRunner._async_run_step

    async def traced_check_all(jarvis: Any, configs: Any, variables: Any = None) -> bool:
        log = _CONDITION_LOG.get()
        if log is None:
            return await original_check_all(jarvis, configs, variables)
        # Same semantics as the original (short-circuit AND over as_list),
        # with each individual verdict written down on the way past.
        for index, config in enumerate(as_list(configs)):
            started = time.perf_counter()
            try:
                ok = bool(await async_check(jarvis, config, variables))
            except Exception as exc:  # pragma: no cover - async_check swallows its own
                log.append(
                    {
                        "index": index,
                        "condition": jsonable(config),
                        "result": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise
            log.append(
                {
                    "index": index,
                    "condition": jsonable(config),
                    "result": ok,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            if not ok:
                return False
        return True

    async def traced_trigger(
        self: Any,
        variables: dict[str, Any] | None = None,
        context: Any = None,
        skip_condition: bool = False,
        wait: bool = False,
    ) -> Any:
        recorder = _recorder_for(self.jarvis)
        if recorder is None:
            # Blank the slot: an untraced instance's conditions must not land
            # in a traced one's run just because a task ancestry connects them.
            token = _CONDITION_LOG.set(None)
            try:
                return await original_trigger(self, variables, context, skip_condition, wait)
            finally:
                _CONDITION_LOG.reset(token)

        token = _CONDITION_LOG.set([])
        try:
            result = await original_trigger(self, variables, context, skip_condition, wait)
        finally:
            checks = list(_CONDITION_LOG.get() or [])
            _CONDITION_LOG.reset(token)

        if result is not None or not self.enabled:
            return result

        merged = {**(self.base_variables or {}), **(variables or {})}
        if checks and not checks[-1]["result"]:
            failed = checks[-1]
            recorder.record_blocked(
                "automation", self.automation_id, self.alias, self.entity_id,
                merged, context, checks, "condition_failed",
                f"condition {failed['index'] + 1} of {len(self.conditions)} was false: "
                f"{describe_condition(failed['condition'])}",
            )
        elif not wait:
            # Conditions passed (or were skipped) and still no task: the run
            # mode refused it. Worth a trace — "it silently did nothing" is
            # exactly the bug people cannot find.
            recorder.record_blocked(
                "automation", self.automation_id, self.alias, self.entity_id,
                merged, context, checks, "skipped",
                f"a run was already in progress (mode: {self.mode}, "
                f"current: {self.current})",
            )
        return result

    def _make_execute_wrapper(original: Any, kind: str) -> Any:
        async def traced_execute(self: Any, variables: dict[str, Any], context: Any) -> Any:
            recorder = _recorder_for(self.jarvis)
            if recorder is None:
                return await original(self, variables, context)
            trace_id = getattr(self, "automation_id", None) or getattr(self, "object_id", "")
            run = recorder.start_run(
                kind, str(trace_id), getattr(self, "alias", str(trace_id)),
                getattr(self, "entity_id", None), variables, context,
            )
            try:
                result = await original(self, variables, context)
            except asyncio.CancelledError:
                recorder.finish_run(run, "cancelled")
                raise
            except BaseException as exc:
                run.error = run.error or f"{type(exc).__name__}: {exc}"
                recorder.finish_run(run, "error")
                raise
            recorder.finish_run(run, "ok")
            return result

        return traced_execute

    async def traced_step(self: Any, step: Any, index: int = 0) -> Any:
        recorder = _recorder_for(self.jarvis)
        run = recorder.active(getattr(self.context, "id", None)) if recorder else None
        if run is None:
            return await original_step(self, step, index)

        entry = run.begin_step(step, index, self.name)
        try:
            result = await original_step(self, step, index)
        except actions_module.StopScript as stop:
            reason = stop.reason or "stopped"
            run.end_step(entry, "stopped", reason=reason)
            run.status = "stopped"
            run.reason = run.reason or reason
            raise
        except asyncio.CancelledError:
            run.end_step(entry, "cancelled")
            run.status = "cancelled"
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            run.end_step(entry, "error", error=error)
            run.status = "error"
            run.error = run.error or error
            raise
        run.end_step(entry, "ok")
        return result

    engine_module.async_check_all = traced_check_all
    engine_module.Automation.async_trigger = traced_trigger
    engine_module.Automation._async_execute = _make_execute_wrapper(
        original_automation_execute, "automation"
    )
    actions_module.ScriptRunner._async_run_step = traced_step

    try:
        from .. import script as script_module

        script_module.Script._async_execute = _make_execute_wrapper(
            script_module.Script._async_execute, "script"
        )
    except Exception:  # pragma: no cover - script integration is optional
        _LOGGER.debug("trace: script integration not available; automations only")

    _PATCHED = True


def get_trace(jarvis: "Jarvis") -> TraceRecorder | None:
    recorder = jarvis.data.get(DOMAIN)
    return recorder if isinstance(recorder, TraceRecorder) else None


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    recorder = TraceRecorder(
        jarvis,
        max_runs=int(options.get("max_runs") or DEFAULT_MAX_RUNS),
        max_traced=int(options.get("max_traced") or DEFAULT_MAX_TRACED),
        max_steps=int(options.get("max_steps") or DEFAULT_MAX_STEPS),
    )
    _install_instrumentation()
    # Setting up twice for the same instance (a reload) must not leave the old
    # recorder in the list: `_recorder_for` returns the first match, so runs
    # would keep landing in a recorder nothing can read any more.
    previous = jarvis.data.get(DOMAIN)
    if isinstance(previous, TraceRecorder):
        try:
            _RECORDERS.remove(previous)
        except ValueError:  # pragma: no cover - already gone
            pass
    jarvis.data[DOMAIN] = recorder
    _RECORDERS.append(recorder)

    async def _shutdown() -> None:
        try:
            _RECORDERS.remove(recorder)
        except ValueError:  # pragma: no cover - already gone
            pass

    jarvis.register_shutdown(_shutdown)

    async def handle_get(call: ServiceCall) -> dict[str, Any]:
        wanted = call.get("automation_id") or call.get("entity_id") or call.get("id")
        limit = call.get("limit")
        runs = recorder.get(wanted, int(limit) if limit else None)
        return {
            "automation_id": wanted,
            "count": len(runs),
            "traces": [run.as_dict() for run in runs],
        }

    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        overview = recorder.overview()
        return {"traced": overview, "count": len(overview)}

    async def handle_clear(call: ServiceCall) -> dict[str, Any]:
        wanted = call.get("automation_id") or call.get("id")
        if not wanted:
            cleared = len(recorder.traces)
            recorder.traces.clear()
            return {"cleared": cleared}
        cleared = 0
        for trace_id in recorder.resolve(wanted):
            cleared += len(recorder.traces.pop(trace_id, ()))
        return {"cleared": cleared}

    jarvis.services.register(
        DOMAIN, "get", handle_get, supports_response=True,
        description="Recent run traces for one automation or script (newest first).",
        fields={
            "automation_id": {
                "description": "Automation id, entity_id or alias. 'all' for everything.",
                "required": True,
            },
            "limit": {"description": "Maximum runs to return."},
        },
    )
    jarvis.services.register(
        DOMAIN, "list", handle_list, supports_response=True,
        description="Every automation/script with recorded runs, most recent first.",
    )
    jarvis.services.register(
        DOMAIN, "clear", handle_clear, supports_response=True,
        description="Discard recorded traces.",
        fields={"automation_id": {"description": "Leave empty to clear everything."}},
    )

    _register_tools(jarvis, recorder)
    _LOGGER.info("trace ready: last %d runs per automation", recorder.max_runs)
    return True


def _register_tools(jarvis: "Jarvis", recorder: TraceRecorder) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("trace: no LLM tool registry; services registered without tools")
        return

    from ...llm.tools import schema_object

    async def tool_get_trace(args: dict[str, Any], context: Any = None) -> Any:
        wanted = args.get("automation") or args.get("automation_id") or "all"
        limit = int(args.get("limit") or 3)
        runs = recorder.get(wanted, limit)
        if not runs:
            return {
                "status": "error",
                "error": f"no recorded runs for {wanted!r}",
                "known": [item["id"] for item in recorder.overview()],
            }
        return {
            "status": "ok",
            "count": len(runs),
            "traces": [
                {
                    **run.summary(),
                    "conditions": run.conditions,
                    "steps": [
                        {
                            "label": s.label,
                            "status": s.status,
                            "elapsed_ms": s.elapsed_ms,
                            "reason": s.reason,
                            "error": s.error,
                        }
                        for s in run.steps
                    ],
                }
                for run in runs
            ],
        }

    registry.register(
        name="get_automation_trace",
        description=(
            "Find out what an automation actually did on its last few runs — "
            "which trigger fired, which condition stopped it, which step failed. "
            "Use it whenever the user says an automation did not work."
        ),
        parameters=schema_object(
            {
                "automation": {
                    "type": "string",
                    "description": "Automation id, entity_id or alias; 'all' for everything.",
                },
                "limit": {"type": "integer", "description": "How many runs (default 3)."},
            }
        ),
        handler=tool_get_trace,
        domain=DOMAIN,
    )


__all__ = [
    "DOMAIN",
    "EVENT_TRACE_RECORDED",
    "RunTrace",
    "StepTrace",
    "TraceRecorder",
    "async_setup",
    "get_trace",
]
