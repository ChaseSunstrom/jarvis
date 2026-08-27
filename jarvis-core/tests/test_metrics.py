"""Where a graph's numbers come from, and the two rules that matter.

**A source never invents a point.** A window with no data returns an empty
series or a gap, never a zero — a chart that cannot tell "nothing happened"
from "nothing was recorded" is worse than no chart.

**Nothing is sent that nobody can see.** A browser has a few hundred pixels;
asking for six hours of a sensor that changed 40,000 times must not put 40,000
points on the wire.
"""

from __future__ import annotations

import time

import pytest

from jarvis.metrics import MAX_POINTS, DataSource, Series, Window, downsample
from jarvis.metrics.sources.internal import InternalSource


class FakeBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    def listen(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)
        return lambda: None

    def fire(self, event, data):
        for handler in self.listeners.get(event, []):
            handler(type("Event", (), {"data": data})())


class FakeStates:
    def __init__(self, states):
        self._states = states

    def all(self):
        return self._states


class FakeState:
    def __init__(self, entity_id, state, attributes=None):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes or {}


class FakeJarvis:
    def __init__(self, states=(), recorder=None):
        self.bus = FakeBus()
        self.states = FakeStates(list(states))
        self.data = {"recorder": recorder} if recorder else {}
        self.config_dir = "/tmp"


# --- the window and the downsampler -----------------------------------------


def test_a_window_chooses_a_step_that_keeps_the_answer_drawable():
    window = Window.last(86400.0)  # a day
    step = window.resolved_step()
    assert 86400.0 / step <= MAX_POINTS
    assert step > 0


def test_an_explicit_step_is_honoured_until_it_would_flood_the_wire():
    fine = Window(start=0, end=3600, step=10)
    assert fine.resolved_step() == 10
    absurd = Window(start=0, end=86400 * 30, step=0.001)
    assert (absurd.span / absurd.resolved_step()) <= MAX_POINTS


def test_a_bucket_with_no_sample_is_a_gap_not_a_zero():
    window = Window(start=0, end=100, step=10)
    points = downsample([(5, 1.0), (95, 2.0)], window)
    values = [p.value for p in points]
    assert values[0] == 1.0
    assert values[-1] == 2.0
    # The middle is unknown, and says so.
    assert None in values
    assert 0.0 not in [v for v in values if v is not None and v == 0.0]


def test_each_aggregate_answers_a_different_question():
    window = Window(start=0, end=10, step=10)
    raw = [(1, 1.0), (2, 5.0), (3, 3.0)]
    assert downsample(raw, window, "min")[0].value == 1.0
    assert downsample(raw, window, "max")[0].value == 5.0
    assert downsample(raw, window, "sum")[0].value == 9.0
    assert downsample(raw, window, "count")[0].value == 3.0
    assert downsample(raw, window, "last")[0].value == 3.0
    assert downsample(raw, window, "mean")[0].value == pytest.approx(3.0)


def test_a_sample_outside_the_window_is_not_dragged_into_it():
    window = Window(start=100, end=200, step=50)
    points = downsample([(50, 99.0), (150, 1.0)], window)
    assert 99.0 not in [p.value for p in points]


def test_an_unknown_aggregate_falls_back_rather_than_raising():
    window = Window(start=0, end=10, step=10)
    assert downsample([(1, 2.0)], window, "median")[0].value == pytest.approx(2.0)


# --- the internal source -----------------------------------------------------


async def test_the_internal_source_is_a_data_source():
    source = InternalSource(FakeJarvis())
    assert isinstance(source, DataSource)
    healthy, why = await source.healthy()
    assert healthy and why == ""


async def test_it_counts_what_the_assistant_does():
    jarvis = FakeJarvis()
    source = InternalSource(jarvis)
    source.attach()

    jarvis.bus.fire("jarvis_tool_finished", {"name": "light.turn_on", "duration_ms": 84})
    jarvis.bus.fire("jarvis_task_tool_finished", {"name": "read_file", "duration_ms": 12})
    jarvis.bus.fire("voice_pipeline_event", {"type": "run-end"})

    window = Window.last(600.0)
    by_key = {s.key: s for s in await source.query(
        ["jarvis.tool_calls", "jarvis.tool_ms", "jarvis.turns"], window
    )}
    assert sum(p.value or 0 for p in by_key["jarvis.tool_calls"].points) == 2
    assert sum(p.value or 0 for p in by_key["jarvis.turns"].points) == 1
    assert max(p.value or 0 for p in by_key["jarvis.tool_ms"].points) >= 12


async def test_a_failed_task_is_counted_as_one():
    jarvis = FakeJarvis()
    source = InternalSource(jarvis)
    source.attach()
    jarvis.bus.fire("jarvis_task_updated", {"task": {"status": "error"}})
    jarvis.bus.fire("jarvis_task_updated", {"task": {"status": "running"}})
    series = (await source.query(["jarvis.tasks_failed"], Window.last(600.0)))[0]
    assert sum(p.value or 0 for p in series.points) == 1


async def test_it_lists_numeric_entities_and_leaves_the_rest_alone():
    jarvis = FakeJarvis(
        states=[
            FakeState("sensor.office_temperature", "21.4", {"unit_of_measurement": "°C"}),
            FakeState("light.kitchen", "on"),
        ]
    )
    keys = {info.key for info in await InternalSource(jarvis).list_series()}
    assert "entity.sensor.office_temperature" in keys
    # A line chart of "on"/"off" over time draws nothing; that is a different
    # chart, and pretending otherwise is how an empty graph gets shipped.
    assert "entity.light.kitchen" not in keys


async def test_an_entity_series_comes_from_the_recorder():
    now = time.time()

    class Recorder:
        async def async_history_period(self, ids, start, end):
            return [
                [
                    {"state": "21.0", "last_updated": now - 300, "attributes": {"unit_of_measurement": "°C"}},
                    {"state": "21.4", "last_updated": now - 60, "attributes": {}},
                ]
            ]

    source = InternalSource(FakeJarvis(recorder=Recorder()))
    series = (await source.query(["entity.sensor.office"], Window.last(600.0)))[0]
    assert series.unit == "°C"
    assert [p.value for p in series.points if p.value is not None]


async def test_no_recorder_is_an_answer_rather_than_a_crash():
    source = InternalSource(FakeJarvis())
    series = (await source.query(["entity.sensor.x"], Window.last(600.0)))[0]
    assert series.points == []
    assert "recorder" in series.error


async def test_an_unknown_series_says_so():
    source = InternalSource(FakeJarvis())
    series = (await source.query(["nonsense.key"], Window.last(60.0)))[0]
    assert "no series" in series.error


def test_a_series_on_the_wire_is_pairs_not_objects():
    """Half the bytes, and the console iterates them anyway."""
    from jarvis.metrics import Point

    payload = Series(key="k", points=[Point(at=1.0, value=2.0), Point(at=2.0, value=None)]).as_dict()
    assert payload["points"] == [[1.0, 2.0], [2.0, None]]
