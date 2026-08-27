"""Overnight reflection (M87): what Jarvis learned today, kept and shown.

Extraction (`MemoryStore.async_extract`) keeps a fact the moment it is said,
one turn at a time, and so keeps the same fact twice in two wordings — "the
user's name is Chase" beside "the speaker's name is Chase" (the agentic
audit, 27 Aug 2026) — and never looks back over a day. This does the second
thing: once a night (or on demand) the day's conversations are read from the
archive, the model is asked ONCE for the few durable facts in them that are
not already known, each is stored with `source: learned`, and one note plus
one inbox card say what was learned, so a person can read it and forget any
fact on the Memory screen the way they forget any other.

What it does not do: it never re-learns something the user asked to forget
(the day's forgotten texts are excluded from the prompt and the answer), it
never reads a channel or background conversation, it never reads a turn that
carried untrusted content (those are fenced in the archive), and it writes
nothing when there is nothing new — no "I learned nothing today" cards.

    memory:
      reflect_at: "03:30"      # local time; absent means on demand only
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING, Any, Iterable

from ...automation.util import get_clock, next_time_of_day
from ...const import EVENT_JARVIS_START

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from . import MemoryStore

_LOGGER = logging.getLogger(__name__)

#: Facts kept per reflection; more than this is a diary, not a memory.
MAX_LEARNED = 5
#: How far back a reflection reads.
WINDOW_SECONDS = 24 * 3600.0
#: What a turn must have to be worth the model's time, and how much of a day
#: is put in front of it.
MIN_TURN_CHARS = 12
MAX_PROMPT_CHARS = 6000
SOURCE = "learned"
EVENT_REFLECTED = "jarvis_memory_reflected"

REFLECT_PROMPT = """Here is what somebody said to their home assistant today, one line per turn:

{turns}

These facts about them are ALREADY known — do not repeat or reword any of them:
{known}

Which DURABLE FACTS ABOUT THEM are new in today's turns — a preference, a person,
a place, a routine, a standing instruction — that would still be true next month?

Rules:
- Facts about the speaker only. Not the weather, not the house's state, not a
  one-off request, not anything the assistant said or did.
- One short sentence each, in the third person, as if noting it down.
- At most {limit}. Usually there are none; say so rather than inventing one.

