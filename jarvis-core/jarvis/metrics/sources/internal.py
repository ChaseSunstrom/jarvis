"""Jarvis, graphing itself.

Everything here is already in this process or on this disk: the recorder's
history of every entity, the host's own load and disk, and the counters the
assistant keeps about its work — turns, tool calls, tasks. No network, no
second database, nothing to install.

## Three families, because they answer different questions

``entity.*``   the recorder's series for one entity. "How warm was the office"
               is a question about a sensor, and the recorder has already been
               writing it down for ten days.
``host.*``     load, memory and disk, read from ``/proc`` and ``statvfs``.
               Sampled here rather than taken from an entity so that a graph of
               "is this machine coping" works on an install with no sensors
               configured at all.
``jarvis.*``   what the assistant has been doing: turns, tool calls, task
               outcomes, first-token latency. Counted from the bus as it
               happens and kept in a ring buffer — the recorder stores states,
               and none of these is a state.

The ring buffers are bounded and in memory: a restart loses them, which is the
honest trade for not writing a second database. What is worth keeping across a
restart is a state, and a state belongs in the recorder.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from .. import AGGREGATES, DataSource, Point, Series, SeriesInfo, Window, downsample

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: How many samples of each in-memory series to keep. At one sample a second
#: that is roughly two hours; the recorder is where anything longer belongs.
RING = 7200


class Ring:
    """A bounded series of `(at, value)`, oldest first."""

    def __init__(self, label: str, unit: str = "", aggregate: str = "mean") -> None:
        self.label = label
        self.unit = unit
        self.aggregate = aggregate
        self.samples: deque[tuple[float, float]] = deque(maxlen=RING)

    def add(self, value: float, at: float | None = None) -> None:
        self.samples.append((time.time() if at is None else at, float(value)))

    def bump(self, by: float = 1.0) -> None:
        """A counter's increment, recorded as an event at a point in time.

        Stored as the increment rather than a running total so that `sum` over a
        window answers "how many in this hour" — which is the question — and a
        restart does not draw a cliff from 4,000 back to zero.
        """
        self.add(by)


class InternalSource:
    """Jarvis's own numbers. Implements :class:`~jarvis.metrics.DataSource`."""

    name = "internal"
    description = "Jarvis itself: entity history, this host, and the assistant's own work."

    def __init__(self, jarvis: "Jarvis") -> None:
        self.jarvis = jarvis
        self.rings: dict[str, Ring] = {
            "jarvis.turns": Ring("Turns", "turns", "sum"),
            "jarvis.tool_calls": Ring("Tool calls", "calls", "sum"),
            "jarvis.tool_ms": Ring("Tool call duration", "ms", "mean"),
            "jarvis.tasks_started": Ring("Tasks started", "tasks", "sum"),
            "jarvis.tasks_failed": Ring("Tasks failed", "tasks", "sum"),
            "jarvis.first_token_ms": Ring("First token", "ms", "mean"),
        }
        self._host_cache: tuple[float, dict[str, float]] = (0.0, {})

    # --- listening to the bus ------------------------------------------------
    def attach(self) -> None:
        """Count what happens, as it happens.

        Deliberately a handful of listeners rather than a general "record every
        event": a graph of every bus event is a graph of the bus, and nobody has
        ever wanted one.
        """
        bus = getattr(self.jarvis, "bus", None)
        if bus is None:  # pragma: no cover - core always builds one
            return
        bus.listen("jarvis_tool_finished", self._on_tool_finished)
        bus.listen("jarvis_task_tool_finished", self._on_tool_finished)
        bus.listen("jarvis_task_added", lambda _e: self.rings["jarvis.tasks_started"].bump())
        bus.listen("jarvis_task_updated", self._on_task_updated)
        bus.listen("voice_pipeline_event", self._on_pipeline)

    def _data(self, event: Any) -> dict[str, Any]:
        data = getattr(event, "data", event)
        return data if isinstance(data, dict) else {}

    def _on_tool_finished(self, event: Any) -> None:
        data = self._data(event)
        self.rings["jarvis.tool_calls"].bump()
        duration = data.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            self.rings["jarvis.tool_ms"].add(float(duration))

    def _on_task_updated(self, event: Any) -> None:
        task = self._data(event).get("task")
        if isinstance(task, dict) and task.get("status") == "error":
            self.rings["jarvis.tasks_failed"].bump()

    def _on_pipeline(self, event: Any) -> None:
        data = self._data(event)
        if data.get("type") == "run-end":
            self.rings["jarvis.turns"].bump()
        if data.get("type") == "intent-progress":
            inner = data.get("data")
            latency = inner.get("first_token_ms") if isinstance(inner, dict) else None
            if isinstance(latency, (int, float)) and latency > 0:
                self.rings["jarvis.first_token_ms"].add(float(latency))

    # --- the host ------------------------------------------------------------
    def _host(self) -> dict[str, float]:
        """Load, memory and disk, from /proc and statvfs. Cached for a second."""
        now = time.time()
        cached_at, cached = self._host_cache
        if now - cached_at < 1.0 and cached:
            return cached
        out: dict[str, float] = {}
        try:
            one, five, fifteen = os.getloadavg()
            out["host.load1"] = one
            out["host.load5"] = five
            out["host.load15"] = fifteen
        except OSError:  # pragma: no cover - not every platform has it
            pass
        try:
            fields: dict[str, float] = {}
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    fields[key] = float(rest.strip().split()[0]) * 1024
            total, available = fields.get("MemTotal", 0.0), fields.get("MemAvailable", 0.0)
            if total:
                out["host.memory_used"] = (total - available) / 1e9
                out["host.memory_percent"] = 100.0 * (total - available) / total
        except (OSError, ValueError, IndexError):  # pragma: no cover
            pass
        try:
            stat = os.statvfs(str(getattr(self.jarvis, "config_dir", "/")))
            out["host.disk_free"] = stat.f_bavail * stat.f_frsize / 1e9
        except OSError:  # pragma: no cover
            pass
        self._host_cache = (now, out)
        return out

    # --- the DataSource protocol --------------------------------------------
    async def healthy(self) -> tuple[bool, str]:
        return True, ""

    async def list_series(self) -> list[SeriesInfo]:
        out = [
            SeriesInfo(
                key=key,
                label=ring.label,
                unit=ring.unit,
                group="jarvis",
                default_aggregate=ring.aggregate,
            )
            for key, ring in self.rings.items()
        ]
        host_labels = {
            "host.load1": ("Load, 1 minute", ""),
            "host.load5": ("Load, 5 minutes", ""),
            "host.load15": ("Load, 15 minutes", ""),
            "host.memory_used": ("Memory used", "GB"),
            "host.memory_percent": ("Memory used", "%"),
            "host.disk_free": ("Disk free", "GB"),
        }
        for key in self._host():
            label, unit = host_labels.get(key, (key, ""))
            out.append(SeriesInfo(key=key, label=label, unit=unit, group="host"))

        states = getattr(self.jarvis, "states", None)
        for state in getattr(states, "all", list)():
            entity_id = getattr(state, "entity_id", "")
            if not entity_id:
                continue
            try:
                float(getattr(state, "state", ""))
            except (TypeError, ValueError):
                # Only numeric entities: a graph of "on"/"off" over time is a
                # different chart (a state timeline) and pretending it is a line
                # would draw nothing.
                continue
            attributes = getattr(state, "attributes", {}) or {}
            out.append(
                SeriesInfo(
                    key=f"entity.{entity_id}",
                    label=str(attributes.get("friendly_name") or entity_id),
                    unit=str(attributes.get("unit_of_measurement") or ""),
                    group="entity",
                )
            )
        return out

    async def query(self, keys: list[str], window: Window, aggregate: str = "") -> list[Series]:
        out: list[Series] = []
        host = self._host()
        for key in keys[:20]:
            if key in self.rings:
                ring = self.rings[key]
                how = aggregate if aggregate in AGGREGATES else ring.aggregate
                out.append(
                    Series(
                        key=key,
                        label=ring.label,
                        unit=ring.unit,
                        aggregate=how,
                        points=downsample(list(ring.samples), window, how),
                    )
                )
            elif key.startswith("host."):
                # One live reading, at the right-hand edge: /proc has no past.
                value = host.get(key)
                out.append(
                    Series(
                        key=key,
                        label=key,
                        aggregate="last",
                        points=[Point(at=window.end, value=value)] if value is not None else [],
                        error="" if value is not None else "this host does not report it",
                    )
                )
            elif key.startswith("entity."):
                out.append(await self._entity_series(key[len("entity.") :], window, aggregate))
            else:
                out.append(Series(key=key, error=f"no series called {key!r}"))
        return out

    async def _entity_series(self, entity_id: str, window: Window, aggregate: str) -> Series:
        recorder = getattr(self.jarvis, "data", {}).get("recorder")
        history = getattr(recorder, "async_history_period", None)
        if history is None:
            return Series(key=f"entity.{entity_id}", error="the recorder is not running")
        try:
            rows = await history([entity_id], window.start, window.end)
        except Exception as err:  # pragma: no cover - a query is not a crash
            _LOGGER.debug("history for %s failed: %s", entity_id, err)
            return Series(key=f"entity.{entity_id}", error=f"{type(err).__name__}: {err}"[:200])

        samples: list[tuple[float, float | None]] = []
        unit = ""
        for series in rows or []:
            for row in series or []:
                if not isinstance(row, dict):
                    continue
                unit = unit or str((row.get("attributes") or {}).get("unit_of_measurement") or "")
                try:
                    value = float(row.get("state"))
                except (TypeError, ValueError):
                    continue
                at = row.get("last_updated") or row.get("last_changed") or 0
                try:
                    samples.append((float(at), value))
                except (TypeError, ValueError):
                    continue
        how = aggregate if aggregate in AGGREGATES else "mean"
        return Series(
            key=f"entity.{entity_id}",
            label=entity_id,
            unit=unit,
            aggregate=how,
            points=downsample(samples, window, how),
        )


def build(jarvis: "Jarvis") -> DataSource:
    source = InternalSource(jarvis)
    source.attach()
    return source
