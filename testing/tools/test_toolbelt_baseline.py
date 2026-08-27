"""The tape measure, tested — because a comparison that flatters is worse than none.

`scripts/verify/toolbelt_baseline.py` is what decides whether a new service in
`docs/TOOLING_DECISIONS.md` earned its place. Everything here is offline: the
snapshots are dicts, and what is under test is the arithmetic of "worse".

    python3 -m pytest testing/tools -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "toolbelt_baseline", REPO / "scripts" / "verify" / "toolbelt_baseline.py"
)
tb = importlib.util.module_from_spec(_spec)
sys.modules["toolbelt_baseline"] = tb
_spec.loader.exec_module(tb)


def snap(**metrics):
    return {"metrics": metrics, "gaps": [], "unknown": []}


def test_nothing_moving_is_not_a_regression():
    same = snap(**{"intelligence.routing": 0.875, "latency.idle_total": 9.4})
    regressions, notes = tb.compare(same, same)
    assert regressions == [] and notes == []


def test_a_rate_that_dropped_by_one_case_is_a_regression():
    """No band on rates: eight prompts, so a drop is a prompt that broke."""
    before = snap(**{"intelligence.routing": 1.0})
    after = snap(**{"intelligence.routing": 0.875})
    regressions, _ = tb.compare(before, after)
    assert len(regressions) == 1 and "lower is worse" in regressions[0]


def test_a_rate_that_improved_is_a_note_not_a_failure():
    regressions, notes = tb.compare(
        snap(**{"intelligence.reasoning": 0.6}), snap(**{"intelligence.reasoning": 1.0})
    )
    assert regressions == []
    assert any("0.6 -> 1.0" in note for note in notes)


def test_latency_noise_does_not_fire_and_a_real_slowdown_does():
    """A shared four-vCPU box moves tens of percent between runs; 2x does not."""
    noise, _ = tb.compare(
        snap(**{"latency.idle_total": 9.4}), snap(**{"latency.idle_total": 11.0})
    )
    assert noise == []
    doubled, _ = tb.compare(
        snap(**{"latency.idle_total": 9.4}), snap(**{"latency.idle_total": 19.0})
    )
    assert len(doubled) == 1 and "allowed" in doubled[0]


def test_a_metric_that_stopped_being_measured_is_a_regression():
    """The usual way a change flatters itself: the eval that would fail it did not run."""
    regressions, _ = tb.compare(
        snap(**{"intelligence.routing": 1.0, "latency.idle_total": 9.0}),
        snap(**{"latency.idle_total": 9.0}),
    )
    assert len(regressions) == 1
    assert "NOT after" in regressions[0]


def test_a_brand_new_metric_is_a_note():
    regressions, notes = tb.compare(snap(), snap(**{"speech.wer_round_trip": 0.05}))
    assert regressions == []
    assert any("new in the second snapshot" in note for note in notes)


def test_every_metric_the_collector_can_produce_has_a_direction():
    """A metric with no direction cannot be judged, so it must not exist quietly."""
    card = {
        "context_retention": {"rate": 1.0}, "routing": {"rate": 1.0, "accuracy": 1.0},
        "reasoning": {"rate": 1.0}, "instructions": {"rate": 1.0},
        "graceful_failure": {"rate": 1.0},
        "speech": {"wer_mean": 0.05},
        "latency": {"idle": {"ttft": 5.0, "total": 7.0},
                    "under_load": {"ttft": 6.0, "total": 8.0}},
    }
    live = {"totals": {"scenarios": 10, "scenarios_passed": 10, "turns": 20,
                       "turns_passed": 20, "routing_accuracy": 1.0, "wer_mean": 0.01,
                       "round_trip_median": 3.8}}
    original = dict(tb.SOURCES)
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            for name, payload in (("scorecard", card), ("live", live)):
                path = Path(work) / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                tb.SOURCES[name] = path
            snapshot = tb.collect()
    finally:
        tb.SOURCES.clear()
        tb.SOURCES.update(original)
    assert snapshot["gaps"] == []
    assert snapshot["unknown"] == [], f"no DIRECTION for {snapshot['unknown']}"
    assert len(snapshot["metrics"]) == len(tb.DIRECTION)


def test_a_missing_eval_is_named_rather_than_silently_dropped():
    original = dict(tb.SOURCES)
    try:
        tb.SOURCES["scorecard"] = REPO / "does" / "not" / "exist.json"
        tb.SOURCES["live"] = REPO / "also" / "not" / "here.json"
        snapshot = tb.collect()
    finally:
        tb.SOURCES.clear()
        tb.SOURCES.update(original)
    assert len(snapshot["gaps"]) == 2
    assert snapshot["metrics"] == {}
