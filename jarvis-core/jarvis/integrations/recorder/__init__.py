"""Recorder — durable history of everything Jarvis has seen.

A single SQLite file (stdlib :mod:`sqlite3`, driven off the loop with
:func:`asyncio.to_thread`) holding two tables:

``states``
    one row per state change — ``entity_id``, ``state``, ``attributes``
    (JSON), ``last_changed``, ``last_updated``, ``context_id``.
``events``
    one row per non-state event — ``event_type``, ``data`` (JSON),
    ``time_fired``, ``context_id``, ``origin``.

Writes are queued by a cheap synchronous bus listener and flushed in a
single transaction every ``commit_interval`` seconds, so a burst of state
changes costs one commit instead of hundreds.

Configuration::

    recorder:
      db_file: jarvis.db          # relative paths resolve under config_dir
      # db_url: sqlite:///abs/path.db   (equivalent, SQLAlchemy-ish spelling)
      purge_keep_days: 10
      commit_interval: 5          # 0 disables the background flush entirely:
                                  # rows then only reach disk when something
                                  # queries or on shutdown. Useful in tests,
                                  # a footgun in production.
      auto_purge: true            # nightly purge at 04:12 local
      exclude:
        domains: [sensor]
        entities: [light.noisy_strip]
        entity_globs: ["sensor.*_rssi"]
        event_types: [call_service]
      include:
        domains: [light, switch]
        entities: [sensor.outside_temperature]

Filter precedence (most specific wins): ``exclude.entities`` →
``include.entities`` → ``entity_globs`` → ``exclude.domains`` →
``include.domains``. With no ``include`` block everything not excluded is
recorded.

Services
    ``recorder.purge`` (keep_days, repack), ``recorder.purge_entities``
    (entity_id, domains).

The live object is stored at ``jarvis.data["recorder"]`` and exposes the
query API the ``history`` and ``logbook`` integrations build on:
:meth:`Recorder.states_between` and :meth:`Recorder.events_between`.
:meth:`Recorder.async_history_period` is the shape the REST layer
duck-types for ``GET /api/history/period``.

All sqlite work runs on a worker thread under a single lock, and a job
already handed to that thread is awaited even if the task that submitted it
is cancelled — otherwise a shutdown closes the database mid-write.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import sqlite3
import time
from collections import deque
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from ...const import EVENT_STATE_CHANGED, MATCH_ALL
from ...services import ServiceCall
from ...state import split_entity_id

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "recorder"

DEFAULT_DB_FILE = "jarvis.db"
DEFAULT_PURGE_KEEP_DAYS = 10
DEFAULT_COMMIT_INTERVAL = 5.0
DEFAULT_MAX_QUEUE = 50_000
MAX_JSON_BYTES = 32_768

# Nightly purge time (local clock).
PURGE_HOUR = 4
PURGE_MINUTE = 12

# Bookkeeping chatter that would only bloat the database.
DEFAULT_EXCLUDED_EVENTS = frozenset(
    {
        EVENT_STATE_CHANGED,  # recorded into `states` instead
        "service_registered",
        "entity_registry_updated",
        "device_registry_updated",
        "area_registry_updated",
        "time_changed",
    }
)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT NOT NULL,
        state TEXT NOT NULL,
        attributes TEXT,
        last_changed REAL NOT NULL,
        last_updated REAL NOT NULL,
        context_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        data TEXT,
        time_fired REAL NOT NULL,
        context_id TEXT,
        origin TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_states_entity_updated ON states (entity_id, last_updated)",
    "CREATE INDEX IF NOT EXISTS ix_states_updated ON states (last_updated)",
    "CREATE INDEX IF NOT EXISTS ix_events_fired ON events (time_fired)",
    "CREATE INDEX IF NOT EXISTS ix_events_type_fired ON events (event_type, time_fired)",
)


# --- time helpers ----------------------------------------------------------
def as_timestamp(value: Any, default: float | None = None) -> float | None:
    """Coerce floats / datetimes / ISO strings to an epoch timestamp.

    Naive datetimes are interpreted as UTC so results never depend on the
    machine's local clock settings.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, date_cls):
        return datetime(
            value.year, value.month, value.day, tzinfo=timezone.utc
        ).timestamp()
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as err:
            raise ValueError(f"cannot parse timestamp: {value!r}") from err
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise ValueError(f"cannot parse timestamp: {value!r}")


