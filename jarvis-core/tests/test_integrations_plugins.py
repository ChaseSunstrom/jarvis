"""The plugin interface, and its first two users.

Most of this is about the four things the base class exists to stop each
integration getting wrong on its own: read-only declared rather than inferred,
state-changing gated by default, credentials fetched when the tool runs, and
every external call visible in the trace.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.integrations.calendar import (
    Calendar,
    Event,
    build_ical,
    free_windows,
    parse_ical,
)
from jarvis.integrations.mail import Mail, body_of
from jarvis.integrations.plugins import PluginTool, ToolPlugin
from jarvis.llm.tools import TIER_APPROVAL, TIER_DIRECT


class FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple] = []

    def fire(self, event_type, data, context=None):
        self.fired.append((event_type, data))


class FakeJarvis:
    def __init__(self) -> None:
        self.data: dict = {}
        self.bus = FakeBus()


# --- the base class --------------------------------------------------------
def test_a_reader_runs_and_a_writer_needs_a_human():
    assert PluginTool("r", "", {}, lambda a: None, read_only=True).resolved_tier() == TIER_DIRECT
    assert PluginTool("w", "", {}, lambda a: None).resolved_tier() == TIER_APPROVAL


def test_a_plugin_may_argue_for_a_different_tier_but_has_to_say_so():
    from jarvis.llm.tools import TIER_BACKGROUND

    tool = PluginTool("slow", "", {}, lambda a: None, tier=TIER_BACKGROUND)
    assert tool.resolved_tier() == TIER_BACKGROUND


def test_a_credential_is_read_when_the_tool_runs():
    """Not at import: a secret should not sit in an attribute for the process's life."""
    jarvis = FakeJarvis()
    plugin = ToolPlugin(jarvis, {"password": ""})
    jarvis.data["secrets"] = {"password": "from-the-store"}
    assert plugin.secret("password") == "from-the-store"


def test_the_config_wins_over_the_store_because_it_was_already_resolved():
    plugin = ToolPlugin(FakeJarvis(), {"password": "from-the-config"})
    assert plugin.secret("password") == "from-the-config"


@pytest.mark.asyncio
async def test_every_call_lands_in_the_trace_with_its_duration():
    jarvis = FakeJarvis()
    plugin = ToolPlugin(jarvis, {})
    wrapped = plugin._wrap(PluginTool("thing", "", {}, lambda a: {"status": "ok"}))
    assert await wrapped({}) == {"status": "ok"}
    (name, data), = jarvis.bus.fired
    assert name == "jarvis_plugin_call" and data["tool"] == "thing" and data["ok"] is True
    assert isinstance(data["ms"], float)


@pytest.mark.asyncio
async def test_a_far_end_that_fails_does_not_kill_the_turn():
    jarvis = FakeJarvis()

    def explode(_args):
        raise ConnectionError("no route to host")

    wrapped = ToolPlugin(jarvis, {})._wrap(PluginTool("thing", "", {}, explode))
    answer = await wrapped({})
    assert answer["status"] == "error" and "no route" in answer["error"]
    assert jarvis.bus.fired[0][1]["ok"] is False


# --- the calendar ----------------------------------------------------------
def test_an_ical_event_is_read_back_out_of_what_we_wrote():
    start = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)
    text = build_ical("Boiler service", start, start + timedelta(hours=1), "the utility room")
    (event,) = parse_ical(text)
    assert event.summary == "Boiler service"
    assert event.start == start
    assert event.location == "the utility room"


def test_a_folded_line_is_unfolded():
    """RFC 5545 folds at 75 octets, and a summary is the field that hits it.

    Unfolding removes the line break AND the single whitespace that follows it
    — so a producer that wants a space there puts it BEFORE the fold. Getting
    this backwards costs a space in the middle of a word, which is exactly the
    kind of thing nobody notices until an event is called "boilerservice".
    """
    text = (
        "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:a very long summary that has been \r\n"
        " folded across two lines\r\nDTSTART:20260304T090000Z\r\nEND:VEVENT\r\n"
    )
    (event,) = parse_ical(text)
    assert event.summary == "a very long summary that has been folded across two lines"


