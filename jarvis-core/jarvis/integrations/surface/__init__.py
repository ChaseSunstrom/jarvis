"""The surface — what Jarvis has put up on the voice screen (M83).

"Have jarvis able to pull things up and display them on the voice screen,
kind of like iron man, and able to move things around." A panel is a thing
the house already knows how to draw — an entity's tile, a camera's still, a
room's readings, a note, a page's text, the sky, the moments — placed around
the instrument. The model puts one up with `show`, takes them down with
`clear_screen`, nudges one with `move_panel`; a person drags them, and the
console tells this store where they were left, so the arrangement survives a
reload and is the same on every screen that shows the surface.

One surface per house, not per token: the voice screen is a place in the
room, and two people looking at it see the same thing.

What it does NOT do: fetch what a panel shows. A panel is a spec — kind and
target — and the screen that draws it reads the live thing the way the
dashboards do, so a stale copy is never on the wall.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "surface"
STORAGE_KEY = "surface"
STORAGE_VERSION = 1
EVENT_SURFACE_CHANGED = "jarvis_surface_changed"

#: Kinds the console can draw. Each is a dashboard widget the console already
#: has (M63), plus two for text: a note, and a page's fenced text.
KINDS = ("entity", "camera", "readings", "sky", "moments", "note", "page", "chart", "task")
#: How many panels fit around an instrument before the screen is a wall.
MAX_PANELS = 8
#: The stage is a 12-column grid; a panel is w×h cells. Slots are the places
#: a new panel goes by default, left and right of the instrument first.
COLUMNS = 12
#: Jobs whose plan the screen follows on its own (M88). A reminder or a
#: research run has its own surface; a background plan has none but this.
FOLLOWED_KINDS = frozenset({"background"})

SLOTS = ((0, 0), (8, 0), (0, 4), (8, 4), (0, 8), (8, 8), (4, 8), (4, 0))
DEFAULT_SIZE = {"entity": (4, 2), "camera": (4, 3), "readings": (4, 3), "sky": (4, 2),
                # A note or a page is a one-row BRIEF (M112): its title and
                # first line, at a side. The operator's report of 27 Aug 2026
                # ("all of the notes popups are still taking up a ton of space")
                # was 4×3 notes, each a third of the page. ⤢ on the console
                # opens one to four rows; `resize` ("bigger") does the same by voice.
                "moments": (4, 3), "note": (4, 1), "page": (4, 1), "chart": (4, 3),
    "task": (4, 4),
}
MAX_TEXT_CHARS = 4000


def _clean(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


class Surface:
    """The panels, in order of placement, and the one file they live in."""

    def __init__(self, jarvis: "Jarvis", store: Store | None = None) -> None:
        self.jarvis = jarvis
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.panels: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        data = await self.store.load() or {}
        self.panels = [p for p in (self._clean_panel(raw) for raw in data.get("panels") or []) if p][:MAX_PANELS]

    async def async_save(self) -> None:
        await self.store.save({"panels": self.panels})

    def _clean_panel(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        kind = _clean(raw.get("kind"), 20)
        if kind not in KINDS:
            return None
        w, h = DEFAULT_SIZE[kind]
        panel = {
            "id": _clean(raw.get("id"), 32) or uuid.uuid4().hex[:10],
            "kind": kind,
            "title": _clean(raw.get("title"), 80),
            "entity": _clean(raw.get("entity"), 120),
            "camera": _clean(raw.get("camera"), 80),
            "area": _clean(raw.get("area"), 80),
            "note": _clean(raw.get("note"), 80),
            "url": _clean(raw.get("url"), 2048),
            "task": _clean(raw.get("task"), 64),
            "text": str(raw.get("text") or "")[:MAX_TEXT_CHARS],
            "limit": max(1, min(int(raw.get("limit") or 6), 20)),
            "x": max(0, min(int(raw.get("x") or 0), COLUMNS - 1)),
            "y": max(0, int(raw.get("y") or 0)),
            "w": max(2, min(int(raw.get("w") or w), COLUMNS)),
            "h": max(1, min(int(raw.get("h") or h), 12)),
            "placed_at": float(raw.get("placed_at") or time.time()),
        }
        return panel

    # --- a plan on the screen (M88) ------------------------------------------
    async def async_follow_task(self, task: dict[str, Any]) -> None:
        """A background job with steps is a `task` panel while it runs and a
        `note` with its result when it is done — the plan beside the instrument,
        as the operator asked ("kind of like iron man"), without anybody having
        to say "show me the job"."""
        task_id = str(task.get("id") or "")
        if not task_id or str(task.get("kind") or "") not in FOLLOWED_KINDS:
            return
        status = str(task.get("status") or "")
        steps = task.get("steps") or []
        finished = status in ("done", "error", "cancelled")
        existing = next((p for p in self.panels if p.get("kind") == "task" and p.get("task") == task_id), None)
        if not finished:
            if not steps or existing is not None:
                return
            await self.async_place({
                "kind": "task", "task": task_id, "title": str(task.get("title") or "A job")[:80],
            })
            return
        if existing is not None:
            self.panels.remove(existing)
            await self._changed()
        result = str(task.get("result") or "").strip()
        if status == "done" and result:
            await self.async_place({
                "kind": "note", "note": f"task:{task_id}",
                "title": f"Finished: {str(task.get('title') or 'a job')[:60]}",
                "text": result[:MAX_TEXT_CHARS],
            })

    def as_payload(self) -> dict[str, Any]:
        return {"panels": [dict(p) for p in self.panels], "max": MAX_PANELS}

    async def _changed(self) -> None:
        await self.async_save()
        await self.jarvis.bus.async_fire(EVENT_SURFACE_CHANGED, self.as_payload())

    def _free_slot(self, w: int, h: int) -> tuple[int, int]:
        if h <= 1:
            # A brief stacks DOWN a side column — the right first, then the
            # left — one row under the last thing there, so three notes are
            # three lines at the edge and not three slots spread over the
            # instrument. Checked against real rectangles, not slot corners:
            # a 4×3 camera at (8, 0) covers (8, 1) and (8, 2) too.
            for x in (COLUMNS - w, 0):
                y = 0
                while y < 12:
                    if not any(self._overlaps(p, x, y, w, h) for p in self.panels):
                        return x, y
                    y += 1
        taken = {(p["x"], p["y"]) for p in self.panels}
        for x, y in SLOTS:
            if (x, y) not in taken:
                return x, y
        # Every slot taken: below the lowest panel, so nothing is covered.
        bottom = max((p["y"] + p["h"] for p in self.panels), default=0)
        return 0, bottom

    @staticmethod
    def _overlaps(panel: dict[str, Any], x: int, y: int, w: int, h: int) -> bool:
        return not (
            panel["x"] + panel["w"] <= x
            or x + w <= panel["x"]
            or panel["y"] + panel["h"] <= y
            or y + h <= panel["y"]
        )

    def find(self, ref: Any) -> dict[str, Any] | None:
        """A panel by id, or by the words of its title / target."""
        text = _clean(ref, 120).lower()
        if not text:
            return None
        for panel in self.panels:
            if panel["id"] == text:
                return panel
        words = set(re.findall(r"[a-z0-9]+", text))
        best, score = None, 0
        for panel in self.panels:
            hay = " ".join(str(panel.get(k) or "") for k in ("title", "entity", "camera", "area", "note", "kind")).lower()
            hits = sum(1 for w in words if w in hay)
            if hits > score:
                best, score = panel, hits
        return best

    async def async_place(self, raw: dict[str, Any]) -> dict[str, Any]:
        panel = self._clean_panel(raw)
        if panel is None:
            return {"status": "error", "error": f"a panel needs a kind from {', '.join(KINDS)}"}
        # The same thing twice is one panel, brought to the front, not two.
        for existing in list(self.panels):
            same = all(existing.get(k) == panel.get(k) for k in ("kind", "entity", "camera", "area", "note", "url", "task"))
            if same:
                self.panels.remove(existing)
                panel["id"] = existing["id"]
                panel["x"], panel["y"] = existing["x"], existing["y"]
                break
        else:
            if len(self.panels) >= MAX_PANELS:
                oldest = min(self.panels, key=lambda p: p["placed_at"])
                self.panels.remove(oldest)
            if "x" not in raw and "y" not in raw:
                panel["x"], panel["y"] = self._free_slot(panel["w"], panel["h"])
        self.panels.append(panel)
        await self._changed()
        return {"status": "ok", "panel": dict(panel), "count": len(self.panels)}

    async def async_move(self, panel_id: Any, **where: Any) -> dict[str, Any]:
        panel = self.find(panel_id)
        if panel is None:
            return {"status": "error", "error": f"no panel {panel_id!r} on the surface"}
        for key in ("x", "y", "w", "h"):
            if where.get(key) is not None:
                panel[key] = int(where[key])
        panel["x"] = max(0, min(panel["x"], COLUMNS - panel["w"]))
        panel["y"] = max(0, panel["y"])
        panel["w"] = max(2, min(panel["w"], COLUMNS))
        panel["h"] = max(1, min(panel["h"], 12))
        await self._changed()
        return {"status": "ok", "panel": dict(panel)}

    async def async_remove(self, panel_id: Any) -> dict[str, Any]:
        panel = self.find(panel_id)
        if panel is None:
            return {"status": "error", "error": f"no panel {panel_id!r} on the surface"}
        self.panels.remove(panel)
        await self._changed()
        return {"status": "ok", "removed": panel["id"], "count": len(self.panels)}

    async def async_clear(self) -> dict[str, Any]:
        gone = len(self.panels)
        self.panels = []
        await self._changed()
        return {"status": "ok", "removed": gone}


async def async_history_series(jarvis: "Jarvis", entity_id: str, hours: float = 24.0) -> dict[str, Any]:
    """An entity's recent history as the chart's series: `[[at, value], …]`,
    oldest first, numeric states only. The same recorder the history tools
    read; with no recorder, the current state as one point, which a chart
    draws as a level rather than nothing."""
    from datetime import datetime

    from ..history import get_history

    entity_id = str(entity_id or "").strip().lower()
    if not entity_id:
        return {"series": []}
    end = time.time()
    hours = max(0.25, min(float(hours or 24.0), 24.0 * 14))
    rows = (await get_history(jarvis, [entity_id], start=end - hours * 3600, end=end)).get(entity_id) or []
    points: list[list[float]] = []
    for row in rows:
        try:
            value = float(row.get("state"))
        except (TypeError, ValueError):
            continue
        stamp = row.get("last_changed") or row.get("last_updated")
        try:
            at = float(stamp) if isinstance(stamp, (int, float)) else datetime.fromisoformat(str(stamp)).timestamp()
        except (TypeError, ValueError):
            continue
        points.append([at, value])
    state = jarvis.states.get(entity_id)
    attributes = state.attributes if state is not None else {}
    return {
        "series": [
            {
                "key": entity_id,
                "label": str(attributes.get("friendly_name") or entity_id),
                "unit": str(attributes.get("unit_of_measurement") or ""),
                "aggregate": "",
                "points": points,
            }
        ],
        "hours": hours,
    }


def get_surface(jarvis: "Jarvis") -> Surface | None:
    return (jarvis.data.get(DOMAIN) or {}).get("surface")


#: "put the camera on the left" — the words a person uses for a place, as the
#: grid position they mean. Centre means beside the instrument, not over it.
PLACES = {
    "left": {"x": 0}, "right": {"x": COLUMNS - 4}, "top": {"y": 0}, "bottom": {"y": 8},
    "top left": {"x": 0, "y": 0}, "top right": {"x": COLUMNS - 4, "y": 0},
    "bottom left": {"x": 0, "y": 8}, "bottom right": {"x": COLUMNS - 4, "y": 8},
    "middle": {"x": 4, "y": 8}, "centre": {"x": 4, "y": 8}, "center": {"x": 4, "y": 8},
}
SIZES = {"bigger": 1, "larger": 1, "smaller": -1, "small": -1, "big": 1, "large": 1}


def _register_tools(jarvis: "Jarvis", surface: Surface) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        return
    from ...llm.tools import TIER_DIRECT, resolve_entities, schema_object

    async def tool_show(args: dict[str, Any], context: Any = None) -> Any:
        kind = _clean(args.get("kind"), 20).lower()
        what = _clean(args.get("what"), 160)
        spec: dict[str, Any] = {"kind": kind, "title": _clean(args.get("title"), 80)}
        # The word "camera" means the camera, whatever the house also calls
        # "front door": a lock and a contact sensor answer to those words too,
        # and the resolver would put a lock's tile up for "the front door camera".
        if not kind and "camera" in what.lower() and not args.get("entity_id"):
            kind = "camera"
        if kind in ("entity", "chart") or (not kind and (args.get("entity_id") or what)):
            resolution = resolve_entities(
                jarvis, registry.exposure, name=args.get("entity_id") or what, entity_id=args.get("entity_id")
            )
            if resolution.error or not resolution.entity_ids:
                if kind in ("entity", "chart"):
                    return {"status": "error", "error": resolution.error or f"nothing called {what!r} to show"}
            else:
                spec["kind"] = kind or "entity"
                spec["entity"] = resolution.entity_ids[0]
                spec["title"] = spec["title"] or what
        if kind == "camera" or (not spec.get("kind") and "camera" in what.lower()):
            spec["kind"] = "camera"
            spec["camera"] = _clean(args.get("camera") or what.replace("camera", "").strip(), 80)
            spec["title"] = spec["title"] or (spec["camera"] or "camera")
        elif kind == "readings":
            spec["area"] = _clean(args.get("area") or what, 80)
            spec["title"] = spec["title"] or (spec["area"] or "readings")
        elif kind == "note":
            spec["note"] = _clean(args.get("note") or what, 80)
            spec["text"] = str(args.get("text") or "")[:MAX_TEXT_CHARS]
            spec["title"] = spec["title"] or spec["note"]
        elif kind == "page":
            spec["url"] = _clean(args.get("url"), 2048)
            spec["text"] = str(args.get("text") or "")[:MAX_TEXT_CHARS]
            spec["title"] = spec["title"] or what or spec["url"]
        elif kind in ("sky", "moments"):
            spec["title"] = spec["title"] or kind
        if spec.get("kind") not in KINDS:
            return {
                "status": "error",
                "error": (
                    f"say what to show: an entity by name, a camera, a room's readings, a note, "
                    f"a page, the sky or the moments (kinds: {', '.join(KINDS)})"
                ),
            }
        place = _clean(args.get("place"), 20).lower()
        if place in PLACES:
            spec.update(PLACES[place])
        result = await surface.async_place(spec)
        if result.get("status") == "ok":
            result["message"] = (
                f"{result['panel']['title'] or result['panel']['kind']} is on the screen. Tell the "
                "user in a few words what is up; they can drag it, or say where it should go."
            )
        return result

    async def tool_clear(args: dict[str, Any], context: Any = None) -> Any:
        which = _clean(args.get("panel"), 120)
        if which:
            return await surface.async_remove(which)
        return await surface.async_clear()

    async def tool_move(args: dict[str, Any], context: Any = None) -> Any:
        panel = surface.find(args.get("panel"))
        if panel is None:
            return {"status": "error", "error": f"no panel {args.get('panel')!r} on the screen"}
        where: dict[str, Any] = {}
        place = _clean(args.get("place"), 20).lower()
        if place in PLACES:
            where.update(PLACES[place])
        size = _clean(args.get("size"), 20).lower()
        if size in SIZES:
            step = SIZES[size]
            where["w"] = panel["w"] + 2 * step
            where["h"] = panel["h"] + step
        if not where:
            return {"status": "error", "error": "say where (left, right, top, bottom, centre) or how big (bigger, smaller)"}
        return await surface.async_move(panel["id"], **where)

    registry.register(
        name="show",
        description=(
            "Put something up on the voice screen, beside the instrument: an entity's tile (a light, "
            "a lock, a sensor), a camera, a room's readings, a note, a page's text, the sky, or "
            "the moments. Use it whenever the user asks to see, show, display, pull up or put up "
            "a thing. It returns at once; the screen draws it live."
        ),
        parameters=schema_object(
            {
                "what": {"type": "string", "description": "what to show, in the user's words — 'the front door camera', 'the bed light', 'the living room readings'"},
                "kind": {"type": "string", "description": f"one of {', '.join(KINDS)}; leave empty to infer from `what`"},
                "entity_id": {"type": "string", "description": "the entity, when known"},
                "camera": {"type": "string", "description": "a camera's name, for kind camera"},
                "area": {"type": "string", "description": "a room, for kind readings"},
                "note": {"type": "string", "description": "a note's title, for kind note"},
                "text": {"type": "string", "description": "text to show, for kind note or page"},
                "url": {"type": "string", "description": "the page's address, for kind page"},
                "title": {"type": "string", "description": "the panel's title, if not the thing's own name"},
                "place": {"type": "string", "description": "left, right, top, bottom, top left, bottom right, centre"},
            },
            ["what"],
        ),
        handler=tool_show,
        tier=TIER_DIRECT,
    )
    registry.register(
        name="clear_screen",
        description=(
            "Take panels off the voice screen — 'clear the screen', 'take that down', "
            "'get rid of the camera': one by name, or all of them when no panel is named. "
            "Call it; do not just say the screen is clear."
        ),
        parameters=schema_object({"panel": {"type": "string", "description": "which panel — its title or what it shows; empty for all"}}, []),
        handler=tool_clear,
        tier=TIER_DIRECT,
    )
    registry.register(
        name="move_panel",
        description="Move or resize a panel on the voice screen: 'put the camera on the left', 'make the readings bigger'.",
        parameters=schema_object(
            {
                "panel": {"type": "string", "description": "which panel — its title or what it shows"},
                "place": {"type": "string", "description": "left, right, top, bottom, top left, top right, bottom left, bottom right, centre"},
                "size": {"type": "string", "description": "bigger or smaller"},
            },
            ["panel"],
        ),
        handler=tool_move,
        tier=TIER_DIRECT,
    )


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    surface = Surface(jarvis)
    await surface.async_load()
    jarvis.data[DOMAIN] = {"surface": surface}
    _register_tools(jarvis, surface)
    _LOGGER.info("Surface: %d panel(s) up", len(surface.panels))

    # M88: the plan follows the job. Off with `surface: plans: false`.
    if (config or {}).get("plans", True) if isinstance(config, dict) else True:
        from ...tasks import EVENT_TASK_ADDED, EVENT_TASK_UPDATED

        async def _on_task(event: Any) -> None:
            task = (getattr(event, "data", None) or {}).get("task") or {}
            try:
                await surface.async_follow_task(task)
            except Exception:  # pragma: no cover - a panel must never fail a job
                _LOGGER.exception("surface: could not follow task %s", task.get("id"))

        jarvis.bus.listen(EVENT_TASK_ADDED, _on_task)
        jarvis.bus.listen(EVENT_TASK_UPDATED, _on_task)
    return True
