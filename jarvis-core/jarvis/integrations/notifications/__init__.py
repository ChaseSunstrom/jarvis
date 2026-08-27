"""notifications — the things Jarvis says without being asked, kept.

A briefing that was spoken to an empty room, a task that finished while you
were out, a reminder that fired on your phone: all of them happened, and none
of them left anything behind. `docs/AUDIT.md` §15 put it plainly — deliveries
are companion pushes and toasts, "not designed UI moments; no notification
record to retrieve". So "what did you tell me earlier?" had no answer, and
neither did "why am I seeing this?".

This is the record. Every proactive message becomes an entry::

    {"id": "9f2c…", "kind": "task", "title": "Research finished",
     "body": "Three sources agree…", "at": 1765…, "read": false,
     "source": "jarvis_task_completed", "link": "/tasks/abc123",
     "task_id": "abc123"}

kept in ``<config>/.storage/notifications.json``, fired on the bus as
``jarvis_notification`` so every surface can draw it as it arrives, and listed
over the websocket and REST so a surface that was closed can catch up.

Configuration (every key optional)::

    notifications:
      max_entries: 200      # oldest read entries fall off the end first
      kinds:                # what produces one; each may be switched off
        task: true          # a background job finished or failed
        reminder: true      # a scheduled notify fired
        briefing: true      # the morning/evening summary

## What this is not

It is not a second delivery channel. `companion.notify` still decides which
device gets a push and `routing.py` still decides whether to speak; this
records what was sent, and gives the console and the phone something to show
afterwards. A message that was never delivered anywhere still lands here,
which is deliberate: "it fired while you were asleep" is exactly the case a
record exists for.

It is also not a to-do list. Entries are marked read or dismissed and expire
off the end; nothing here is a task, and `tasks.py` owns those.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...const import (
    EVENT_TASK_CANCELLED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
)
from ...store import Store

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "notifications"
DEPENDENCIES: list[str] = []

STORAGE_KEY = "notifications"
STORAGE_VERSION = 1
DATA_STORE = "notifications"
#: Kinds of finished work worth a spoken word (M95); a reminder IS its own
#: announcement, and a `notify` task finishing is that reminder.
SPOKEN_KINDS = frozenset({"background", "research", "code"})

#: The bus event every surface listens to. One per record, as it is created.
EVENT_NOTIFICATION = "jarvis_notification"

DEFAULT_MAX_ENTRIES = 200
MAX_TITLE = 120
MAX_BODY = 1000

#: What can produce a notification, and whether it does by default. Named
#: rather than free-form so a surface can group them and an operator can turn
#: one off without turning off the feature.
#: `camera` is an NVR's event — "a person at the front door" from Frigate
#: (`vision.frigate`) — recorded so a household can see what the cameras
#: noticed while nobody was asking.
KINDS = ("task", "reminder", "briefing", "approval", "note", "camera")


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass
class Notification:
    """One thing Jarvis said without being asked."""

    id: str
    kind: str
    title: str
    body: str = ""
    at: float = field(default_factory=time.time)
    read: bool = False
    #: The bus event that produced it, so "why am I seeing this" has an answer
    #: that is not a guess.
    source: str = ""
    #: Where to go to see the thing itself.
    link: str = ""
    task_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "at": self.at,
            "read": self.read,
            "source": self.source,
            "link": self.link,
            "task_id": self.task_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Notification | None":
        if not isinstance(raw, dict):
            return None
        entry_id = _clip(raw.get("id"), 64)
        title = _clip(raw.get("title"), MAX_TITLE)
        if not entry_id or not title:
            return None
        try:
            at = float(raw.get("at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        return cls(
            id=entry_id,
            kind=_clip(raw.get("kind"), 32) or "task",
            title=title,
            body=_clip(raw.get("body"), MAX_BODY),
            at=at or time.time(),
            read=bool(raw.get("read")),
            source=_clip(raw.get("source"), 64),
            link=_clip(raw.get("link"), 200),
            task_id=_clip(raw.get("task_id"), 64),
        )


class NotificationStore:
    """Every proactive message, newest first."""

    def __init__(self, jarvis: "Jarvis", store: Store | None = None,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 kinds: dict[str, bool] | None = None) -> None:
        self.jarvis = jarvis
        self.store = store or Store(jarvis.config_dir, STORAGE_KEY, STORAGE_VERSION)
        self.max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
        #: Which kinds produce a record. Absent means yes.
        self.kinds = {str(k): bool(v) for k, v in (kinds or {}).items()}
        self.entries: list[Notification] = []

    def wanted(self, kind: str) -> bool:
        return self.kinds.get(str(kind), True)

    async def async_load(self) -> None:
        data = await self.store.load()
        raw = (data or {}).get("entries") if isinstance(data, dict) else None
        loaded = [n for n in (Notification.from_dict(item) for item in raw or []) if n]
        loaded.sort(key=lambda n: n.at)
        self.entries = loaded[-self.max_entries :]

    async def async_save(self) -> None:
        await self.store.save({"entries": [n.as_dict() for n in self.entries]})

    async def async_add(
        self,
        kind: str,
        title: str,
        body: str = "",
        source: str = "",
        link: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        kind = _clip(kind, 32) or "task"
        if not self.wanted(kind):
            return {"recorded": False, "reason": f"{kind} notifications are switched off"}
        title = _clip(title, MAX_TITLE)
        if not title:
            return {"recorded": False, "reason": "a notification needs a title"}
        entry = Notification(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            title=title,
            body=_clip(body, MAX_BODY),
            source=_clip(source, 64),
            link=_clip(link, 200),
            task_id=_clip(task_id, 64),
        )
        self.entries.append(entry)
        self._trim()
        await self.async_save()
        self._fire(entry)
        return {"recorded": True, "notification": entry.as_dict()}

    def _trim(self) -> None:
        """Oldest READ entries go first.

        A hundred unread notifications and one read one is not a reason to drop
        something the user has not seen — which is exactly what a plain "oldest
        first" would do on a busy day.
        """
        while len(self.entries) > self.max_entries:
            read = next((n for n in self.entries if n.read), None)
            self.entries.remove(read or self.entries[0])

    def _fire(self, entry: Notification) -> None:
        bus = getattr(self.jarvis, "bus", None)
        if bus is None:
            return
        try:
            bus.fire(EVENT_NOTIFICATION, {"notification": entry.as_dict()})
        except Exception:  # pragma: no cover - a listener must not break the record
            _LOGGER.exception("Could not fire %s", EVENT_NOTIFICATION)

    # --- reading ----------------------------------------------------------
    def listing(self, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        rows = [n for n in reversed(self.entries) if not unread_only or not n.read]
        return [n.as_dict() for n in rows[: max(1, int(limit))]]

    @property
    def unread(self) -> int:
        return sum(1 for n in self.entries if not n.read)

    def get(self, entry_id: str) -> Notification | None:
        wanted = _clip(entry_id, 64)
        return next((n for n in self.entries if n.id == wanted), None)

    # --- writing ----------------------------------------------------------
    async def async_mark_read(self, entry_id: str = "", everything: bool = False) -> dict:
        changed = 0
        for entry in self.entries:
            if everything or entry.id == _clip(entry_id, 64):
                if not entry.read:
                    entry.read = True
                    changed += 1
        if changed:
            await self.async_save()
        return {"read": changed, "unread": self.unread}

    async def async_dismiss(self, entry_id: str = "", everything: bool = False) -> dict:
        before = len(self.entries)
        if everything:
            self.entries = []
        else:
            wanted = _clip(entry_id, 64)
            self.entries = [n for n in self.entries if n.id != wanted]
        removed = before - len(self.entries)
        if removed:
            await self.async_save()
        return {"dismissed": removed}


def _listen(jarvis: "Jarvis", store: NotificationStore) -> None:
    """Turn the events that already exist into records.

    The lifecycle events came from M12 and were built for automations; this is
    the second consumer, and the reason they are separate events rather than
    one `jarvis_task_updated` firehose. Nothing here re-derives "did it just
    finish?" — the event IS that.
    """

    async def on_completed(event: Any) -> None:
        task = (event.data or {}).get("task") or {}
        if task.get("kind") == "notify":
            # A reminder finishing is the reminder; recording "the reminder
            # task completed" as well would put the same thing on the screen
            # twice, once in the user's words and once in ours.
            return
        # Said once, here, when the engine picked the job back up after a
        # restart (M85): the person asked for it before the restart and would
        # otherwise not know why it took so long, or that it ran twice.
        finished = "Finished (picked back up after a restart)" if task.get("resumed") else "Finished"
        await store.async_add(
            kind="task",
            title=f"{finished}: {task.get('title') or 'a background job'}",
            body=str(task.get("result") or "")[:MAX_BODY],
            source=EVENT_TASK_COMPLETED,
            link=f"/tasks#{task.get('id') or ''}",
            task_id=str(task.get("id") or ""),
        )
        # Finished work SPEAKS (M95). "Look into every sensor and tell me when
        # it is done" ended as an inbox card and nothing else — the tier
        # contract calls Tier-2 work "announced", and nothing announced it (the
        # agentic audit, 27 Aug 2026). companion.notify routes it: spoken if
        # the person is up and at a device, a quiet notification if not, queued
        # if unreachable. `notifications: speak_completions: false` turns it off.
        if store.speak_completions and str(task.get("kind") or "") in SPOKEN_KINDS:
            if jarvis.services.has_service("companion", "notify"):
                result = " ".join(str(task.get("result") or "").split())[:240]
                message = f"{finished}: {task.get('title') or 'the job you gave me'}."
                if result:
                    message += f" {result}"
                try:
                    await jarvis.services.async_call(
                        "companion", "notify",
                        {"message": message, "importance": "normal", "kind": "notify"},
                        blocking=True, return_response=True,
                    )
                except Exception:  # noqa: BLE001 - the card is already there
                    _LOGGER.exception("notifications: could not announce %s", task.get("id"))

    async def on_failed(event: Any) -> None:
        task = (event.data or {}).get("task") or {}
        await store.async_add(
            kind="task",
            title=f"Failed: {task.get('title') or 'a background job'}",
            body=str(task.get("error") or "")[:MAX_BODY],
            source=EVENT_TASK_FAILED,
            link=f"/tasks#{task.get('id') or ''}",
            task_id=str(task.get("id") or ""),
        )

    async def on_cancelled(event: Any) -> None:
        # Deliberately nothing. Somebody stopped it and they know; a
        # notification for it is the machine telling you what you just did.
        return

    jarvis.bus.listen(EVENT_TASK_COMPLETED, on_completed)
    jarvis.bus.listen(EVENT_TASK_FAILED, on_failed)
    jarvis.bus.listen(EVENT_TASK_CANCELLED, on_cancelled)

    async def on_briefing(event: Any) -> None:
        data = event.data or {}
        await store.async_add(
            kind="briefing",
            title=str(data.get("title") or "Briefing"),
            body=str(data.get("text") or data.get("speech") or "")[:MAX_BODY],
            source="briefing_ready",
            link="/",
        )

    jarvis.bus.listen("briefing_ready", on_briefing)


def _register_services(jarvis: "Jarvis", store: NotificationStore) -> None:
    async def notify(call: Any) -> Any:
        data = call.data or {}
        return await store.async_add(
            kind=str(data.get("kind") or "task"),
            title=str(data.get("title") or data.get("message") or ""),
            body=str(data.get("body") or data.get("message") or ""),
            source=str(data.get("source") or "service"),
            link=str(data.get("link") or ""),
        )

    async def listing(call: Any) -> Any:
        data = call.data or {}
        return {
            "notifications": store.listing(
                unread_only=bool(data.get("unread")), limit=int(data.get("limit") or 100)
            ),
            "unread": store.unread,
        }

    async def mark_read(call: Any) -> Any:
        data = call.data or {}
        return await store.async_mark_read(
            entry_id=str(data.get("id") or ""), everything=bool(data.get("all"))
        )

    async def dismiss(call: Any) -> Any:
        data = call.data or {}
        return await store.async_dismiss(
            entry_id=str(data.get("id") or ""), everything=bool(data.get("all"))
        )

    jarvis.services.register(DOMAIN, "add", notify, supports_response=True)
    jarvis.services.register(DOMAIN, "list", listing, supports_response=True)
    jarvis.services.register(DOMAIN, "mark_read", mark_read, supports_response=True)
    jarvis.services.register(DOMAIN, "dismiss", dismiss, supports_response=True)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    store = NotificationStore(
        jarvis,
        max_entries=int(cfg.get("max_entries") or DEFAULT_MAX_ENTRIES),
        kinds=cfg.get("kinds") if isinstance(cfg.get("kinds"), dict) else None,
    )
    store.speak_completions = bool(cfg.get("speak_completions", True))
    await store.async_load()
    jarvis.data[DATA_STORE] = store
    _register_services(jarvis, store)
    _register_tools(jarvis, store)
    _listen(jarvis, store)
    _LOGGER.info("notifications ready: %d kept, %d unread", len(store.entries), store.unread)
    return True


def _register_tools(jarvis: "Jarvis", store: NotificationStore) -> None:
    """`recent_moments` (M95): Jarvis reads its own inbox.

    "What did you tell me while I was out?" was answered by reconstruction —
    the model guessing at what it might have said — because nothing let it
    read the record (the agentic audit, 27 Aug 2026). This is the record,
    server-authored: what was recorded, when, and where it came from.
    """
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        return

    from ...llm.tools import schema_object

    async def tool_recent_moments(args: dict[str, Any], context: Any = None) -> Any:
        minutes = max(1.0, min(float(args.get("minutes") or 60), 7 * 24 * 60))
        limit = max(1, min(int(args.get("limit") or 10), 30))
        since = time.time() - minutes * 60
        rows = [r for r in store.listing(limit=200) if float(r.get("at") or 0.0) >= since][:limit]
        now = time.time()
        return {
            "status": "ok",
            "minutes": minutes,
            "count": len(rows),
            "moments": [
                {
                    "kind": r.get("kind"),
                    "title": r.get("title"),
                    "body": str(r.get("body") or "")[:300],
                    "minutes_ago": round(max(0.0, now - float(r.get("at") or now)) / 60.0, 1),
                    "source": r.get("source"),
                    "read": bool(r.get("read")),
                }
                for r in rows
            ],
            "note": "These are the messages Jarvis recorded for the user — say them back as such, briefly; do not invent others.",
        }

    registry.register(
        name="recent_moments",
        description=(
            "What Jarvis has told the user lately — finished jobs, reminders, notices, "
            "briefings — from its own record. Use it for \"what did you tell me while I was "
            "out?\", \"did that finish?\", \"anything I missed?\". Never invent a message "
            "this does not list."
        ),
        parameters=schema_object(
            {
                "minutes": {"type": "number", "description": "How far back to look (default 60, at most a week)."},
                "limit": {"type": "integer", "description": "At most this many (default 10)."},
            }
        ),
        handler=tool_recent_moments,
        domain=DOMAIN,
        read_only=True,
    )
