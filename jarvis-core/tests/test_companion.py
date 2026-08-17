"""Cross-device presence routing + proactive outreach."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.companion import (  # noqa: E402
    CompanionManager,
)
from jarvis.presence import (  # noqa: E402
    NEEDS_ANSWER,
    NEEDS_SPEECH,
    NEEDS_VISUAL,
    PresenceRegistry,
    Reach,
)

NOW = 1_000_000.0


def make_registry() -> PresenceRegistry:
    reg = PresenceRegistry()
    reg.register("phone", "Pixel", "android", ["speak", "ask", "ui_automation"])
    reg.register("desk", "Workstation", "desktop", ["speak", "ask", "shell"])
    for d in reg.devices.values():
        d.last_seen = NOW
    return reg


# --- reach ------------------------------------------------------------------
def test_reach_levels():
    reg = make_registry()
    phone = reg.devices["phone"]

    phone.last_interaction = NOW - 10
    assert phone.reach(NOW) is Reach.ACTIVE

    phone.last_interaction = NOW - 3600
    phone.screen_on, phone.locked = True, False
    assert phone.reach(NOW) is Reach.PRESENT

    phone.locked = True
    assert phone.reach(NOW) is Reach.IDLE

    phone.screen_on = False
    assert phone.reach(NOW) is Reach.BACKGROUND

    phone.last_seen = NOW - 3600  # stale
    assert phone.reach(NOW) is Reach.ABSENT

    phone.last_seen = NOW
    phone.connected = False
    assert phone.reach(NOW) is Reach.ABSENT


# --- routing ----------------------------------------------------------------
def test_routes_to_the_device_you_just_used():
    reg = make_registry()
    reg.devices["desk"].last_interaction = NOW - 5
    reg.devices["phone"].last_interaction = NOW - 600
    delivery = reg.route(NEEDS_VISUAL, now=NOW)
    assert delivery.device_id == "desk"
    assert "phone" in delivery.fallbacks


def test_driving_wins_and_speaks():
    reg = make_registry()
    reg.devices["desk"].last_interaction = NOW - 5   # desk used more recently
    phone = reg.devices["phone"]
    phone.driving = True
    phone.last_interaction = NOW - 600

    delivery = reg.route(NEEDS_SPEECH, now=NOW)
    assert delivery.device_id == "phone", "driving must beat recency for speech"
    assert delivery.mode == "speak"
    assert "driving" in delivery.reason


def test_question_needs_an_answerable_device():
    reg = make_registry()
    # both asleep -> nobody can answer
    for d in reg.devices.values():
        d.screen_on = False
        d.last_interaction = NOW - 3600
    assert reg.route(NEEDS_ANSWER, now=NOW).device_id is None

    reg.devices["phone"].screen_on = True
    delivery = reg.route(NEEDS_ANSWER, now=NOW)
    assert delivery.device_id == "phone"
    assert delivery.mode == "ask"


def test_muted_device_is_skipped_but_can_still_be_asked():
    reg = make_registry()
    phone = reg.devices["phone"]
    phone.muted = True
    phone.last_interaction = NOW - 5
    desk = reg.devices["desk"]
    desk.last_interaction = NOW - 600
    desk.screen_on, desk.locked = True, False

    assert reg.route(NEEDS_VISUAL, now=NOW).device_id == "desk"
    assert reg.route(NEEDS_SPEECH, now=NOW).device_id == "desk"


def test_no_audio_falls_back_to_notify():
    reg = make_registry()
    reg.devices.pop("desk")
    phone = reg.devices["phone"]
    phone.audio_available = False
    phone.last_interaction = NOW - 5
    assert reg.route(NEEDS_SPEECH, now=NOW).mode == "notify"


def test_nothing_reachable_queues_but_critical_still_lands():
    reg = make_registry()
    for d in reg.devices.values():
        d.connected = False
    assert reg.route(NEEDS_VISUAL, now=NOW).mode == "queue"
    critical = reg.route(NEEDS_VISUAL, importance="critical", now=NOW)
    assert critical.mode == "notify" and critical.device_id is not None


def test_preferred_device_is_honoured_when_usable():
    reg = make_registry()
    reg.devices["desk"].last_interaction = NOW - 5
    reg.devices["phone"].screen_on = True
    delivery = reg.route(NEEDS_VISUAL, prefer_device="phone", now=NOW)
    assert delivery.device_id == "phone"


# --- companion manager ------------------------------------------------------
@pytest.fixture
async def companion(tmp_path):
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"companion": {}})
    manager: CompanionManager = jarvis.data["companion"]
    reg: PresenceRegistry = jarvis.data["presence"]
    reg.register("phone", "Pixel", "android", ["ask"])
    reg.register("desk", "Workstation", "desktop", ["ask"])
    for d in reg.devices.values():
        d.last_seen = time.time()
        d.screen_on = True
        d.locked = False
    sent: list[tuple[str, dict]] = []

    async def transport(device_id, payload):
        sent.append((device_id, payload))
        return True

    manager.set_transport(transport)
    return jarvis, manager, reg, sent


async def test_notify_delivers_to_best_device(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("desk")
    result = await manager.send("Build finished.", kind="notify")
    assert result["status"] == "delivered"
    assert result["device_id"] == "desk"
    assert sent[0][0] == "desk"
    assert sent[0][1]["text"] == "Build finished."
    assert sent[0][1]["type"] == "jarvis_message"


async def test_ask_blocks_until_the_device_answers(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")

    task = asyncio.create_task(
        manager.send("Deploy to production?", kind="ask", options=["yes", "no"])
    )
    await asyncio.sleep(0.05)
    assert sent, "question should have been pushed to a device"
    device_id, payload = sent[0]
    assert device_id == "phone"
    assert payload["kind"] == "ask"
    assert payload["options"] == ["yes", "no"]

    manager.on_device_answer(payload["message_id"], "no")
    result = await task
    assert result["status"] == "answered"
    assert result["answer"] == "no"


async def test_ask_times_out_without_hanging(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")
    result = await manager.send("Anyone there?", kind="ask", timeout=0.05)
    assert result["status"] == "timeout"
    assert result["answer"] is None


async def test_dismissal_escalates_to_the_other_device(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")
    task = asyncio.create_task(manager.send("Confirm?", kind="ask", timeout=2.0))
    await asyncio.sleep(0.05)
    first_device, payload = sent[0]

    # user dismissed it on the first device -> try the next one
    manager.on_device_answer(payload["message_id"], None, status="dismissed")
    await asyncio.sleep(0.05)
    assert len(sent) == 2, "should have escalated to the fallback device"
    assert sent[1][0] != first_device

    manager.on_device_answer(payload["message_id"], "yes")
    result = await task
    assert result["answer"] == "yes"


async def test_queues_when_no_device_is_reachable_then_drains(companion):
    jarvis, manager, reg, sent = companion
    for d in reg.devices.values():
        d.connected = False

    result = await manager.send("Washing machine done.", kind="notify")
    assert result["status"] == "queued"
    assert manager.queued == 1
    assert not sent

    # a device comes back and the transport is (re)installed -> queue drains
    reg.register("phone", "Pixel", "android", ["ask"])
    reg.devices["phone"].screen_on = True
    reg.devices["phone"].locked = False

    async def transport(device_id, payload):
        sent.append((device_id, payload))
        return True

    manager.set_transport(transport)
    await asyncio.sleep(0.05)
    assert sent and sent[0][1]["text"] == "Washing machine done."


async def test_conversation_id_travels_with_the_message(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("desk")
    await manager.send("Continuing here.", kind="say", conversation_id="conv-7")
    assert sent[0][1]["conversation_id"] == "conv-7"


async def test_services_registered_and_callable(companion):
    jarvis, manager, reg, sent = companion
    assert jarvis.services.has_service("companion", "notify")
    assert jarvis.services.has_service("companion", "ask")
    assert jarvis.services.has_service("companion", "presence")

    reg.touch_interaction("phone")
    report = await jarvis.async_call_service(
        "companion", "presence", {}, return_response=True
    )
    assert len(report["devices"]) == 2
    assert report["route"]["device_id"] == "phone"

    out = await jarvis.async_call_service(
        "companion", "notify", {"message": "hello"}, return_response=True
    )
    assert out["status"] == "delivered"


async def test_answer_for_unknown_message_is_ignored(companion):
    jarvis, manager, reg, sent = companion
    assert manager.on_device_answer("nope", "yes") is False


async def test_transport_failure_requeues(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")

    async def broken(device_id, payload):
        raise RuntimeError("socket closed")

    manager.set_transport(broken)
    await asyncio.sleep(0)  # let the drain task run
    result = await manager.send("still here?", kind="notify")
    assert result["status"] == "queued"


# --- a question may never be answered by a device on the user's behalf -------
#
# The bug these cover: `route()` hard-coded mode "notify" for the
# critical/nothing-reachable branch, whatever the message needed. A device told
# "just notify" posts the notification and reports `answered` — that is how
# every device acknowledges delivery of a plain message. The manager then
# resolved the waiting `companion.ask` with an empty answer and stopped
# escalating, so an automation branched on a reply nobody gave and the human
# was never asked at all.


def test_critical_question_stays_a_question_when_nothing_is_reachable():
    reg = make_registry()
    for d in reg.devices.values():
        d.connected = False
    delivery = reg.route(NEEDS_ANSWER, importance="critical", now=NOW)
    assert delivery.device_id is not None
    assert delivery.mode == "ask", "a question routed as 'notify' gets auto-acknowledged"


def test_critical_speech_still_downgrades_on_a_muted_device():
    # The same branch must keep honouring what the device can actually do.
    reg = make_registry()
    for d in reg.devices.values():
        d.connected = False
        d.muted = True
    assert reg.route(NEEDS_SPEECH, importance="critical", now=NOW).mode == "notify"


async def test_blank_answered_does_not_resolve_a_question(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")
    task = asyncio.create_task(
        manager.send("Upload the photos?", kind="ask", options=["yes", "no"], timeout=0.4)
    )
    await asyncio.sleep(0.05)
    message_id = sent[0][1]["message_id"]

    # The delivery acknowledgement a device sends for a notification.
    assert manager.on_device_answer(message_id, "", "answered") is True

    result = await task
    assert result["status"] == "timeout"
    assert result["answer"] is None


async def test_blank_answered_escalates_to_the_next_device(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")
    task = asyncio.create_task(
        manager.send("Deploy?", kind="ask", options=["yes", "no"], timeout=0.5)
    )
    await asyncio.sleep(0.05)
    message_id = sent[0][1]["message_id"]
    manager.on_device_answer(message_id, None, "answered")
    await asyncio.sleep(0.05)

    # It moved on to the other device rather than being swallowed.
    assert [d for d, _ in sent] == ["phone", "desk"]
    manager.on_device_answer(message_id, "yes", "answered")
    result = await task
    assert result["status"] == "answered" and result["answer"] == "yes"


async def test_real_answers_are_untouched(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("phone")
    task = asyncio.create_task(manager.send("Deploy?", kind="ask", timeout=1.0))
    await asyncio.sleep(0.05)
    manager.on_device_answer(sent[0][1]["message_id"], "  no  ", "answered")
    result = await task
    assert result["status"] == "answered" and result["answer"] == "  no  "


async def test_notify_service_cannot_be_turned_into_a_blocking_ask(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("desk")
    out = await asyncio.wait_for(
        jarvis.services.async_call(
            "companion", "notify",
            {"message": "Deploy?", "kind": "ask"},
            blocking=True, return_response=True,
        ),
        timeout=2.0,
    )
    assert out["status"] == "delivered"
    assert sent[0][1]["kind"] == "notify"


async def test_unknown_kind_becomes_a_quiet_notify(companion):
    jarvis, manager, reg, sent = companion
    reg.touch_interaction("desk")
    result = await manager.send("something", kind="shout")
    assert result["status"] == "delivered"
    assert sent[0][1]["kind"] == "notify"


async def test_a_queued_question_is_not_asked_after_nobody_is_waiting(companion):
    jarvis, manager, reg, sent = companion
    for d in reg.devices.values():
        d.connected = False

    result = await manager.send("Upload the photos?", kind="ask", timeout=0.5)
    assert result["status"] == "queued"
    assert manager.queued == 1

    expired: list[dict] = []
    jarvis.bus.listen("companion_message_expired", lambda e: expired.append(e.data))

    reg.register("phone", "Pixel", "android", ["ask"])
    reg.devices["phone"].screen_on = True
    reg.devices["phone"].locked = False

    async def transport(device_id, payload):
        sent.append((device_id, payload))
        return True

    manager.set_transport(transport)
    await asyncio.sleep(0.05)
    assert not sent, "a question whose asker has gone must not be put on a screen"
    assert expired and expired[0]["reason"] == "nobody waiting"


async def test_a_queued_notification_still_drains(companion):
    jarvis, manager, reg, sent = companion
    for d in reg.devices.values():
        d.connected = False
    assert (await manager.send("Washing machine done.", kind="notify"))["status"] == "queued"

    reg.register("phone", "Pixel", "android", ["ask"])
    reg.devices["phone"].screen_on = True
    reg.devices["phone"].locked = False

    async def transport(device_id, payload):
        sent.append((device_id, payload))
        return True

    manager.set_transport(transport)
    await asyncio.sleep(0.05)
    assert sent and sent[0][1]["text"] == "Washing machine done."
