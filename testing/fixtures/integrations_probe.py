#!/usr/bin/env python3
"""Calendar and mail, against real servers, with nobody's account involved.

M39 asks for three things to be shown rather than asserted:

    a created event appears on the calendar
    a sent message lands in the inbox
    a state-changing call that was not approved is refused

The first two run against the fixture containers — Radicale and smtp4dev,
behind `--profile fixtures` — because a CalDAV client that passes against a
mock of itself has proved nothing about CalDAV. The third needs no server at
all: it is the tier system, and the point is that it holds.

    docker compose --profile fixtures up -d
    python3 testing/fixtures/integrations_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "jarvis-core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from jarvis.integrations.calendar import Calendar  # noqa: E402
from jarvis.integrations.mail import Mail  # noqa: E402

CALDAV = "http://127.0.0.1:5232/jarvis/diary"
SMTP_HOST, SMTP_PORT = "127.0.0.1", 1025
IMAP_HOST, IMAP_PORT = "127.0.0.1", 1043
SINK_API = "http://127.0.0.1:1080/api/Messages"


class FakeJarvis:
    def __init__(self) -> None:
        self.data: dict = {}

        class Bus:
            @staticmethod
            def fire(*_a, **_kw):
                return None

        self.bus = Bus()


def sink_messages() -> list[dict]:
    with urllib.request.urlopen(SINK_API, timeout=10) as answer:
        return json.loads(answer.read() or b"{}").get("results") or []


async def main() -> int:
    failures: list[str] = []
    jarvis = FakeJarvis()

    # --- the calendar -----------------------------------------------------
    calendar = Calendar(jarvis, {"url": CALDAV})
    health = await calendar.health()
    if not health.get("ok"):
        print(f"the fixture calendar is not up: {health}", file=sys.stderr)
        print("start it with: docker compose --profile fixtures up -d", file=sys.stderr)
        return 2

    summary = f"probe {uuid.uuid4().hex[:8]}"
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    made = await calendar.create_event(
        {"summary": summary, "start": start.isoformat(), "minutes": 45}
    )
    if made.get("status") != "ok":
        failures.append(f"the event was not created: {made}")
    else:
        print(f"  ok   an event is created on a real CalDAV server ({made['uid'][:8]}…)")

    listed = await calendar.list_events({"days": 2})
    summaries = [e["summary"] for e in listed.get("events") or []]
    if summary not in summaries:
        failures.append(f"the created event is not in the diary: {summaries}")
    else:
        print("  ok   and reading the diary back finds it")

    free = await calendar.availability({"days": 1, "minutes": 30})
    if free.get("status") != "ok" or not isinstance(free.get("free"), list):
        failures.append(f"availability did not answer: {free}")
    else:
        print(f"  ok   availability is arithmetic, not a guess ({len(free['free'])} window(s))")

    if made.get("uid"):
        await calendar.delete_event({"uid": made["uid"]})

    # --- the mail ---------------------------------------------------------
    before = len(sink_messages())
    mail = Mail(jarvis, {
        "smtp": {"host": SMTP_HOST, "port": SMTP_PORT, "starttls": False, "auth": False},
        "imap": {"host": IMAP_HOST, "port": IMAP_PORT, "ssl": False},
        "from": "jarvis@fixture.local",
        "allow_to": ["operator@fixture.local"],
        "username": "",
        "password": "",
    })
    subject = f"probe {uuid.uuid4().hex[:8]}"
    sent = await mail.send_mail(
        {"to": "operator@fixture.local", "subject": subject, "body": "the boiler is serviced"}
    )
    if sent.get("status") != "ok":
        failures.append(f"the mail was not sent: {sent}")
    else:
        await asyncio.sleep(1.5)
        landed = [m for m in sink_messages() if subject in str(m.get("subject") or "")]
        if not landed:
            failures.append("the sent mail did not land in the fixture inbox")
        else:
            print("  ok   a sent message lands in a real inbox")

    refused = await mail.send_mail({"to": "stranger@example.com", "subject": "x", "body": "y"})
    if refused.get("status") == "ok":
        failures.append("MAIL WAS SENT TO AN ADDRESS THAT IS NOT ALLOW-LISTED")
    elif len(sink_messages()) != before + 1:
        failures.append("a refused message reached the server anyway")
    else:
        print("  ok   an address nobody allow-listed is refused, not asked about")

    # --- the gate ---------------------------------------------------------
    from jarvis.integrations.plugins import PluginTool
    from jarvis.llm.tools import TIER_APPROVAL, TIER_DIRECT

    tiers = {t.name: t.resolved_tier() for t in list(calendar.tools()) + list(mail.tools())}
    readers = [n for n, tier in tiers.items() if tier == TIER_DIRECT]
    writers = [n for n, tier in tiers.items() if tier >= TIER_APPROVAL]
    if any(n in writers for n in ("calendar_list", "calendar_availability", "mail_read")):
        failures.append("a read-only tool needs approval, which makes the assistant useless")
    if any(n in readers for n in ("calendar_create", "calendar_delete", "mail_send")):
        failures.append("A STATE-CHANGING TOOL RUNS WITHOUT A HUMAN")
    else:
        print(f"  ok   {len(readers)} read-only tool(s) run; {len(writers)} need a human")

    unclassified = PluginTool("x", "", {}, lambda a: None)
    if unclassified.resolved_tier() != TIER_APPROVAL:
        failures.append("a tool nobody classified defaults to running without a human")
    else:
        print("  ok   a tool nobody classified needs a human")

    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\nintegrations probe: {6 - len(failures)}/6")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