def test_an_event_we_cannot_fully_read_is_still_an_event():
    """A diary that fails to load because one entry has an odd RRULE is worse."""
    (event,) = parse_ical("BEGIN:VEVENT\r\nRRULE:FREQ=WEEKLY\r\nSUMMARY:gym\r\nEND:VEVENT\r\n")
    assert event.summary == "gym" and event.start is None


def test_availability_is_arithmetic():
    day = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)
    events = [
        Event("1", "standup", day, day + timedelta(minutes=15)),
        Event("2", "lunch", day + timedelta(hours=3), day + timedelta(hours=4)),
    ]
    windows = free_windows(events, day, day + timedelta(hours=8), minutes=30)
    assert (day + timedelta(minutes=15), day + timedelta(hours=3)) in windows
    # And a gap shorter than asked for is not offered.
    assert all((b - a) >= timedelta(minutes=30) for a, b in windows)


def test_overlapping_events_do_not_invent_free_time():
    day = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)
    events = [
        Event("1", "a", day, day + timedelta(hours=2)),
        Event("2", "b", day + timedelta(hours=1), day + timedelta(hours=3)),
    ]
    windows = free_windows(events, day, day + timedelta(hours=4), minutes=30)
    assert windows == [(day + timedelta(hours=3), day + timedelta(hours=4))]


def test_the_calendar_tools_are_split_the_right_way():
    tools = {t.name: t for t in Calendar(FakeJarvis(), {"url": "http://x"}).tools()}
    assert tools["calendar_list"].read_only is True
    assert tools["calendar_availability"].read_only is True
    assert tools["calendar_create"].resolved_tier() == TIER_APPROVAL
    assert tools["calendar_delete"].resolved_tier() == TIER_APPROVAL


@pytest.mark.asyncio
async def test_creating_an_event_needs_a_start():
    calendar = Calendar(FakeJarvis(), {"url": "http://x"})
    assert (await calendar.create_event({"summary": "x"}))["status"] == "error"
    assert (await calendar.create_event({"start": "2026-03-04T09:00:00Z"}))["status"] == "error"


# --- the mail --------------------------------------------------------------
def test_mail_reading_is_free_and_sending_is_not():
    tools = {t.name: t for t in Mail(FakeJarvis(), {"smtp": {"host": "x"}}).tools()}
    assert tools["mail_read"].read_only is True
    assert tools["mail_send"].resolved_tier() == TIER_APPROVAL


@pytest.mark.asyncio
async def test_an_address_nobody_allow_listed_is_refused():
    """"Send this to attacker@example?" is a prompt somebody clicks yes on."""
    mail = Mail(FakeJarvis(), {"smtp": {"host": "x"}, "allow_to": ["me@home"]})
    answer = await mail.send_mail({"to": "stranger@example.com", "subject": "s", "body": "b"})
    assert answer["status"] == "error" and "allow-list" in answer["error"]


@pytest.mark.asyncio
async def test_an_empty_allow_list_means_nobody():
    mail = Mail(FakeJarvis(), {"smtp": {"host": "x"}})
    answer = await mail.send_mail({"to": "me@home", "subject": "s", "body": "b"})
    assert answer["status"] == "error" and "nobody" in answer["error"]


def test_a_multipart_body_prefers_the_plain_text_part():
    import email.message

    message = email.message.EmailMessage()
    message.set_content("the plain one")
    message.add_alternative("<p>the html one</p>", subtype="html")
    assert "the plain one" in body_of(message)


def test_a_body_is_clipped_so_a_newsletter_cannot_eat_the_context():
    import email.message

    message = email.message.EmailMessage()
    message.set_content("x" * 50_000)
    assert len(body_of(message, limit=2000)) <= 2000