def as_iso(timestamp: float | None) -> str | None:
    """Epoch seconds → UTC ISO-8601 string (what HTTP clients want)."""
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dumps(value: Any) -> str:
    try:
        text = json.dumps(value, default=_json_default)
    except (TypeError, ValueError):
        return json.dumps({"unserializable": True})
    if len(text) > MAX_JSON_BYTES:
        return json.dumps({"truncated": True, "bytes": len(text)})
    return text


def _loads(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


# --- filtering -------------------------------------------------------------
class EntityFilter:
    """Decides whether an entity_id is worth writing to disk."""

    def __init__(
        self,
        include: dict[str, Any] | None = None,
        exclude: dict[str, Any] | None = None,
    ) -> None:
        include = include or {}
        exclude = exclude or {}
        self.include_domains = set(_as_list(include.get("domains")))
        self.include_entities = {e.lower() for e in _as_list(include.get("entities"))}
        self.include_globs = _as_list(include.get("entity_globs"))
        self.exclude_domains = set(_as_list(exclude.get("domains")))
        self.exclude_entities = {e.lower() for e in _as_list(exclude.get("entities"))}
        self.exclude_globs = _as_list(exclude.get("entity_globs"))
        self.has_include = bool(
            self.include_domains or self.include_entities or self.include_globs
        )
        self._cache: dict[str, bool] = {}

    def __call__(self, entity_id: str) -> bool:
        cached = self._cache.get(entity_id)
        if cached is None:
            cached = self._evaluate(entity_id)
            self._cache[entity_id] = cached
        return cached

    def _evaluate(self, entity_id: str) -> bool:
        entity_id = entity_id.lower()
        domain = split_entity_id(entity_id)[0]

        # Entity-level rules beat everything else.
        if entity_id in self.exclude_entities:
            return False
        if entity_id in self.include_entities:
            return True
        if any(fnmatch.fnmatch(entity_id, g) for g in self.exclude_globs):
            return False
        if any(fnmatch.fnmatch(entity_id, g) for g in self.include_globs):
            return True

        if domain in self.exclude_domains:
            return False
        if self.include_domains:
            return domain in self.include_domains
        # An include block listing only entities/globs means "nothing else".
        if self.has_include:
            return False
        return True


# --- the recorder ----------------------------------------------------------
class Recorder:
    """Owns the SQLite connection, the write queue and the query API."""

    def __init__(self, jarvis: "Jarvis", config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.jarvis = jarvis
        self.config = config
        self.db_path = _resolve_db_path(jarvis, config)
        self.purge_keep_days = int(config.get("purge_keep_days", DEFAULT_PURGE_KEEP_DAYS))
        self.commit_interval = float(
            config.get("commit_interval", DEFAULT_COMMIT_INTERVAL)
        )
        self.auto_purge = bool(config.get("auto_purge", True))
        self.max_queue = int(config.get("max_queue", DEFAULT_MAX_QUEUE))
        self.filter = EntityFilter(config.get("include"), config.get("exclude"))

        include = config.get("include") or {}
        exclude = config.get("exclude") or {}
        self.include_event_types = set(_as_list(include.get("event_types")))
        self.exclude_event_types = set(
            _as_list(exclude.get("event_types"))
        ) | set(DEFAULT_EXCLUDED_EVENTS)

        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._state_queue: deque[tuple] = deque()
        self._event_queue: deque[tuple] = deque()
        self._commit_task: asyncio.Task | None = None
        self._purge_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._unsub: Any = None
        self._dropped = 0
        self.recording = True

    # --- lifecycle ----------------------------------------------------
    async def async_setup(self) -> None:
        await asyncio.to_thread(self._connect)
        self._unsub = self.jarvis.bus.listen(MATCH_ALL, self._queue_event)
        if self.commit_interval > 0:
            self._commit_task = self.jarvis.async_create_task(self._commit_loop())
        if self.auto_purge:
            self._purge_task = self.jarvis.async_create_task(self._purge_loop())
        self.jarvis.register_shutdown(self.async_shutdown)
        _LOGGER.info("Recorder writing to %s", self.db_path)

    def _connect(self) -> None:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # Serialised by self._lock, so sharing across to_thread workers is safe.
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        for statement in SCHEMA:
            conn.execute(statement)
        conn.commit()
        self._conn = conn

    async def async_shutdown(self) -> None:
        self.recording = False
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

        # Ask the flush loop to stop rather than cancelling it: cancelling
        # a task that is mid-write unwinds it (releasing the lock) while its
        # worker thread is still inside sqlite, and the close below would
        # then pull the connection out from under that thread.
        self._stopping.set()
        commit_task, self._commit_task = self._commit_task, None
        if commit_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await commit_task

        purge_task, self._purge_task = self._purge_task, None
        if purge_task is not None:
            purge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await purge_task

        await self.async_commit()
        # Held across the close, so no read or write can straddle it.
        await self._run_locked(self._close)

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    # --- capture ------------------------------------------------------
    def _queue_event(self, event: Any) -> None:
        """Bus listener. Deliberately synchronous and allocation-light."""
        if not self.recording:
            return
        try:
            if event.event_type == EVENT_STATE_CHANGED:
                self._queue_state_change(event)
            else:
                self._queue_plain_event(event)
        except Exception:  # never let a bad payload break the bus
            _LOGGER.exception("Recorder failed to queue %s", event.event_type)

    def _queue_state_change(self, event: Any) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:  # entity removed — nothing meaningful to store
            return
        entity_id = getattr(new_state, "entity_id", None) or event.data.get("entity_id")
        if not entity_id or not self.filter(entity_id):
            return
        if len(self._state_queue) >= self.max_queue:
            self._dropped += 1
            return
        self._state_queue.append(
            (
                entity_id,
                str(getattr(new_state, "state", "")),
                _dumps(getattr(new_state, "attributes", {}) or {}),
                float(getattr(new_state, "last_changed", event.time_fired)),
                float(getattr(new_state, "last_updated", event.time_fired)),
                getattr(getattr(new_state, "context", None), "id", None),
            )
        )

    def _queue_plain_event(self, event: Any) -> None:
        event_type = event.event_type
        if event_type in self.exclude_event_types:
            return
        if self.include_event_types and event_type not in self.include_event_types:
            return
        if len(self._event_queue) >= self.max_queue:
            self._dropped += 1
            return
        context = getattr(event, "context", None)
        self._event_queue.append(
            (
                event_type,
                _dumps(event.data or {}),
                float(event.time_fired),
                getattr(context, "id", None),
                getattr(context, "origin", None),
            )
        )

    # --- writing ------------------------------------------------------
    async def _await_uncancellable(self, job: asyncio.Task) -> Any:
        """Await thread work that cannot actually be recalled once submitted.

        A worker thread runs to completion whatever happens to the task
        awaiting it. If we simply propagated the cancellation we would leave
        sqlite busy while our caller believes the connection is idle — which
        is how a shutdown used to close the database mid-write.
        """
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            with contextlib.suppress(BaseException):
                await asyncio.wait([job])
            raise

    async def _run_locked(self, func: Any, *args: Any) -> Any:
        """Run blocking sqlite work under the connection lock."""
        async with self._lock:
            return await self._await_uncancellable(
                asyncio.ensure_future(asyncio.to_thread(func, *args))
            )

    async def _commit_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.commit_interval
                    )
                await self.async_commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient sqlite failure (locked database, full disk)
                # must not kill the flush loop for the rest of the process
                # lifetime — the rows are back on the queue, so retry.
                _LOGGER.exception("Recorder commit failed; will retry")

    def _requeue(self, states: list[tuple], events: list[tuple]) -> None:
        """Put rows that failed to write back at the head of the queues."""
        for queue, rows in ((self._state_queue, states), (self._event_queue, events)):
            room = max(self.max_queue - len(queue), 0)
            if len(rows) > room:  # keep the newest, count the rest as dropped
                self._dropped += len(rows) - room
                rows = rows[len(rows) - room :]
            queue.extendleft(reversed(rows))

    async def async_commit(self) -> int:
        """Flush queued rows to disk. Returns the number of rows written."""
        if self._conn is None:
            return 0
        if not self._state_queue and not self._event_queue:
            return 0
        states = list(self._state_queue)
        self._state_queue.clear()
        events = list(self._event_queue)
        self._event_queue.clear()
        if self._dropped:
            _LOGGER.warning("Recorder dropped %d rows (queue full)", self._dropped)
            self._dropped = 0
        handed_off = False
        try:
            async with self._lock:
                job = asyncio.ensure_future(
                    asyncio.to_thread(self._write_sync, states, events)
                )
                handed_off = True
                return await self._await_uncancellable(job)
        except asyncio.CancelledError:
            # Rows already handed to a worker thread are on their way to
            # disk — requeueing them here would write them twice.
            if not handed_off:
                self._requeue(states, events)
            raise
        except Exception:
            # A failed write must not silently swallow the rows it carried.
            self._requeue(states, events)
            raise

    def _write_sync(self, states: list[tuple], events: list[tuple]) -> int:
        conn = self._conn
        if conn is None:
            _LOGGER.warning(
                "Recorder asked to write %d rows after the database was closed",
                len(states) + len(events),
            )
            return 0
        if states:
            conn.executemany(
                "INSERT INTO states "
                "(entity_id, state, attributes, last_changed, last_updated, context_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                states,
            )
        if events:
            conn.executemany(
                "INSERT INTO events (event_type, data, time_fired, context_id, origin) "
                "VALUES (?, ?, ?, ?, ?)",
                events,
            )
        conn.commit()
        return len(states) + len(events)

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return await self._run_locked(self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        conn = self._conn
        if conn is None:
            # An empty result here is indistinguishable from "no history",
            # so say out loud that the answer is missing, not empty.
            _LOGGER.warning("Recorder query issued after the database was closed")
            return []
        cursor = conn.execute(sql, tuple(params))
        rows = cursor.fetchall()
        cursor.close()
        return rows

    # --- queries ------------------------------------------------------
    async def states_between(
        self,
        entity_ids: Iterable[str] | str | None = None,
        start: Any = None,
        end: Any = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Recorded states in ``[start, end]``, oldest first."""
        await self.async_commit()
        start_ts = as_timestamp(start, 0.0)
        end_ts = as_timestamp(end, time.time())
        sql = (
            "SELECT entity_id, state, attributes, last_changed, last_updated, context_id "
            "FROM states WHERE last_updated >= ? AND last_updated <= ?"
        )
        params: list[Any] = [start_ts, end_ts]
        ids = _as_list(entity_ids)
        if ids:
            sql += f" AND entity_id IN ({','.join('?' * len(ids))})"
            params.extend(i.lower() for i in ids)
        sql += " ORDER BY last_updated ASC, id ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_state_row(row) for row in await self._execute(sql, params)]

    async def last_state_before(
        self, entity_ids: Iterable[str] | str, when: Any = None
    ) -> dict[str, dict[str, Any]]:
        """The most recent recorded state per entity strictly before ``when``."""
        await self.async_commit()
        when_ts = as_timestamp(when, time.time())
        out: dict[str, dict[str, Any]] = {}
        for entity_id in _as_list(entity_ids):
            # Keyed by the canonical (lower-case) id so callers can merge
            # this with states_between() without ending up with two series
            # for the same entity.
            key = entity_id.lower()
            rows = await self._execute(
                "SELECT entity_id, state, attributes, last_changed, last_updated, "
                "context_id FROM states WHERE entity_id = ? AND last_updated < ? "
                "ORDER BY last_updated DESC, id DESC LIMIT 1",
                (key, when_ts),
            )
            if rows:
                out[key] = _state_row(rows[0])
        return out

    async def events_between(
        self,
        start: Any = None,
        end: Any = None,
        event_types: Iterable[str] | str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Recorded events in ``[start, end]``, oldest first."""
        await self.async_commit()
        start_ts = as_timestamp(start, 0.0)
        end_ts = as_timestamp(end, time.time())
        sql = (
            "SELECT event_type, data, time_fired, context_id, origin "
            "FROM events WHERE time_fired >= ? AND time_fired <= ?"
        )
        params: list[Any] = [start_ts, end_ts]
        types = _as_list(event_types)
        if types:
            sql += f" AND event_type IN ({','.join('?' * len(types))})"
            params.extend(types)
        sql += " ORDER BY time_fired ASC, id ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_event_row(row) for row in await self._execute(sql, params)]

    async def async_history_period(
        self,
        entity_ids: Iterable[str] | str | None = None,
        start_time: Any = None,
        end_time: Any = None,
    ) -> list[list[dict[str, Any]]]:
        """One series per entity, oldest first — the shape ``/api/history/period``
        hands straight back to a client.

        The REST layer duck-types the recorder looking for this method (see
        ``jarvis/api/common.py``); without it every history request answers
        with an empty list.
        """
        end_ts = as_timestamp(end_time, time.time())
        start_ts = as_timestamp(start_time, (end_ts or time.time()) - 86400.0)
        ids = [e.lower() for e in _as_list(entity_ids)]
        if not ids:
            ids = await self.recorded_entity_ids()

        series: dict[str, list[dict[str, Any]]] = {entity_id: [] for entity_id in ids}
        for entity_id, prior in (await self.last_state_before(ids, start_ts)).items():
            series.setdefault(entity_id, []).append(prior)
        for row in await self.states_between(ids, start_ts, end_ts):
            series.setdefault(row["entity_id"], []).append(row)
        return [rows for rows in series.values() if rows]

    async def recorded_entity_ids(self) -> list[str]:
        await self.async_commit()
        rows = await self._execute("SELECT DISTINCT entity_id FROM states ORDER BY 1")
        return [row["entity_id"] for row in rows]

    async def row_counts(self) -> dict[str, int]:
        await self.async_commit()
        states = await self._execute("SELECT COUNT(*) AS n FROM states")
        events = await self._execute("SELECT COUNT(*) AS n FROM events")
        return {
            "states": states[0]["n"] if states else 0,
            "events": events[0]["n"] if events else 0,
        }

    # --- maintenance --------------------------------------------------
    async def purge(self, keep_days: int | None = None, repack: bool = False) -> int:
        """Delete rows older than ``keep_days``. Returns rows removed."""
        await self.async_commit()
        days = self.purge_keep_days if keep_days is None else int(keep_days)
        cutoff = time.time() - days * 86400
        removed = await self._run_locked(self._purge_sync, cutoff, repack)
        _LOGGER.info("Purged %d rows older than %d days", removed, days)
        return removed

    def _purge_sync(self, cutoff: float, repack: bool) -> int:
        conn = self._conn
        if conn is None:
            return 0
        removed = conn.execute(
            "DELETE FROM states WHERE last_updated < ?", (cutoff,)
        ).rowcount
        removed += conn.execute(
            "DELETE FROM events WHERE time_fired < ?", (cutoff,)
        ).rowcount
        conn.commit()
        if repack:
            conn.execute("VACUUM")
            conn.commit()
        return max(removed, 0)

    async def purge_entities(
        self,
        entity_ids: Iterable[str] | str | None = None,
        domains: Iterable[str] | str | None = None,
    ) -> int:
        """Delete every recorded state for the given entities / domains."""
        await self.async_commit()
        ids = [e.lower() for e in _as_list(entity_ids)]
        doms = [d.lower() for d in _as_list(domains)]
        if not ids and not doms:
            return 0
        removed = await self._run_locked(self._purge_entities_sync, ids, doms)
        return removed

    def _purge_entities_sync(self, ids: list[str], domains: list[str]) -> int:
        conn = self._conn
        if conn is None:
            return 0
        removed = 0
        if ids:
            placeholders = ",".join("?" * len(ids))
            removed += conn.execute(
                f"DELETE FROM states WHERE entity_id IN ({placeholders})", ids
            ).rowcount
        for domain in domains:
            removed += conn.execute(
                "DELETE FROM states WHERE entity_id LIKE ?", (f"{domain}.%",)
            ).rowcount
        conn.commit()
        return max(removed, 0)

    async def _purge_loop(self) -> None:
        """Run a purge every night at PURGE_HOUR:PURGE_MINUTE local time."""
        try:
            while True:
                await asyncio.sleep(_seconds_until_next_purge())
                try:
                    await self.purge(repack=False)
                except Exception:
                    _LOGGER.exception("Nightly purge failed")
        except asyncio.CancelledError:
            raise


def _seconds_until_next_purge(now: datetime | None = None) -> float:
    now = now or datetime.now()
    target = now.replace(hour=PURGE_HOUR, minute=PURGE_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), 1.0)


def _state_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entity_id": row["entity_id"],
        "state": row["state"],
        "attributes": _loads(row["attributes"]),
        "last_changed": row["last_changed"],
        "last_updated": row["last_updated"],
        "last_changed_iso": as_iso(row["last_changed"]),
        "last_updated_iso": as_iso(row["last_updated"]),
        "context_id": row["context_id"],
    }


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_type": row["event_type"],
        "data": _loads(row["data"]),
        "time_fired": row["time_fired"],
        "time_fired_iso": as_iso(row["time_fired"]),
        "context_id": row["context_id"],
        "origin": row["origin"],
    }


