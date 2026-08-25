"""calendar — CalDAV, over HTTP, with no new dependency.

    calendar:
      url: https://dav.example/user/calendar/
      username: !secret caldav_user
      password: !secret caldav_password

CalDAV is HTTP verbs and XML, and that is all this speaks: `REPORT` to read a
window, `PUT` to write an event, `DELETE` to remove one. The `caldav` library
would bring `lxml`, and `jarvis-core`'s dependency list is seven pure-Python
wheels on purpose (`DEVIATIONS.md` §9) — `xml.etree` and `httpx` are already
here.

## What is read-only and what is not

Reading the diary is Tier 1: it is somebody's own calendar and the model is
answering a question about it. Writing is Tier 3, always — an event that
appears in a shared calendar is visible to other people, and an event that
quietly moves is worse than one that was never made.

Availability ("am I free on Tuesday afternoon") is read-only and is the query
this exists for. It is computed here rather than asked of the model, because
"free" is arithmetic over busy periods and a model doing arithmetic over
timestamps is a model getting it wrong occasionally and confidently.
"""

from __future__ import annotations

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..plugins import PluginTool, ToolPlugin, get_registry

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "calendar"
DEPENDENCIES = ["llm"]

#: How far ahead "what's on" looks when nobody says.
DEFAULT_WINDOW_DAYS = 7
#: A ceiling, because a REPORT over a year is a lot of XML for a spoken answer.
MAX_WINDOW_DAYS = 92

CALDAV_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}

