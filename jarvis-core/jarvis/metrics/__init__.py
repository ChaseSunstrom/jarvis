"""Where a graph's numbers come from.

A dashboard widget asks one question — *"this series, over this window"* — and
must not care whether the answer comes from Jarvis's own recorder, from an
InfluxDB in the garage, or from a file. So there is one shape:

    source.list_series()                 what can be graphed
    await source.query(series, window)   the points

Two rules the shape exists to enforce:

**A source never invents a point.** If it has nothing for a window it returns an
empty series, and the widget draws "no data" rather than a flat line at zero. A
graph that cannot tell "nothing happened" from "nothing was recorded" is worse
than no graph.

**Every source is local.** The internal source reads the recorder; the InfluxDB
source reads an instance the operator runs. Nothing here reaches a cloud
service, and `metrics: sources:` is the whole list of what may be reached.

## Windows, and why the step matters

A query is `(start, end, step)`. The step is not decoration: asking for six
hours of a sensor that changed 40,000 times must not send 40,000 points to a
browser that has 400 pixels to draw them in. Every source downsamples to the
step it was asked for, and says how (`aggregate`), because a maximum and a mean
look identical on a chart and mean different things.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "AGGREGATES",
    "DataSource",
    "Point",
    "Series",
    "SeriesInfo",
    "Window",
    "downsample",
    "registry_for",
]

#: How several points inside one step become one point. `last` is the default
#: for a state (a light is on or off; averaging that is meaningless); `mean` is
#: the default for a measurement.
AGGREGATES = ("last", "mean", "min", "max", "sum", "count")

#: The most points any query may return. A browser draws a few hundred pixels
#: wide; anything past this is bytes nobody can see, and the cap is here rather
#: than in each source so that a new source cannot forget it.
MAX_POINTS = 1500


@dataclass(frozen=True)
class Window:
    """The span a widget is asking about, and how finely."""

    #: Epoch seconds.
    start: float
    end: float
    #: Seconds per point. Zero means "choose one for me".
    step: float = 0.0

    @property
    def span(self) -> float:
        return max(0.0, self.end - self.start)

    def resolved_step(self, target_points: int = 300) -> float:
        """A step that keeps the answer under [MAX_POINTS]."""
        if self.step > 0:
            return max(self.step, self.span / MAX_POINTS if self.span else self.step)
        if self.span <= 0:
            return 60.0
        return max(1.0, self.span / max(1, target_points))

    @classmethod
    def last(cls, seconds: float, *, step: float = 0.0, now: float | None = None) -> "Window":
        end = time.time() if now is None else now
        return cls(start=end - seconds, end=end, step=step)


@dataclass(frozen=True)
class Point:
    """One sample. `value` is None where the source has nothing — never zero."""

    at: float
    value: float | None


@dataclass
class SeriesInfo:
    """One thing that can be graphed, as a picker would list it."""

    #: Unique within its source, e.g. `sensor.office_temperature`.
    key: str
    #: What a person calls it.
    label: str
    #: "°C", "tok/s", "W" — drawn on the axis, never guessed at.
    unit: str = ""
    #: Free text: "room", "model", "host". The picker groups by it.
    group: str = ""
    #: Which aggregate suits it (see [AGGREGATES]).
    default_aggregate: str = "mean"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "group": self.group,
            "default_aggregate": self.default_aggregate,
        }


@dataclass
class Series:
    """The answer to one query."""

    key: str
    label: str = ""
    unit: str = ""
    points: list[Point] = field(default_factory=list)
    #: How several samples inside one step were combined.
    aggregate: str = "mean"
    #: Set when the source could not answer. The widget shows this instead of a
    #: line, because an empty chart with no explanation reads as "zero".
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label or self.key,
            "unit": self.unit,
            "aggregate": self.aggregate,
            "error": self.error,
            # [at, value] pairs: half the bytes of {"at": …, "value": …} and the
            # console has to iterate them anyway.
            "points": [[round(p.at, 3), p.value] for p in self.points],
        }


@runtime_checkable
class DataSource(Protocol):
    """What a dashboard can graph. Implemented by `sources/*.py`."""

    #: Stable id used in a saved layout, e.g. "internal", "influx".
    name: str
    #: One line for the picker: what this source is.
    description: str

    async def list_series(self) -> list[SeriesInfo]:
        """Everything this source can answer about, for the picker."""

    async def query(self, keys: list[str], window: Window, aggregate: str = "") -> list[Series]:
        """The points for those keys. Never invents one."""

    async def healthy(self) -> tuple[bool, str]:
        """Is it reachable? `(False, "why not")` is shown in the picker."""


def downsample(
    raw: list[tuple[float, float | None]], window: Window, aggregate: str = "mean"
) -> list[Point]:
    """Fold `(at, value)` samples into one point per step.

    Buckets with no sample become a point with `value=None` rather than being
    dropped: a gap in a series is information — the recorder was down, the
    sensor stopped reporting — and a chart that silently joins across it draws a
    line through time that never happened.
    """
    step = window.resolved_step()
    if step <= 0 or window.span <= 0:
        return [Point(at=at, value=value) for at, value in raw]
    if aggregate not in AGGREGATES:
        aggregate = "mean"

    buckets: dict[int, list[float]] = {}
    for at, value in raw:
        if value is None or at < window.start or at > window.end:
            continue
        buckets.setdefault(int((at - window.start) // step), []).append(float(value))

    # Buckets cover [start, end): a trailing bucket at exactly `end` would be a
    # point in a window nothing was asked about, and it always reads as a gap.
    total = min(MAX_POINTS, max(1, math.ceil(window.span / step)))
    points: list[Point] = []
    for index in range(total):
        values = buckets.get(index)
        at = window.start + index * step
        if not values:
            points.append(Point(at=at, value=None))
            continue
        if aggregate == "last":
            value = values[-1]
        elif aggregate == "min":
            value = min(values)
        elif aggregate == "max":
            value = max(values)
        elif aggregate == "sum":
            value = sum(values)
        elif aggregate == "count":
            value = float(len(values))
        else:
            value = sum(values) / len(values)
        points.append(Point(at=at, value=value))
    return points


def registry_for(jarvis: Any) -> dict[str, DataSource]:
    """The sources this instance has, by name. Empty when metrics is not set up."""
    sources = getattr(jarvis, "data", {}).get("metrics_sources")
    return sources if isinstance(sources, dict) else {}
