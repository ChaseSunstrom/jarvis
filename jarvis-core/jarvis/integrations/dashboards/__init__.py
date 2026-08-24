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

    Refusing here rather than when drawing: a widget with a type the console
    cannot draw becomes a blank rectangle somebody has to delete, and a widget
    with no series becomes an empty chart that looks like a broken sensor.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "line")
    if kind not in TYPES:
        return None
    series = [str(s)[:120] for s in (raw.get("series") or []) if str(s).strip()][:8]
    if not series:
        return None
    return {
        "id": _slug(raw.get("id"), f"w{index}"),
        "title": str(raw.get("title") or "")[:80],
        "type": kind,
        "source": _slug(raw.get("source"), "internal"),
        "series": series,
        "aggregate": str(raw.get("aggregate") or "")[:10],
        "x": _int(raw.get("x"), 0, 0, COLUMNS - 1),
        "y": _int(raw.get("y"), index, 0, 500),
        "w": _int(raw.get("w"), 4, 1, COLUMNS),
        "h": _int(raw.get("h"), 2, 1, 12),
    }


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
        """Examples an operator can copy. Never written to."""
        try:
            paths = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
        except OSError:  # pragma: no cover
            return
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
            self.shipped.append(board)

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

    _LOGGER.info(
        "dashboards ready: %d source(s), %d saved, %d shipped",
        len(sources),
        len(store.saved),
        len(store.shipped),
    )
    return True
