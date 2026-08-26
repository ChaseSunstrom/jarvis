"""`dashboards` integration — graphs the user arranges, and the numbers behind them.

    metrics:
      sources:
        influx:                    # optional; see metrics/sources/influx.py
          url: !env_var INFLUX_URL

    dashboards:
      shipped: dashboards          # <config>/dashboards/*.yaml, read-only examples

Two halves, deliberately separate:

**Metrics** are where numbers come from. `jarvis/metrics.py` defines the shape
(`list_series`, `query`) and `metrics/sources/` implements it — `internal`
always, anything else because the operator configured it. A widget names a
source and a series and does not care which is which.

**Dashboards** are what somebody arranged. A layout is a list of widgets with
grid coordinates, saved under the token that saved it —
`tests/contracts/dashboard_layout.json` is the shape, read by this side and by
the console's tests.

A widget has a *kind* (M63). `metric` is a graph of recorded numbers and the
default when the field is absent, so every layout saved before kinds existed
loads unchanged. The others show the house itself: `entity` (one entity's
state and its switch), `readings` (the newest sensor readings by room),
`camera` (a still, through the vision integration's consent and audit),
`sky` (the next ISS pass and the moon), `moments` (the newest notifications).
The data behind them is read over three websocket commands the contract
names; this module only decides what a saved widget may be.

## Whose dashboard is it

There are no user accounts here (`jarvis/auth.py`: "There are no user accounts
and no login form"). A token is the identity: one per device, minted by the
operator. So `owner` is a token id, "per user" means "per token", and a layout
with no owner is shared — which is what the shipped examples are, and what an
operator gets if they save one from a console session with no token of its own.

The alternative — one global set of dashboards — was rejected because the phone
and the wall panel want different screens, and they authenticate as different
tokens already.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import yaml

from ...metrics import DataSource, Series, Window
from ...metrics.sources.internal import build as build_internal
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dashboards"
STORAGE_KEY = "dashboards"
STORAGE_VERSION = 1

#: A layout is small; a hundred of them is still small. The cap is a guard
#: against a client in a loop, not a limit anybody should meet.
MAX_DASHBOARDS = 100
MAX_WIDGETS = 40
COLUMNS = 12

#: The chart types the console can draw. A widget naming anything else is
#: refused at save time rather than drawn as a blank rectangle later.
TYPES = ("line", "area", "bar", "stat", "gauge", "table")
#: What a widget shows. `metric` is the graphs; a widget with no `kind` is one,
#: so a layout saved before M63 loads as it did. The console's list is the same
#: list — the contract's `kinds` table — and both suites check it.
KINDS = ("metric", "entity", "readings", "camera", "sky", "moments")
#: A new widget's footprint per kind, in columns and rows: a tile is small, a
#: list of readings or a still needs room. Only used when the client sent none.
DEFAULT_SIZE = {
    "metric": (4, 2),
    "entity": (3, 2),
    "readings": (6, 3),
    "camera": (6, 3),
    "sky": (3, 2),
    "moments": (6, 3),
}
#: How many moments a `moments` widget shows when it does not say, and the most
#: it may ask for — a dashboard is a glance, not the inbox.
DEFAULT_MOMENTS = 6
MAX_MOMENTS = 20
#: `domain.object_id`, as the state machine spells it. An entity tile naming
#: anything else would poll for a state that can never exist.
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
RANGES = ("1h", "6h", "24h", "7d")
RANGE_SECONDS = {"1h": 3600.0, "6h": 21600.0, "24h": 86400.0, "7d": 604800.0}


def _slug(value: Any, fallback: str = "") -> str:
    text = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(value or "").lower())
    return "-".join(part for part in text.split("-") if part)[:60] or fallback


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def clean_widget(raw: Any, index: int) -> dict[str, Any] | None:
    """One widget, or None if it is not one.

    Refusing here rather than when drawing: a widget with a kind or chart type
    the console cannot draw becomes a blank rectangle somebody has to delete, a
    graph with no series becomes an empty chart that looks like a broken
    sensor, and an entity tile with no entity is a card about nothing.

    What this does not check is that the entity, camera or room exists today.
    A layout is saved one day and shown on another, and a tile whose device is
    unplugged for the afternoon should come back with it, not be dropped from
    the file.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "metric")
    if kind not in KINDS:
        return None
    width, height = DEFAULT_SIZE[kind]
    widget: dict[str, Any] = {
        "id": _slug(raw.get("id"), f"w{index}"),
        "title": str(raw.get("title") or "")[:80],
        "kind": kind,
        "x": _int(raw.get("x"), 0, 0, COLUMNS - 1),
        "y": _int(raw.get("y"), index, 0, 500),
        "w": _int(raw.get("w"), width, 1, COLUMNS),
        "h": _int(raw.get("h"), height, 1, 12),
    }
    if kind == "metric":
        chart = str(raw.get("type") or "line")
        if chart not in TYPES:
            return None
        series = [str(s)[:120] for s in (raw.get("series") or []) if str(s).strip()][:8]
        if not series:
            return None
        widget.update(
            {
                "type": chart,
                "source": _slug(raw.get("source"), "internal"),
                "series": series,
                "aggregate": str(raw.get("aggregate") or "")[:10],
            }
        )
    elif kind == "entity":
        entity = str(raw.get("entity") or "").strip()[:120]
        if not _ENTITY_ID.match(entity):
            return None
        widget["entity"] = entity
    elif kind == "camera":
        # Empty means "the only camera, if there is one": the shipped House
        # dashboard cannot know what an installation calls its front door.
        widget["camera"] = str(raw.get("camera") or "").strip()[:80]
    elif kind == "readings":
        widget["area"] = str(raw.get("area") or "").strip()[:80]
    elif kind == "moments":
        widget["limit"] = _int(raw.get("limit"), DEFAULT_MOMENTS, 1, MAX_MOMENTS)
    return widget