def _resolve_db_path(jarvis: "Jarvis", config: dict[str, Any]) -> str:
    """Accept db_file, or a sqlite:// db_url, and resolve under config_dir."""
    db_url = config.get("db_url")
    if db_url:
        url = str(db_url)
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///") :]
        elif url.startswith("sqlite://"):
            raw = url[len("sqlite://") :] or ":memory:"
        else:
            _LOGGER.warning(
                "recorder: only sqlite db_url is supported (got %r); using %s",
                url, DEFAULT_DB_FILE,
            )
            raw = DEFAULT_DB_FILE
    else:
        raw = str(config.get("db_file", DEFAULT_DB_FILE))

    if raw in (":memory:", ""):
        return ":memory:"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(jarvis.config_dir) / path
    return str(path)


def get_recorder(jarvis: "Jarvis") -> Recorder | None:
    """The live recorder, or None when persistence is not configured."""
    return jarvis.data.get(DOMAIN)


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    if not isinstance(config, dict):
        config = {}

    recorder = Recorder(jarvis, config)
    await recorder.async_setup()
    jarvis.data[DOMAIN] = recorder

    async def handle_purge(call: ServiceCall) -> dict[str, Any]:
        removed = await recorder.purge(
            keep_days=call.get("keep_days"), repack=bool(call.get("repack", False))
        )
        return {"removed": removed}

    async def handle_purge_entities(call: ServiceCall) -> dict[str, Any]:
        removed = await recorder.purge_entities(
            entity_ids=call.get("entity_id"), domains=call.get("domains")
        )
        return {"removed": removed}

    jarvis.services.register(
        DOMAIN,
        "purge",
        handle_purge,
        description="Delete recorded history older than keep_days.",
        fields={
            "keep_days": {"description": "Days of history to keep.", "example": 10},
            "repack": {"description": "VACUUM the database afterwards.", "example": False},
        },
        supports_response=True,
    )
    jarvis.services.register(
        DOMAIN,
        "purge_entities",
        handle_purge_entities,
        description="Delete all recorded history for entities or whole domains.",
        fields={
            "entity_id": {"description": "Entity id or list of entity ids."},
            "domains": {"description": "List of domains to purge entirely."},
        },
        supports_response=True,
    )
    return True
