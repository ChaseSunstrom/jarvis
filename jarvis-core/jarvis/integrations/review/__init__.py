"""review — Jarvis learns from its own mistakes (M102).

    review:
      at: "03:45"        # the nightly hour, in the house's zone; unset for on demand only

The nightly reflection (M87) learns facts about the person. Nothing learned
from the day's FAILURES: a tool that errored, a turn the claimed-action guard
caught saying something was done, a run the person stopped, a model server
that could not be reached. This reads that record once a night — and on
demand (`review.run`, the rig's ``review: true``) — asks the model once for
the few things it should do differently, and leaves a note ("What went wrong
on <day>") and a card the person can read. "What did you get wrong today?"
answers from the record (`what_went_wrong`), never from a guess.

The record is two things: the observability recorder's traces (spans that
errored, with the tool's name and the error) and this integration's own day
log of bus events — ``jarvis_turn_guarded`` (the agent's guard) and
``jarvis_run_stopped`` (a run ended by `assist_pipeline/stop`). The log is
kept on disk so a restart does not lose the morning's mistakes before the
night reads them.

What this does NOT do: change any rule by itself. A lesson is a sentence for
the person; the model reads the last review's lessons in the note like any
other note, and the person decides what to keep.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING, Any

from ...automation.util import get_clock, next_time_of_day
from ...const import EVENT_JARVIS_START
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "review"
DEPENDENCIES: list[str] = []
STORAGE_KEY = "review"
STORAGE_VERSION = 1

EVENT_GUARDED = "jarvis_turn_guarded"
EVENT_STOPPED = "jarvis_run_stopped"
EVENT_REVIEWED = "jarvis_reviewed"

#: How far back a review reads.
WINDOW_SECONDS = 24 * 3600.0
#: Lessons kept per review; more than this is a lecture.
MAX_LESSONS = 3
#: Rows of the day put in front of the model, and rows kept on disk.
MAX_ROWS = 40
MAX_LOG = 500
MAX_PROMPT_CHARS = 6000

REVIEW_PROMPT = """You are reviewing your own day as a home assistant. Here is what went wrong today, one line per event:

{rows}

Write at most {limit} LESSONS — short, specific, in the first person, each one thing to do differently next time
("When a device is not in the house, say so instead of turning on something else"). Only from these events;
never invent one. If nothing here is a lesson, say so.