def clean_dashboard(raw: Any, *, owner: str = "") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()[:80]
    dashboard_id = _slug(raw.get("id"), _slug(title))
    if not dashboard_id or not title:
        return None
    widgets = [w for w in (clean_widget(w, i) for i, w in enumerate(raw.get("widgets") or [])) if w]
    window = str(raw.get("range") or "6h")
    return {
        "id": dashboard_id,
        "title": title,
        "owner": owner,
        "range": window if window in RANGES else "6h",
        "widgets": widgets[:MAX_WIDGETS],
        "updated": float(raw.get("updated") or time.time()),
    }


class DashboardStore:
    """Saved layouts, by owner. One JSON file; a layout is a few hundred bytes."""

    def __init__(self, jarvis: "Jarvis", store: Store | None = None) -> None:
        self.jarvis = jarvis
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        #: Saved by somebody. Shipped examples live in `self.shipped`.
        self.saved: list[dict[str, Any]] = []
        #: Read-only, from `<config>/dashboards/*.yaml`. Owned by nobody.
        self.shipped: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        data = await self.store.load() or {}
        self.saved = [
            board
            for board in (
                clean_dashboard(raw, owner=str((raw or {}).get("owner") or ""))
                for raw in (data.get("dashboards") or [])
            )
            if board
        ][:MAX_DASHBOARDS]

    async def async_save(self) -> None:
        await self.store.save({"dashboards": self.saved})

    def load_shipped(self, directory: Any) -> None:
        """Examples an operator can copy. Never written to.

        Ordered by each file's `order`, then its name: the first shipped board
        is what a fresh install opens on, and file names sort "homelab" ahead
        of "house" — the graphs of the machine ahead of the house itself.
        """
        try:
            paths = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
        except OSError:  # pragma: no cover
            return
        loaded: list[tuple[int, str, dict[str, Any]]] = []
        for path in paths:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as err:
                _LOGGER.warning("dashboards: %s is not readable YAML (%s)", path.name, err)
                continue
            board = clean_dashboard(raw, owner="")
            if board is None:
                _LOGGER.warning("dashboards: %s is not a dashboard", path.name)
                continue
            board["shipped"] = True
            loaded.append((_int(raw.get("order"), 100, 0, 1000), path.name, board))
        loaded.sort(key=lambda item: (item[0], item[1]))
        self.shipped.extend(board for _, _, board in loaded)

    def visible_to(self, owner: str) -> list[dict[str, Any]]:
        """This token's own, plus everything shared. Never somebody else's."""
        mine = [b for b in self.saved if b.get("owner") == owner and owner]
        shared = [b for b in self.saved if not b.get("owner")]
        return [*mine, *shared, *self.shipped]

    async def async_put(self, raw: Any, owner: str) -> dict[str, Any]:
        board = clean_dashboard(raw, owner=owner)
        if board is None:
            raise ValueError("a dashboard needs an id, a title and at least one usable widget")
        board["updated"] = time.time()
        # Replace this owner's board of the same id; never somebody else's.
        self.saved = [
            b for b in self.saved if not (b["id"] == board["id"] and b.get("owner") == owner)
        ]
        self.saved.append(board)
        del self.saved[:-MAX_DASHBOARDS]
        await self.async_save()
        return board

    async def async_delete(self, dashboard_id: str, owner: str) -> bool:
        before = len(self.saved)
        self.saved = [
            b
            for b in self.saved
            if not (b["id"] == dashboard_id and b.get("owner") == owner)
        ]
        if len(self.saved) == before:
            return False
        await self.async_save()
        return True


