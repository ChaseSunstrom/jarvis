"""Traces: grouped by the context that already existed, and never fatal.

The whole design rests on one thing being true — every bus event carries a
`Context` with an id and a parent — so most of these tests are about the
grouping, the bounds, and the two ways a recorder could hurt the thing it is
watching: raising, or growing without limit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.bus import Context, Event
from jarvis.integrations.observability import Recorder, Trace


class FakeJarvis:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = str(config_dir)
        self.data: dict = {}


def recorder(tmp_path, **kwargs) -> Recorder:
    return Recorder(FakeJarvis(tmp_path), **kwargs)


def event(name: str, data: dict, context: Context | None = None) -> Event:
    return Event(name, data, context=context or Context(origin="llm"))


def test_a_tool_becomes_a_span_with_a_duration(tmp_path):
    rec = recorder(tmp_path)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_tool_started", {"name": "get_state"}, ctx))
    rec.on_event(event("jarvis_tool_finished", {"name": "get_state", "ok": True}, ctx))
    (trace,) = rec.traces.values()
    (span,) = trace.spans
    assert span.kind == "tool" and span.name == "get_state" and span.ok
    assert span.ms is not None and span.ms >= 0


def test_everything_in_one_turn_lands_in_one_trace(tmp_path):
    """The context id is the trace id. That is the whole correlation story."""
    rec = recorder(tmp_path)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_tool_started", {"name": "recall"}, ctx))
    rec.on_event(event("jarvis_tool_finished", {"name": "recall", "ok": True}, ctx))
    rec.on_event(event("jarvis_model_call", {"model": "qwen", "ms": 400,
                                             "prompt_tokens": 900, "completion_tokens": 40}, ctx))
    assert len(rec.traces) == 1
    totals = next(iter(rec.traces.values())).totals()
    assert totals["tools"] == 1 and totals["model_calls"] == 1
    assert totals["prompt_tokens"] == 900 and totals["completion_tokens"] == 40


def test_a_child_context_joins_its_parents_trace(tmp_path):
    """A subagent's work belongs to the turn that asked for it."""
    rec = recorder(tmp_path)
    parent = Context(origin="llm")
    child = Context(origin="llm", parent_id=parent.id)
    rec.on_event(event("jarvis_tool_started", {"name": "delegate_to_agents"}, parent))
    rec.on_event(event("jarvis_model_call", {"model": "qwen", "ms": 10}, child))
    assert len(rec.traces) == 1


def test_a_failed_tool_is_recorded_as_failed(tmp_path):
    rec = recorder(tmp_path)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_tool_started", {"name": "web_fetch"}, ctx))
    rec.on_event(event("jarvis_tool_finished",
                       {"name": "web_fetch", "ok": False, "error": "refused: loopback"}, ctx))
    (trace,) = rec.traces.values()
    assert trace.totals()["errors"] == 1
    assert "loopback" in trace.spans[0].error


def test_a_finish_with_no_start_is_still_recorded(tmp_path):
    """"This happened and we never saw it begin" is a real thing to know."""
    rec = recorder(tmp_path)
    rec.on_event(event("jarvis_tool_finished", {"name": "ghost", "ok": True}))
    (trace,) = rec.traces.values()
    assert trace.spans[0].name == "ghost"


def test_spans_are_bounded_and_the_truncation_is_counted(tmp_path):
    """A runaway loop must not eat the heap, and must not lie about it either."""
    rec = recorder(tmp_path, max_spans=5)
    ctx = Context(origin="llm")
    for index in range(50):
        rec.on_event(event("jarvis_model_call", {"model": f"m{index}", "ms": 1}, ctx))
    (trace,) = rec.traces.values()
    assert len(trace.spans) == 5
    assert trace.truncated == 45
    assert trace.summary()["truncated"] == 45


def test_traces_are_bounded_and_the_oldest_goes_first(tmp_path):
    rec = recorder(tmp_path, max_traces=3)
    for index in range(10):
        rec.on_event(event("jarvis_model_call", {"model": "m", "ms": 1}, Context(origin="llm")))
    assert len(rec.traces) == 3


def test_a_broken_event_cannot_break_a_turn(tmp_path):
    """The recorder is a listener on the hot path. It never raises."""
    rec = recorder(tmp_path)

    class Exploding:
        event_type = "jarvis_tool_started"
        context = Context()

        @property
        def data(self):
            raise RuntimeError("boom")

    rec.on_event(Exploding())  # must not raise


def test_a_finished_task_closes_its_trace_and_writes_it_down(tmp_path):
    rec = recorder(tmp_path)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_task_created",
                       {"task": {"id": "t1", "title": "research the boiler", "status": "running"}},
                       ctx))
    rec.on_event(event("jarvis_model_call", {"model": "qwen", "ms": 5}, ctx))
    rec.on_event(event("jarvis_task_updated",
                       {"task": {"id": "t1", "title": "research the boiler", "status": "done"}},
                       ctx))
    (trace,) = rec.traces.values()
    assert trace.ended is not None and trace.ms is not None
    written = list((tmp_path / "traces").glob("*.jsonl"))
    assert written, "a finished trace was not written to disk"
    row = json.loads(written[0].read_text().splitlines()[0])
    assert row["task_id"] == "t1" and row["label"] == "research the boiler"


def test_the_link_from_a_task_finds_its_trace(tmp_path):
    """What the UI's "view trace" has is a task id, not a context id."""
    rec = recorder(tmp_path)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_task_created", {"task": {"id": "t9", "status": "running"}}, ctx))
    assert rec.for_task("t9") == ctx.id
    assert rec.for_task("nothing-like-it") == ""


def test_a_span_payload_is_clipped_rather_than_dropped(tmp_path):
    """A tool argument is a value the MODEL chose the size of."""
    rec = recorder(tmp_path)
    rec.on_event(event("jarvis_tool_started", {"name": "note_create", "body": "x" * 5000}))
    (trace,) = rec.traces.values()
    body = trace.spans[0].data["body"]
    assert len(body) < 300 and body.endswith("…")


def test_disk_writing_can_be_turned_off(tmp_path):
    rec = recorder(tmp_path, to_disk=False)
    ctx = Context(origin="llm")
    rec.on_event(event("jarvis_task_created", {"task": {"id": "t", "status": "running"}}, ctx))
    rec.on_event(event("jarvis_task_updated", {"task": {"id": "t", "status": "done"}}, ctx))
    assert not (tmp_path / "traces").exists()


@pytest.mark.parametrize("origin", ["user", "llm", "automation", "api"])
def test_the_listing_can_be_filtered_by_where_it_came_from(tmp_path, origin):
    rec = recorder(tmp_path)
    for name in ("user", "llm", "automation", "api"):
        rec.on_event(event("jarvis_model_call", {"model": "m", "ms": 1}, Context(origin=name)))
    rows = rec.listing(kind=origin)
    assert len(rows) == 1 and rows[0]["origin"] == origin


def test_totals_add_up_what_a_turn_cost(tmp_path):
    trace = Trace(id="t")
    from jarvis.integrations.observability import Span

    trace.spans = [
        Span("model", "qwen", 0, 1, data={"prompt_tokens": 100, "completion_tokens": 10}),
        Span("model", "qwen", 0, 2, data={"prompt_tokens": 200, "completion_tokens": 20}),
        Span("tool", "get_state", 0, 0.5),
    ]
    totals = trace.totals()
    assert totals["prompt_tokens"] == 300 and totals["completion_tokens"] == 30
    assert totals["model_calls"] == 2 and totals["tools"] == 1