#: The window query. `VEVENT` only: tasks and journals are a different feature
#: and returning them here would answer "what's on" with somebody's to-do list.
REPORT_BODY = """<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_stamp(raw: str) -> datetime | None:
    text = (raw or "").strip()
    for shape in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, shape)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass
class Event:
    """One VEVENT, reduced to what a person asks about."""

    uid: str
    summary: str
    start: datetime | None
    end: datetime | None
    location: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "summary": self.summary,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "location": self.location or None,
        }


def parse_ical(text: str) -> list[Event]:
    """Every VEVENT in an iCalendar document.

    A hand-rolled reader for five fields, not a general parser: what is needed
    is "when is it and what is it called", and folding, escaping and timezones
    beyond UTC are handled to the extent the fixtures and Radicale produce
    them. Anything it cannot read becomes an event with a blank field rather
    than an exception — a diary that fails to load because one entry has an
    odd RRULE is worse than one that shows the entry without its recurrence.
    """
    events: list[Event] = []
    # Unfold: a continued line starts with a space or a tab.
    unfolded = re.sub(r"\r?\n[ \t]", "", text or "")
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.split(";")[0].strip().upper()] = value.strip()
        events.append(
            Event(
                uid=fields.get("UID", ""),
                summary=fields.get("SUMMARY", "").replace("\\,", ",").replace("\\n", " "),
                start=_parse_stamp(fields.get("DTSTART", "")),
                end=_parse_stamp(fields.get("DTEND", "")),
                location=fields.get("LOCATION", ""),
            )
        )
    events.sort(key=lambda e: e.start or datetime.max.replace(tzinfo=timezone.utc))
    return events


def build_ical(summary: str, start: datetime, end: datetime, location: str = "",
               uid: str = "") -> str:
    """One VEVENT, as a whole VCALENDAR — which is what a PUT takes."""
    identifier = uid or f"{uuid.uuid4().hex}@jarvis"
    escaped = summary.replace(",", "\\,").replace(";", "\;")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Jarvis//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{identifier}\r\n"
        f"DTSTAMP:{_stamp(datetime.now(timezone.utc))}\r\n"
        f"DTSTART:{_stamp(start)}\r\n"
        f"DTEND:{_stamp(end)}\r\n"
        f"SUMMARY:{escaped}\r\n"
        + (f"LOCATION:{location}\r\n" if location else "")
        + "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def free_windows(
    events: list[Event], start: datetime, end: datetime, minutes: int = 30
) -> list[tuple[datetime, datetime]]:
    """Gaps of at least `minutes` between `start` and `end`.

    Arithmetic, here, rather than a question for the model: "am I free on
    Tuesday afternoon" is a fold over busy periods, and a model doing that over
    timestamps gets it wrong occasionally and confidently.
    """
    busy = sorted(
        (e.start, e.end or e.start + timedelta(hours=1))
        for e in events
        if e.start is not None
    )
    free: list[tuple[datetime, datetime]] = []
    cursor = start
    for busy_start, busy_end in busy:
        if busy_start > cursor and (busy_start - cursor) >= timedelta(minutes=minutes):
            free.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
    if end > cursor and (end - cursor) >= timedelta(minutes=minutes):
        free.append((cursor, end))
    return free


class Calendar(ToolPlugin):
    """CalDAV, as four tools."""

    domain = DOMAIN

    @property
    def url(self) -> str:
        return str(self.config.get("url") or "").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.url)

    async def _request(self, method: str, path: str = "", body: str = "",
                       headers: dict[str, str] | None = None) -> tuple[int, str]:
        import httpx

        auth = None
        user = self.secret("username")
        password = self.secret("password")
        if user:
            auth = (user, password)
        url = f"{self.url}/{path.lstrip('/')}" if path else self.url + "/"
        async with httpx.AsyncClient(timeout=30.0, auth=auth) as http:
            answer = await http.request(method, url, content=body or None, headers=headers)
            return answer.status_code, answer.text

    async def events_between(self, start: datetime, end: datetime) -> list[Event]:
        status, text = await self._request(
            "REPORT",
            body=REPORT_BODY.format(start=_stamp(start), end=_stamp(end)),
            headers={"depth": "1", "content-type": "application/xml; charset=utf-8"},
        )
        if status >= 400:
            raise RuntimeError(f"the calendar answered {status}")
        events: list[Event] = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise RuntimeError(f"the calendar sent XML we could not read: {err}") from err
        for data in root.iter(f"{{{CALDAV_NS['c']}}}calendar-data"):
            events.extend(parse_ical(data.text or ""))
        events.sort(key=lambda e: e.start or datetime.max.replace(tzinfo=timezone.utc))
        return events

    # --- the tools --------------------------------------------------------
    async def list_events(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"status": "error", "error": "no calendar is configured"}
        days = max(1, min(int(args.get("days") or DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
        start = datetime.now(timezone.utc)
        events = await self.events_between(start, start + timedelta(days=days))
        return {
            "status": "ok",
            "days": days,
            "events": [e.as_dict() for e in events[:50]],
        }

    async def availability(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"status": "error", "error": "no calendar is configured"}
        days = max(1, min(int(args.get("days") or 1), MAX_WINDOW_DAYS))
        minutes = max(5, int(args.get("minutes") or 30))
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days)
        events = await self.events_between(start, end)
        windows = free_windows(events, start, end, minutes)
        return {
            "status": "ok",
            "free": [
                {"from": a.isoformat(), "to": b.isoformat()} for a, b in windows[:20]
            ],
            "busy": len(events),
        }

    async def create_event(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"status": "error", "error": "no calendar is configured"}
        summary = str(args.get("summary") or "").strip()
        if not summary:
            return {"status": "error", "error": "an event needs a summary"}
        start = _parse_iso(args.get("start"))
        if start is None:
            return {"status": "error", "error": "an event needs a start time (ISO 8601)"}
        end = _parse_iso(args.get("end")) or start + timedelta(
            minutes=int(args.get("minutes") or 60)
        )
        uid = f"{uuid.uuid4().hex}@jarvis"
        body = build_ical(summary, start, end, str(args.get("location") or ""), uid)
        status, text = await self._request(
            "PUT", f"{uid}.ics", body,
            headers={"content-type": "text/calendar; charset=utf-8"},
        )
        if status >= 400:
            return {"status": "error", "error": f"the calendar refused it ({status}): {text[:200]}"}
        return {"status": "ok", "uid": uid, "summary": summary, "start": start.isoformat()}

    async def delete_event(self, args: dict[str, Any]) -> dict[str, Any]:
        uid = str(args.get("uid") or "").strip()
        if not uid:
            return {"status": "error", "error": "which event? give its uid"}
        status, _text = await self._request("DELETE", f"{uid}.ics")
        if status >= 400:
            return {"status": "error", "error": f"the calendar refused ({status})"}
        return {"status": "ok", "uid": uid}

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "error": "no url configured"}
        try:
            status, _ = await self._request("OPTIONS")
        except Exception as err:  # noqa: BLE001
            return {"ok": False, "error": f"{type(err).__name__}"}
        return {"ok": status < 400, "status": status}

    def tools(self):
        return [
            PluginTool(
                "calendar_list",
                "What is in the diary over the next few days.",
                {"days": {"type": "integer", "description": "how far ahead (default 7)"}},
                self.list_events,
                read_only=True,
            ),
            PluginTool(
                "calendar_availability",
                "When the user is free — gaps between what is already booked.",
                {
                    "days": {"type": "integer", "description": "how far ahead (default 1)"},
                    "minutes": {"type": "integer", "description": "shortest useful gap"},
                },
                self.availability,
                read_only=True,
            ),
            PluginTool(
                "calendar_create",
                "Put an event in the diary. Needs a human to say yes.",
                {
                    "summary": {"type": "string", "description": "what it is"},
                    "start": {"type": "string", "description": "ISO 8601 start"},
                    "end": {"type": "string", "description": "ISO 8601 end (or use minutes)"},
                    "minutes": {"type": "integer", "description": "length, if no end"},
                    "location": {"type": "string"},
                },
                self.create_event,
            ),
            PluginTool(
                "calendar_delete",
                "Remove an event from the diary. Needs a human to say yes.",
                {"uid": {"type": "string", "description": "the event's uid"}},
                self.delete_event,
            ),
        ]


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _parse_stamp(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    plugin = Calendar(jarvis, config)
    jarvis.data[DOMAIN] = plugin
    get_registry(jarvis).add(plugin)
    if not plugin.configured:
        _LOGGER.info("calendar: no url configured; no tools registered")
        return True
    plugin.register()
    return True