async def async_query(
    jarvis: "Jarvis",
    source_name: str,
    keys: list[str],
    window: Window,
    aggregate: str = "",
) -> list[dict[str, Any]]:
    """One widget's numbers. A missing source is an answer, not an exception."""
    sources: dict[str, DataSource] = jarvis.data.get("metrics_sources") or {}
    source = sources.get(_slug(source_name, "internal"))
    if source is None:
        return [
            Series(key=key, error=f"no data source called {source_name!r}").as_dict()
            for key in keys
        ]
    try:
        series = await source.query(list(keys), window, aggregate)
    except Exception as err:  # pragma: no cover - a source is not a crash
        _LOGGER.exception("dashboards: %s failed", source_name)
        return [
            Series(key=key, error=f"{type(err).__name__}: {err}"[:200]).as_dict() for key in keys
        ]
    return [s.as_dict() for s in series]


async def async_sources(jarvis: "Jarvis") -> list[dict[str, Any]]:
    """Every source and what it can graph, for the widget editor."""
    sources: dict[str, DataSource] = jarvis.data.get("metrics_sources") or {}
    out: list[dict[str, Any]] = []
    for name, source in sorted(sources.items()):
        healthy, why = await source.healthy()
        try:
            series = [info.as_dict() for info in await source.list_series()]
        except Exception as err:  # pragma: no cover
            healthy, why, series = False, f"{type(err).__name__}: {err}"[:200], []
        out.append(
            {
                "name": name,
                "description": getattr(source, "description", ""),
                "healthy": healthy,
                "detail": why,
                "series": series,
            }
        )
    return out


def window_for(payload: dict[str, Any]) -> Window:
    """`{range: "6h"}` or `{start, end, step}` — whichever the client sent."""
    named = str(payload.get("range") or "")
    if named in RANGE_SECONDS:
        return Window.last(RANGE_SECONDS[named], step=float(payload.get("step") or 0))
    try:
        end = float(payload.get("end") or time.time())
        start = float(payload.get("start") or (end - 21600.0))
        step = float(payload.get("step") or 0)
    except (TypeError, ValueError):
        return Window.last(21600.0)
    if end <= start:
        return Window.last(21600.0)
    return Window(start=start, end=end, step=step)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    options = config if isinstance(config, dict) else {}

    sources: dict[str, DataSource] = {"internal": build_internal(jarvis)}
    metrics_config = getattr(jarvis, "config", {}).get("metrics") if hasattr(jarvis, "config") else None
    for name, settings in ((metrics_config or {}).get("sources") or {}).items():
        if name == "influx":
            try:
                from ...metrics.sources.influx import build as build_influx

                sources["influx"] = build_influx(jarvis, settings or {})
            except Exception as err:  # pragma: no cover - a bad source is not a boot failure
                _LOGGER.error("dashboards: the influx source did not start: %s", err)
        else:
            _LOGGER.warning("dashboards: no data source called %r", name)
    jarvis.data["metrics_sources"] = sources

    store = DashboardStore(jarvis)
    await store.async_load()
    shipped = getattr(jarvis, "config_dir", None)
    if shipped is not None:
        from pathlib import Path

        store.load_shipped(Path(shipped) / str(options.get("shipped") or "dashboards"))
    jarvis.data[DOMAIN] = store

    _register_metrics_tool(jarvis)

    _LOGGER.info(
        "dashboards ready: %d source(s), %d saved, %d shipped",
        len(sources),
        len(store.saved),
        len(store.shipped),
    )
    return True


