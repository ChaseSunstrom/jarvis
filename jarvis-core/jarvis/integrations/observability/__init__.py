"""observability — what the agent did, step by step, and what each step cost.

    observability:
      max_traces: 200      # kept in memory, newest first
      max_spans: 400       # per trace, before it truncates
      to_disk: true        # append finished traces to <config>/traces/<date>.jsonl

## Why this is not Langfuse

Langfuse is the obvious answer and it was costed before it was rejected. Its
own self-hosting guide asks for **4 cores and 16 GiB**, and the compose file is
Postgres *and* ClickHouse *and* Redis *and* MinIO *and* two Langfuse
containers. This host has four cores and sixteen gigabytes in total, most of
which is the assistant. `docs/TOOLING_DECISIONS.md` §6 carries the numbers.

What Langfuse would have bought is a UI over data this process already
generates and throws away. So the data stops being thrown away.

## How it works, and why it needed no new plumbing

Every event on the bus already carries a `Context` with an `id`, a `parent_id`
and an `origin` — put there so an automation could say what triggered it. That
is exactly a trace and a span: this integration subscribes to the lifecycle
events the agent already fires, groups them by context id, nests them by parent
id, and pairs each `*_started` with its `*_finished`.

    trace  = one context tree: a spoken turn, a research run, a coding job
    span   = a tool call, a model call, an approval, a subagent, a task step

Nothing in `llm/tools.py` or the agent loop knows this exists. The one addition
anywhere else is `jarvis_model_call`, fired after each exchange with the model,
because token counts and time-to-answer are the two numbers that are otherwise
lost the moment the stream closes.

## What it costs

Bounded on both axes and never on the hot path: a span is a dict append, the
trace list is capped at `max_traces`, and each trace is capped at `max_spans`.
Writing to disk happens when a trace ENDS, one line of JSON, so a running
conversation never waits for a file.

Services
    ``observability.get``    (trace_id) → the whole trace
    ``observability.list``   (limit, kind) → one summary per trace
    ``observability.clear``

Websocket: ``jarvis/traces/list``, ``jarvis/traces/get``.
REST: ``GET /api/traces``, ``GET /api/traces/{trace_id}``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "observability"

DEFAULT_MAX_TRACES = 200
DEFAULT_MAX_SPANS = 400

#: The event pairs that become spans. `(start, end, kind, name key)` — a start
#: opens a span and the matching end closes the newest open one of that kind.
SPAN_EVENTS = (
    ("jarvis_tool_started", "jarvis_tool_finished", "tool", "name"),
    ("jarvis_approval_required", "jarvis_approval_resolved", "approval", "tool"),
)

#: Events that are a span all by themselves — they report something that has
#: already happened, with its own duration.
POINT_EVENTS = {
    "jarvis_model_call": "model",
    "jarvis_task_child_added": "subagent",
    "jarvis_tool_called": "tool-inline",
    # The agent's claimed-action guard and a run stopped at the server (M102):
    # in the trace, so "why did you say that?" and the nightly review read them.
    "jarvis_turn_guarded": "guard",
    "jarvis_run_stopped": "stop",
}

#: Fired by the agent after each exchange with the model. Carries what the
#: stream knows and nothing else can reconstruct: which model, how long, and
#: how many tokens each way.
EVENT_MODEL_CALL = "jarvis_model_call"


@dataclass
class Span:
    kind: str
    name: str
    started: float
    ended: float | None = None
    ok: bool = True
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ms(self) -> float | None:
        if self.ended is None:
            return None
        return round((self.ended - self.started) * 1000, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "started": round(self.started, 3),
            "ms": self.ms,
            "ok": self.ok,
            "error": self.error or None,
            "data": self.data,
        }


@dataclass
class Trace:
    """One context tree, with everything that happened under it."""

    id: str
    origin: str = "internal"
    started: float = field(default_factory=time.time)
    ended: float | None = None
    label: str = ""
    task_id: str = ""
    spans: list[Span] = field(default_factory=list)
    truncated: int = 0

    @property
    def ms(self) -> float | None:
        if self.ended is None:
            return None
        return round((self.ended - self.started) * 1000, 1)

    def totals(self) -> dict[str, Any]:
        tools = [s for s in self.spans if s.kind in ("tool", "tool-inline")]
        models = [s for s in self.spans if s.kind == "model"]
        return {
            "spans": len(self.spans),
            "tools": len(tools),
            "model_calls": len(models),
            "prompt_tokens": sum(int(s.data.get("prompt_tokens") or 0) for s in models),
            "completion_tokens": sum(int(s.data.get("completion_tokens") or 0) for s in models),
            "model_ms": round(sum(s.ms or 0 for s in models), 1),
            "tool_ms": round(sum(s.ms or 0 for s in tools), 1),
            "errors": sum(1 for s in self.spans if not s.ok),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "origin": self.origin,
            "label": self.label,
            "task_id": self.task_id or None,
            "started": round(self.started, 3),
            "ms": self.ms,
            "truncated": self.truncated,
            **self.totals(),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.summary(), "spans": [span.as_dict() for span in self.spans]}


class Recorder:
    """Every trace this process has seen lately, newest last."""

    def __init__(
        self,
        jarvis: "Jarvis",
        max_traces: int = DEFAULT_MAX_TRACES,
        max_spans: int = DEFAULT_MAX_SPANS,
        to_disk: bool = True,
    ) -> None:
        self.jarvis = jarvis
        self.max_traces = max(1, int(max_traces))
        self.max_spans = max(1, int(max_spans))
        self.to_disk = bool(to_disk)
        self.traces: dict[str, Trace] = {}
        self._unsubscribe: list[Any] = []

    # --- reading ----------------------------------------------------------
    def get(self, trace_id: str) -> dict[str, Any] | None:
        trace = self.traces.get(str(trace_id))
        return trace.as_dict() if trace is not None else None

    def listing(self, limit: int = 50, kind: str = "") -> list[dict[str, Any]]:
        rows = [t.summary() for t in self.traces.values()]
        if kind:
            rows = [r for r in rows if r["origin"] == kind]
        rows.sort(key=lambda row: -row["started"])
        return rows[: max(1, int(limit))]

    def for_task(self, task_id: str) -> str:
        """The trace id covering a task, or "" — what the UI's link needs."""
        for trace in reversed(list(self.traces.values())):
            if trace.task_id == str(task_id):
                return trace.id
        return ""

    def clear(self) -> int:
        count = len(self.traces)
        self.traces.clear()
        return count

    # --- recording --------------------------------------------------------
    def _trace_for(self, context: Any, origin_hint: str = "") -> Trace:
        trace_id = str(getattr(context, "parent_id", None) or getattr(context, "id", "") or "-")
        trace = self.traces.get(trace_id)
        if trace is None:
            trace = Trace(
                id=trace_id,
                origin=str(getattr(context, "origin", "") or origin_hint or "internal"),
            )
            self.traces[trace_id] = trace
            while len(self.traces) > self.max_traces:
                # Oldest first: a dict preserves insertion order, and the
                # oldest trace is the one least likely to be looked at.
                self.traces.pop(next(iter(self.traces)))
        return trace

    def _add(self, trace: Trace, span: Span) -> None:
        if len(trace.spans) >= self.max_spans:
            trace.truncated += 1
            return
        trace.spans.append(span)

    def _open(self, trace: Trace, kind: str, name: str, data: dict[str, Any]) -> None:
        self._add(trace, Span(kind=kind, name=name, started=time.time(), data=data))

    def _close(self, trace: Trace, kind: str, name: str, ok: bool, error: str) -> None:
        for span in reversed(trace.spans):
            if span.kind == kind and span.name == name and span.ended is None:
                span.ended = time.time()
                span.ok = ok
                span.error = error
                return
        # A finish with no start: still worth recording, because "this happened
        # and we never saw it begin" is a real thing to know.
        self._add(
            trace,
            Span(kind=kind, name=name, started=time.time(), ended=time.time(), ok=ok, error=error),
        )

    def on_event(self, event: Any) -> None:
        """One bus event. Never raises: a recorder must not break a turn."""
        try:
            self._record(event)
        except Exception:  # pragma: no cover - observability is never fatal
            _LOGGER.exception("Could not record %s", getattr(event, "event_type", "?"))

    def _record(self, event: Any) -> None:
        name = str(getattr(event, "event_type", "") or "")
        data = dict(getattr(event, "data", {}) or {})
        context = getattr(event, "context", None)

        for start, end, kind, key in SPAN_EVENTS:
            if name == start:
                trace = self._trace_for(context)
                trace.label = trace.label or str(data.get(key) or kind)
                self._open(trace, kind, str(data.get(key) or "?"), _small(data))
                return
            if name == end:
                trace = self._trace_for(context)
                ok = bool(data.get("ok", data.get("approved", True)))
                self._close(trace, kind, str(data.get(key) or "?"), ok, str(data.get("error") or ""))
                if not ok:
                    trace.label = trace.label or str(data.get(key) or kind)
                return

        kind = POINT_EVENTS.get(name)
        if kind is not None:
            trace = self._trace_for(context)
            started = time.time() - float(data.get("ms") or 0) / 1000
            span = Span(
                kind=kind,
                name=str(data.get("model") or data.get("name") or data.get("agent") or kind),
                started=started,
                ended=time.time(),
                ok=bool(data.get("ok", True)),
                error=str(data.get("error") or ""),
                data=_small(data),
            )
            self._add(trace, span)
            return

        if name in ("jarvis_task_created", "jarvis_task_updated"):
            task = data.get("task") if isinstance(data.get("task"), dict) else data
            task_id = str(task.get("id") or "")
            if not task_id:
                return
            trace = self._trace_for(context)
            trace.task_id = trace.task_id or task_id
            trace.label = trace.label or str(task.get("title") or "")
            status = str(task.get("status") or "")
            if status in ("done", "error", "cancelled"):
                self._finish(trace)

    def _finish(self, trace: Trace) -> None:
        if trace.ended is not None:
            return
        trace.ended = time.time()
        if self.to_disk:
            self._append(trace)

    def _append(self, trace: Trace) -> None:
        try:
            directory = Path(self.jarvis.config_dir) / "traces"
            directory.mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d", time.localtime(trace.started))
            with (directory / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(trace.as_dict(), separators=(",", ":")) + "\n")
        except Exception:  # pragma: no cover - a full disk must not end a turn
            _LOGGER.exception("Could not write a trace to disk")