Answer with JSON only: {{"facts": []}} or {{"facts": ["...", "..."]}}
"""


def _skip(conversation_id: str) -> bool:
    cid = str(conversation_id or "")
    return cid.startswith(("channel:", "background-", "test:", "audit-"))


class Reflection:
    """One reflection at a time, on a schedule or on demand."""

    def __init__(self, jarvis: "Jarvis", memory: "MemoryStore", at: str | None) -> None:
        self.jarvis = jarvis
        self.memory = memory
        self.at = str(at).strip() if at else None
        #: `at` as a time of day, or None when it is unset or unreadable. Parsed
        #: here, once: the schedule handed the raw string to `next_time_of_day`
        #: and every start of the house ended the reflection task with
        #: "'str' object has no attribute 'hour'" — logged only when the task
        #: was collected at shutdown, so the nightly reflection never ran and
        #: nothing said so until the container logs were read.
        self.time_of_day: dt_time | None = self.parse_time_of_day(self.at)
        if self.at and self.time_of_day is None:
            _LOGGER.error(
                "memory: reflect_at %r is not a time of day (HH:MM); no reflection is scheduled",
                self.at,
            )
        self.last: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # --- the schedule ------------------------------------------------------
    @staticmethod
    def parse_time_of_day(value: str | None) -> dt_time | None:
        """`"03:30"` → 03:30; anything else → None. Seconds are accepted, not required."""
        text = str(value or "").strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        return None

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
        except Exception:
            _LOGGER.exception("memory: the reflection schedule did not stop cleanly")

    async def _run(self) -> None:
        clock = get_clock(self.jarvis)
        assert self.time_of_day is not None  # start() refuses to schedule without one
        while True:
            when = next_time_of_day(clock.now(), self.time_of_day)
            await clock.sleep(max(1.0, (when - clock.now()).total_seconds()))
            try:
                await self.reflect()
            except asyncio.CancelledError:
                raise
            except Exception:  # a failed night must not end the schedule
                _LOGGER.exception("memory: the overnight reflection failed")

    # --- the reflection ------------------------------------------------------
    def _turns(self, since: float) -> list[tuple[str, str]]:
        """`(speaker, text)` for every user turn of the day worth reading.

        The speaker is who the voice gate recognised (M100), "" for a typed or
        unverified turn — the reflection asks about each person separately, so
        Ted's tea is filed under Ted and never under Chase.
        """
        agent = self.jarvis.data.get("llm")
        archive = getattr(agent, "archive", None)
        out: list[tuple[str, str]] = []
        for summary in (archive.listing() if archive is not None else []):
            cid = str(summary.get("id") or "")
            if _skip(cid) or float(summary.get("last_active") or 0.0) < since:
                continue
            conversation = archive.get(cid)
            for turn in getattr(conversation, "turns", []) or []:
                if turn.role != "user" or float(turn.timestamp or 0.0) < since:
                    continue
                text = " ".join(str(turn.content or "").split())
                if len(text) >= MIN_TURN_CHARS and "<untrusted" not in text:
                    out.append((str(getattr(turn, "speaker", "") or ""), text))
        return out

    def _known(self) -> list[str]:
        return [e.text for e in self.memory.entries]

    async def reflect(self, now: float | None = None) -> dict[str, Any]:
        """Read the day, ask once, keep what is new. Returns what happened."""
        async with self._lock:
            now = float(now if now is not None else time.time())
            since = now - WINDOW_SECONDS
            turns = self._turns(since)
            result: dict[str, Any] = {
                "status": "ok", "turns": len(turns), "learned": [], "skipped": [], "at": now,
            }
            if not turns:
                result["reason"] = "nothing said today"
                self.last = result
                return result
            agent = self.jarvis.data.get("llm")
            ask = getattr(agent, "ask_once", None)
            if not callable(ask):
                result.update(status="error", reason="no model to ask")
                self.last = result
                return result
            known = self._known()
            from . import MemoryEntry, _parse_facts, one_line

            day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            # One ask per person who spoke (and one for the turns nobody was
            # recognised on): a single prompt over everybody's day filed Ted's
            # tea under "the user", which is to say under Chase.
            by_person: dict[str, list[str]] = {}
            for who, text in turns:
                by_person.setdefault(who, []).append(text)
            for who, said in by_person.items():
                body = "\n".join(f"- {t}" for t in said)[-MAX_PROMPT_CHARS:]
                if who:
                    body = f"(These were all said by {who}.)\n" + body
                prompt = REFLECT_PROMPT.format(
                    turns=body,
                    known="\n".join(f"- {k}" for k in known[-40:]) or "- (nothing yet)",
                    limit=MAX_LEARNED,
                )
                try:
                    raw = await ask(prompt)
                except Exception as err:  # noqa: BLE001 - reported, never raised out of the night
                    result.update(status="error", reason=f"the model did not answer: {err}")
                    self.last = result
                    return result
                facts = _parse_facts(raw)[:MAX_LEARNED]
                for fact in facts:
                    if self.memory.was_forgotten(fact):
                        result["skipped"].append({"fact": fact, "reason": "the user asked to forget this"})
                        continue
                    # Known already, in these words or near enough: the store's own
                    # duplicate test, which `async_add` would use to REPLACE the
                    # older entry — a reflection must not rewrite what the user said
                    # in its own wording.
                    twin = self.memory._duplicate_of(MemoryEntry(id="reflection", text=fact, source=SOURCE, person=who))
                    if twin is not None or one_line(fact).lower() in {one_line(e.text).lower() for e in self.memory.entries}:
                        result["skipped"].append({"fact": fact, "reason": f"already remembered as {twin.text!r}" if twin else "already remembered"})
                        continue
                    stored = await self.memory.async_add(
                        fact, tags=["learned", day], source=SOURCE, conversation_id="reflection", person=who
                    )
                    if stored.get("stored"):
                        result["learned"].append(f"{who}: {fact}" if who else fact)
                    else:
                        result["skipped"].append({"fact": fact, "reason": str(stored.get("reason") or "not stored")})
            merged = await self.consolidate()
            if merged:
                result["merged"] = merged
            if result["learned"]:
                await self._tell(day, result["learned"])
            self.jarvis.bus.fire(EVENT_REFLECTED, {k: v for k, v in result.items() if k != "at"})
            self.last = result
            return result

    async def consolidate(self, threshold: float = 0.92) -> list[dict[str, Any]]:
        """Fold near-duplicates of one person's facts into one entry.

        The store's own duplicate test is words — "The speaker's name is
        Chase" and "The user's name is Chase" passed it as two facts. With the
        embedding index up, two entries of the same person whose vectors sit
        above `threshold` are one fact: the newer (a pinned one wins) is kept,
        the other forgotten, and the card says so. Without an index nothing is
        merged and nothing is claimed.
        """
        memory = self.memory
        if getattr(memory, "vectors", None) is None:
            return []
        merged: list[dict[str, Any]] = []
        gone: set[str] = set()
        entries = sorted(memory.entries, key=lambda e: (not e.pinned, -e.created))
        for entry in entries:
            if entry.id in gone:
                continue
            try:
                scores = await memory.async_semantic_ids(entry.text)
            except Exception:  # noqa: BLE001 - a broken index merges nothing
                return merged
            for other_id, score in scores.items():
                if other_id == entry.id or other_id in gone or score < threshold:
                    continue
                other = memory.get(other_id)
                if other is None or other.person != entry.person or other.pinned:
                    continue
                gone.add(other_id)
                merged.append({"kept": entry.text, "dropped": other.text, "person": entry.person, "score": round(score, 3)})
        if gone:
            memory.entries = [e for e in memory.entries if e.id not in gone]
            await memory.async_save()
            await memory._async_reindex()
            for row in merged:
                memory._fire("consolidated", memory.get(next(e.id for e in memory.entries if e.text == row["kept"])) if any(e.text == row["kept"] for e in memory.entries) else None)
        return merged

    async def _tell(self, day: str, learned: Iterable[str]) -> None:
        lines = list(learned)
        body = "\n".join(f"- {line}" for line in lines)
        title = f"What I learned on {day}"
        services = self.jarvis.services
        if services.has_service("notes", "create"):
            try:
                await services.async_call(
                    "notes", "create",
                    {"title": title, "body": body + "\n\nForget any of these on the Memory screen."},
                    blocking=True, return_response=True,
                )
            except Exception:
                _LOGGER.exception("memory: could not write the reflection note")
        store = self.jarvis.data.get("notifications")
        add = getattr(store, "async_add", None)
        if callable(add):
            try:
                await add(
                    kind="reflection",
                    title=title,
                    body="; ".join(lines)[:600],
                    source=EVENT_REFLECTED,
                    link="/knowledge/memory",
                )
            except Exception:
                _LOGGER.exception("memory: could not record the reflection")


def attach(jarvis: "Jarvis", memory: "MemoryStore", options: dict[str, Any]) -> Reflection:
    """Build the reflection, register `memory.reflect`, start the schedule."""
    reflection = Reflection(jarvis, memory, options.get("reflect_at"))
    jarvis.data["memory_reflection"] = reflection

    async def handle_reflect(call: Any) -> dict[str, Any]:
        return await reflection.reflect()

    jarvis.services.register(
        "memory", "reflect", handle_reflect, supports_response=True,
        description="Read the day's conversations once and keep the new durable facts (M87).",
    )

    def _start(event: Any = None) -> None:
        reflection.start()

    if jarvis.is_running:
        reflection.start()
    else:
        jarvis.bus.listen(EVENT_JARVIS_START, _start)
    jarvis.register_shutdown(reflection.stop)
    if reflection.at:
        _LOGGER.info("memory: overnight reflection at %s", reflection.at)
    return reflection


__all__ = ["Reflection", "attach", "REFLECT_PROMPT", "MAX_LEARNED", "SOURCE", "EVENT_REFLECTED"]