#: How far back `metrics_query` will look without being asked to.
DEFAULT_METRICS_HOURS = 6
#: And the furthest it will go. A month of one-minute samples is not an answer
#: anybody wanted spoken aloud, and it is a query that can hurt an InfluxDB.
MAX_METRICS_HOURS = 24 * 14


def _register_metrics_tool(jarvis: "Jarvis") -> None:
    """Let the model read the same numbers the dashboards draw.

    The measurements were already here — `metrics/sources/influx.py` has been
    feeding the console's charts — but nothing could put one in a sentence.
    "Is the loft warmer than it was this morning" was a question Jarvis could
    draw and could not answer.

    Read-only and Tier 1: it queries a time series and returns numbers. The
    summary rather than the points, because a spoken answer needs "18.4°C,
    down 2 degrees since midnight" and a hundred samples is what makes a model
    invent a trend rather than read one.
    """
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        return
    from ...llm.tools import schema_object

    async def tool_metrics_query(args: dict[str, Any], context: Any = None) -> Any:
        source = str(args.get("source") or "internal")
        keys = args.get("keys") or args.get("key") or []
        if isinstance(keys, str):
            keys = [keys]
        keys = [str(k) for k in list(keys)[:8] if str(k).strip()]
        if not keys:
            available = await async_sources(jarvis)
            return {
                "status": "error",
                "error": "name at least one series in `keys`",
                "sources": available,
            }
        try:
            hours = float(args.get("hours") or DEFAULT_METRICS_HOURS)
        except (TypeError, ValueError):
            hours = float(DEFAULT_METRICS_HOURS)
        hours = max(0.05, min(hours, float(MAX_METRICS_HOURS)))
        rows = await async_query(
            jarvis,
            source,
            keys,
            Window.last(hours * 3600.0),
            str(args.get("aggregate") or ""),
        )
        out = []
        for row in rows:
            values = [v for _, v in row.get("points", []) if v is not None]
            summary: dict[str, Any] = {
                "key": row.get("key"),
                "label": row.get("label"),
                "unit": row.get("unit"),
                "error": row.get("error", ""),
                "samples": len(values),
            }
            if values:
                summary.update(
                    {
                        "latest": round(values[-1], 4),
                        "first": round(values[0], 4),
                        "min": round(min(values), 4),
                        "max": round(max(values), 4),
                        "mean": round(sum(values) / len(values), 4),
                        "change": round(values[-1] - values[0], 4),
                    }
                )
            out.append(summary)
        return {"status": "ok", "source": source, "hours": round(hours, 3), "series": out}

    registry.register(
        name="metrics_query",
        description=(
            "Read recorded measurements — temperatures, power, model throughput, "
            "anything the dashboards graph. Returns a summary per series (latest, "
            "min, max, mean, change over the window), not the raw samples. Call "
            "with no `keys` to find out what this house records."
        ),
        parameters=schema_object(
            {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Series keys, e.g. sensor.office_temperature.",
                },
                "source": {
                    "type": "string",
                    "description": "Which store: 'internal' (default) or 'influx'.",
                },
                "hours": {
                    "type": "number",
                    "description": f"How far back (default {DEFAULT_METRICS_HOURS}).",
                },
                "aggregate": {
                    "type": "string",
                    "description": "mean, min, max, sum or last. The series' own default if unset.",
                },
            },
        ),
        handler=tool_metrics_query,
        domain=DOMAIN,
    )