def _small(data: dict[str, Any], limit: int = 200) -> dict[str, Any]:
    """A payload small enough to keep hundreds of, with no secrets in it.

    Clipped rather than dropped, and redacted rather than trusted: a trace is
    written to disk and read by whoever can read the config directory, and a
    tool's arguments are exactly where a credential would be if one were ever
    passed as one. `security/secrets.py` says why this is by value.
    """
    from ...security.secrets import redact

    data = redact(data)
    out: dict[str, Any] = {}
    for key, value in list(data.items())[:16]:
        if key in ("context", "task"):
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            text = str(value)
            out[key] = text if len(text) <= limit else text[:limit] + "…"
    return out


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}
    recorder = Recorder(
        jarvis,
        max_traces=int(options.get("max_traces") or DEFAULT_MAX_TRACES),
        max_spans=int(options.get("max_spans") or DEFAULT_MAX_SPANS),
        to_disk=bool(options.get("to_disk", True)),
    )
    jarvis.data[DOMAIN] = recorder

    watched = {start for start, _e, _k, _n in SPAN_EVENTS}
    watched |= {end for _s, end, _k, _n in SPAN_EVENTS}
    watched |= set(POINT_EVENTS)
    watched |= {"jarvis_task_created", "jarvis_task_updated"}
    for event_type in sorted(watched):
        recorder._unsubscribe.append(jarvis.bus.listen(event_type, recorder.on_event))

    async def _get(call: Any) -> dict[str, Any]:
        trace = recorder.get(str(call.data.get("trace_id") or ""))
        return {"trace": trace}

    async def _list(call: Any) -> dict[str, Any]:
        return {
            "traces": recorder.listing(
                limit=int(call.data.get("limit") or 50),
                kind=str(call.data.get("kind") or ""),
            )
        }

    async def _clear(_call: Any) -> dict[str, Any]:
        return {"cleared": recorder.clear()}

    jarvis.services.register(
        DOMAIN, "get", _get,
        description="One trace, with every span under it.",
        fields={"trace_id": {"description": "the trace to fetch"}},
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN, "list", _list,
        description="Recent traces, newest first, one summary line each.",
        fields={"limit": {"description": "how many"},
                "kind": {"description": "filter by origin: user, llm, automation, api"}},
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN, "clear", _clear, description="Forget every trace held in memory.",
        supports_response=True,
    )
    _LOGGER.info(
        "Observability recording: %d traces x %d spans, disk %s",
        recorder.max_traces, recorder.max_spans, "on" if recorder.to_disk else "off",
    )
    return True