Answer with JSON only: {{"lessons": []}} or {{"lessons": ["...", "..."]}}
"""


def parse_lessons(raw: Any) -> list[str]:
    """The model's answer as sentences; anything unreadable is nothing."""
    text = str(raw or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    rows = payload.get("lessons") if isinstance(payload, dict) else None
    out: list[str] = []
    for row in rows or []:
        line = " ".join(str(row or "").split()).strip(" .")
        if 12 <= len(line) <= 300:
            out.append(line + ".")
    return out[:MAX_LESSONS]


class Review:
    """The day's mistakes, on disk, and one ask a night about them."""

    def __init__(self, jarvis: "Jarvis", at: str | None, store: Store | None = None) -> None:
        self.jarvis = jarvis
        self.at = str(at).strip() if at else None
        self.time_of_day: dt_time | None = self.parse_time_of_day(self.at)
        if self.at and self.time_of_day is None:
            _LOGGER.error("review: at %r is not a time of day (HH:MM); no review is scheduled", self.at)
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.rows: list[dict[str, Any]] = []
        self.last: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def parse_time_of_day(value: str | None) -> dt_time | None:
        text = str(value or "").strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        return None

    # --- the day log ---------------------------------------------------------
    async def async_load(self) -> None:
        data = await self.store.load()
        rows = (data or {}).get("rows") if isinstance(data, dict) else None
        self.rows = [r for r in (rows or []) if isinstance(r, dict)][-MAX_LOG:]
        last = (data or {}).get("last") if isinstance(data, dict) else None
        self.last = last if isinstance(last, dict) else None

    async def async_save(self) -> None:
        await self.store.save({"rows": self.rows[-MAX_LOG:], "last": self.last})

    def note(self, kind: str, detail: str, **extra: Any) -> dict[str, Any]:
        row = {"kind": str(kind), "at": time.time(), "detail": " ".join(str(detail or "").split())[:300], **extra}
        self.rows.append(row)
        self.rows = self.rows[-MAX_LOG:]
        self.jarvis.async_create_task(self.async_save())
        return row

    def _on_guarded(self, event: Any) -> None:
        data = dict(getattr(event, "data", {}) or {})
        self.note("guard", f"said {str(data.get('said') or '')[:120]!r} to {str(data.get('request') or '')[:120]!r} with no tool called")

    def _on_stopped(self, event: Any) -> None:
        data = dict(getattr(event, "data", {}) or {})
        self.note("stopped", f"a run was stopped by the person after {float(data.get('seconds') or 0):.1f}s", conversation_id=str(data.get("conversation_id") or ""))

    # --- what the traces say -----------------------------------------------
    def _trace_rows(self, since: float) -> list[dict[str, Any]]:
        recorder = self.jarvis.data.get("observability")
        listing = getattr(recorder, "listing", None)
        get = getattr(recorder, "get", None)
        if not callable(listing) or not callable(get):
            return []
        out: list[dict[str, Any]] = []
        for summary in listing(limit=200):
            if float(summary.get("started") or 0.0) < since or not int(summary.get("errors") or 0):
                continue
            trace = get(str(summary.get("id") or "")) or {}
            for span in trace.get("spans") or []:
                if span.get("ok", True) or not span.get("error"):
                    continue
                error = " ".join(str(span.get("error") or "").split())[:200]
                kind = "unreachable" if "reach" in error.lower() else "tool-error"
                out.append({"kind": kind, "at": float(span.get("started") or summary.get("started") or 0.0),
                            "detail": f"{span.get('name')}: {error}"})
        return out

    def day(self, now: float | None = None) -> list[dict[str, Any]]:
        """Every row of the last day, traces and the log together, oldest first."""
        now = float(now if now is not None else time.time())
        since = now - WINDOW_SECONDS
        rows = [r for r in self.rows if float(r.get("at") or 0.0) >= since] + self._trace_rows(since)
        rows.sort(key=lambda r: float(r.get("at") or 0.0))
        return rows[-MAX_ROWS:]

    # --- the review ----------------------------------------------------------
    async def review(self, now: float | None = None) -> dict[str, Any]:
        async with self._lock:
            now = float(now if now is not None else time.time())
            rows = self.day(now)
            result: dict[str, Any] = {"status": "ok", "events": len(rows), "lessons": [], "at": now,
                                      "rows": rows[-12:]}
            if not rows:
                result["reason"] = "nothing went wrong today"
                self.last = result
                await self.async_save()
                return result
            agent = self.jarvis.data.get("llm")
            ask = getattr(agent, "ask_once", None)
            if not callable(ask):
                result.update(status="error", reason="no model to ask")
                self.last = result
                return result
            body = "\n".join(f"- [{r.get('kind')}] {r.get('detail')}" for r in rows)[-MAX_PROMPT_CHARS:]
            try:
                raw = await ask(REVIEW_PROMPT.format(rows=body, limit=MAX_LESSONS))
            except Exception as err:  # noqa: BLE001 - reported, never raised out of the night
                result.update(status="error", reason=f"the model did not answer: {err}")
                self.last = result
                return result
            result["lessons"] = parse_lessons(raw)
            day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            await self._tell(day, rows, result["lessons"])
            self.jarvis.bus.fire(EVENT_REVIEWED, {k: v for k, v in result.items() if k != "rows"})
            self.last = result
            await self.async_save()
            return result

    async def _tell(self, day: str, rows: list[dict[str, Any]], lessons: list[str]) -> None:
        title = f"What went wrong on {day}"
        lines = [f"- [{r.get('kind')}] {r.get('detail')}" for r in rows]
        body = "\n".join(lines)
        if lessons:
            body += "\n\nWhat I will do differently:\n" + "\n".join(f"- {line}" for line in lessons)
        else:
            body += "\n\nNothing here is a lesson."
        services = self.jarvis.services
        if services.has_service("notes", "create"):
            try:
                await services.async_call("notes", "create", {"title": title, "body": body}, blocking=True, return_response=True)
            except Exception:
                _LOGGER.exception("review: could not write the note")
        store = self.jarvis.data.get("notifications")
        add = getattr(store, "async_add", None)
        if callable(add):
            try:
                await add(kind="review", title=title,
                          body=("; ".join(lessons) if lessons else f"{len(rows)} thing(s) went wrong; nothing to learn from them")[:600],
                          source=EVENT_REVIEWED, link="/knowledge/notes")
            except Exception:
                _LOGGER.exception("review: could not record the card")

    # --- the schedule ------------------------------------------------------
    def start(self) -> None:
        if self._task is not None or self.time_of_day is None:
            return
        self._task = self.jarvis.async_create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        clock = get_clock(self.jarvis)
        assert self.time_of_day is not None
        while True:
            when = next_time_of_day(clock.now(), self.time_of_day)
            await clock.sleep(max(1.0, (when - clock.now()).total_seconds()))
            try:
                await self.review()
            except asyncio.CancelledError:
                raise
            except Exception:  # a failed night must not end the schedule
                _LOGGER.exception("review: the nightly review failed")


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    if config is None or config is False:
        return True
    options = config if isinstance(config, dict) else {}
    review = Review(jarvis, options.get("at"))
    await review.async_load()
    jarvis.data[DOMAIN] = review
    jarvis.bus.listen(EVENT_GUARDED, review._on_guarded)
    jarvis.bus.listen(EVENT_STOPPED, review._on_stopped)

    async def handle_run(call: Any) -> dict[str, Any]:
        return await review.review()

    jarvis.services.register(DOMAIN, "run", handle_run, supports_response=True,
                             description="Read the day's mistakes once and say what to do differently (M102).")

    registry = jarvis.data.get("llm_tools")
    if registry is not None:
        from ...llm.tools import TIER_DIRECT, schema_object

        async def tool_what_went_wrong(args: dict[str, Any], context: Any = None) -> Any:
            rows = review.day()
            last = review.last or {}
            return {
                "status": "ok",
                "count": len(rows),
                "events": [{"kind": r.get("kind"), "detail": r.get("detail")} for r in rows[-12:]],
                "lessons": list(last.get("lessons") or []),
                "message": ("Nothing went wrong in the last day." if not rows else
                            f"{len(rows)} thing(s) went wrong in the last day; read `events` back in your own words, "
                            "and `lessons` if the review has run. Do not add anything the record does not say."),
            }

        registry.register(
            name="what_went_wrong",
            description=(
                "What went wrong in the last day, from the record: tools that errored, things you said were done "
                "with no tool called, runs the user stopped, a model server that could not be reached — and the "
                "lessons the nightly review drew. For 'what did you get wrong today?' and 'why did that fail?'."
            ),
            parameters=schema_object({}, []),
            handler=tool_what_went_wrong,
            tier=TIER_DIRECT,
            read_only=True,
        )

    def _start(event: Any = None) -> None:
        review.start()

    if getattr(jarvis, "is_running", False):
        review.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, _start)
    jarvis.register_shutdown(review.stop)
    _LOGGER.info("review: %s", f"nightly at {review.at}" if review.time_of_day else "on demand only")
    return True


__all__ = ["DOMAIN", "EVENT_GUARDED", "EVENT_REVIEWED", "EVENT_STOPPED", "Review", "async_setup", "parse_lessons"]
