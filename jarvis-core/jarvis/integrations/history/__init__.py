"""History — reads back what the recorder wrote.

Pure query layer: no HTTP, no state of its own. The HTTP API, the voice
assistant and templates all funnel through :func:`get_history`.

Configuration is optional — ``history:`` on its own is enough (it pulls in
``recorder`` as a dependency)::

    history:
      days: 7          # default window when a caller gives no start/end

Services
    ``history.get`` (entity_id, start, end, minimal_response,
    include_start_time_state) → ``{"history": {entity_id: [...]}, ...}``
    ``history.stats`` (entity_id, start, end) → min / max / mean / first /
    last / changes for numeric entities.

Both support responses, so an LLM tool call or the REST API gets data back
directly rather than having to poll.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Iterable

from ...services import ServiceCall
from ..recorder import as_iso, as_timestamp, get_recorder

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "history"
DEPENDENCIES = ["recorder"]

DEFAULT_WINDOW_DAYS = 1


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _minimal(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entry["entity_id"],
        "state": entry["state"],
        "last_changed": entry["last_changed"],
        "last_changed_iso": entry["last_changed_iso"],
    }


def resolve_window(jarvis: "Jarvis", start: Any, end: Any) -> tuple[float, float]:
    """The (start, end) epoch pair a query actually runs over.

    Callers need this to report the window back accurately when they did not
    pass one — the default is ``end - days``, not "no start".
    """
    settings = jarvis.data.get(DOMAIN)
    days = settings.get("days", DEFAULT_WINDOW_DAYS) if isinstance(settings, dict) else DEFAULT_WINDOW_DAYS
    end_ts = as_timestamp(end, None)
    if end_ts is None:
        end_ts = time.time()
    start_ts = as_timestamp(start, None)
    if start_ts is None:
        start_ts = end_ts - days * 86400
    return start_ts, end_ts


def _from_state_machine(jarvis: "Jarvis", entity_ids: list[str]) -> dict[str, list]:
    """Fallback when nothing has been recorded: one point per live entity."""
    ids = entity_ids or jarvis.states.entity_ids()
    out: dict[str, list[dict[str, Any]]] = {}
    for entity_id in ids:
        state = jarvis.states.get(entity_id)
        if state is None:
            out[entity_id] = []
            continue
        out[entity_id] = [
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
                "last_changed": state.last_changed,
                "last_updated": state.last_updated,
                "last_changed_iso": as_iso(state.last_changed),
                "last_updated_iso": as_iso(state.last_updated),
                "context_id": state.context.id,
            }
        ]
    return out


async def get_history(
    jarvis: "Jarvis",
    entity_ids: Iterable[str] | str | None = None,
    start: Any = None,
    end: Any = None,
    include_start_time_state: bool = True,
    minimal_response: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """State history per entity, oldest first.

    ``start``/``end`` accept epoch floats, datetimes or ISO strings. When
    ``include_start_time_state`` is set, each entity's series is prefixed
    with the state it was already in when the window opened, so a graph
    starts at the right value instead of at the first change.
    """
    ids = [e.lower() for e in _as_list(entity_ids)]
    recorder = get_recorder(jarvis)
    if recorder is None:
        _LOGGER.debug("history: no recorder configured, using current states")
        return _from_state_machine(jarvis, ids)

    start_ts, end_ts = resolve_window(jarvis, start, end)

    rows = await recorder.states_between(ids or None, start_ts, end_ts)

    result: dict[str, list[dict[str, Any]]] = {entity_id: [] for entity_id in ids}
    if include_start_time_state:
        # With no explicit entity list the caller means "everything", so the
        # prior state has to be looked up for every recorded entity — not
        # skipped, which used to leave whole-house graphs starting at the
        # first change instead of at the value the window opened on.
        prior_ids = ids or await recorder.recorded_entity_ids()
        priors = await recorder.last_state_before(prior_ids, start_ts)
        for entity_id, prior in priors.items():
            result.setdefault(entity_id, []).append(prior)

    for row in rows:
        result.setdefault(row["entity_id"], []).append(row)

    if minimal_response:
        for entity_id, entries in result.items():
            if not entries:
                continue
            result[entity_id] = [entries[0]] + [_minimal(e) for e in entries[1:]]
    return result


async def get_stats(
    jarvis: "Jarvis",
    entity_ids: Iterable[str] | str | None = None,
    start: Any = None,
    end: Any = None,
) -> dict[str, dict[str, Any]]:
    """min / max / mean / first / last / change-count over a window."""
    history = await get_history(
        jarvis, entity_ids, start, end, include_start_time_state=True
    )
    stats: dict[str, dict[str, Any]] = {}
    for entity_id, entries in history.items():
        if not entries:
            stats[entity_id] = {"count": 0}
            continue
        numbers: list[float] = []
        for entry in entries:
            try:
                numbers.append(float(entry["state"]))
            except (TypeError, ValueError):
                continue
        summary: dict[str, Any] = {
            "count": len(entries),
            "first": entries[0]["state"],
            "last": entries[-1]["state"],
            "changes": sum(
                1
                for prev, cur in zip(entries, entries[1:])
                if prev["state"] != cur["state"]
            ),
        }
        if numbers:
            summary.update(
                {
                    "min": min(numbers),
                    "max": max(numbers),
                    "mean": round(sum(numbers) / len(numbers), 4),
                }
            )
        stats[entity_id] = summary
    return stats


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}
    jarvis.data[DOMAIN] = {"days": int(config.get("days", DEFAULT_WINDOW_DAYS))}

    async def handle_get(call: ServiceCall) -> dict[str, Any]:
        start = call.get("start") or call.get("start_time")
        end = call.get("end") or call.get("end_time")
        history = await get_history(
            jarvis,
            entity_ids=call.get("entity_id"),
            start=start,
            end=end,
            include_start_time_state=bool(call.get("include_start_time_state", True)),
            minimal_response=bool(call.get("minimal_response", False)),
        )
        # Report the window that was really queried, not the (possibly
        # absent) one the caller typed.
        start_ts, end_ts = resolve_window(jarvis, start, end)
        return {
            "history": history,
            "start": as_iso(start_ts),
            "end": as_iso(end_ts),
        }

    async def handle_stats(call: ServiceCall) -> dict[str, Any]:
        return {
            "stats": await get_stats(
                jarvis,
                entity_ids=call.get("entity_id"),
                start=call.get("start"),
                end=call.get("end"),
            )
        }

    jarvis.services.register(
        DOMAIN,
        "get",
        handle_get,
        description="Return recorded state history for one or more entities.",
        fields={
            "entity_id": {"description": "Entity id or list of entity ids."},
            "start": {"description": "Window start (ISO 8601 or epoch seconds)."},
            "end": {"description": "Window end (ISO 8601 or epoch seconds)."},
            "minimal_response": {"description": "Drop attributes after the first row."},
            "include_start_time_state": {
                "description": "Prefix each series with the state at `start`."
            },
        },
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN,
        "stats",
        handle_stats,
        description="Summarise recorded history (min/max/mean/changes).",
        fields={
            "entity_id": {"description": "Entity id or list of entity ids."},
            "start": {"description": "Window start (ISO 8601 or epoch seconds)."},
            "end": {"description": "Window end (ISO 8601 or epoch seconds)."},
        },
        supports_response=True,
    )
    return True
