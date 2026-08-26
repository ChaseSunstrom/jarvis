"""The `schedule` integration: reminders, and work put off until later.

No sleeping and no real clock — the manager's `now()` is replaced, so "two days
later" is a line rather than a wait.

The arithmetic has its own file (`test_schedule_plan.py`). What is here is
everything built on it, and two of those are refusals:

  * scheduling a service call must not be a way round a Tier-3 gate. A held
    action deferred by sixty seconds arrives with nobody to ask.
  * the MODEL may schedule a reminder or a research run. It may not schedule an
    action on the house — that is a tool for laundering a prompt injection
    through a delay.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core import Jarvis  # noqa: E402
from jarvis.integrations.schedule import (  # noqa: E402
    KIND_NOTIFY,
    STORE_KEY,
    ScheduleManager,
    async_setup,
    get_manager,
    job_from_dict,
)
from jarvis.llm.tools import ToolRegistry  # noqa: E402
from jarvis.store import Store  # noqa: E402

LONDON = ZoneInfo("Europe/London")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=LONDON)


class FrozenClock:
    """A clock a test moves by hand."""

    def __init__(self, start: str = "2026-01-01T06:00") -> None:
        self.at = at(start)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kw: Any) -> None:
        self.at += timedelta(**kw)


@pytest.fixture
async def jarvis(tmp_path):
    instance = Jarvis(tmp_path)
    instance.data["llm_tools"] = ToolRegistry(instance)
    await instance.async_setup({})
    await instance.async_start()
    yield instance
    await instance.async_stop()


class Recorder:
    """Stands in for `companion` and for whatever a service call would do."""

    def __init__(self, jarvis) -> None:
        self.notified: list[str] = []
        self.asked: list[str] = []
        self.answer = "no"
        self.calls: list[tuple[str, dict]] = []

        async def notify(call) -> dict:
            self.notified.append(str(call.get("message") or ""))
            return {"status": "ok"}

        async def ask(call) -> dict:
            self.asked.append(str(call.get("question") or ""))
            return {"status": "answered", "answer": self.answer}

        async def unlock(call) -> dict:
            self.calls.append(("lock.unlock", dict(call.data)))
            return {"ok": True}

        async def tell(call) -> dict:
            self.calls.append(("demo.tell", dict(call.data)))
            return {"ok": True}

        jarvis.services.register("companion", "notify", notify, supports_response=True)
        jarvis.services.register("companion", "ask", ask, supports_response=True)
        jarvis.services.register("lock", "unlock", unlock, supports_response=True)
        jarvis.services.register("demo", "tell", tell, supports_response=True)


async def manager_for(jarvis, clock: FrozenClock, **cfg) -> ScheduleManager:
    await async_setup(jarvis, cfg)
    manager = get_manager(jarvis)
    manager.now = clock  # type: ignore[assignment]
    return manager


async def settle(jarvis) -> None:
    """Wait for whatever the manager just set off.

    `async_drain` rather than a handful of `sleep(0)`s: a firing does several
    awaits — mint a task, save the store, call a service — and "enough turns of
    the loop" is a number that is right until somebody adds an await.
    """
    manager = get_manager(jarvis)
    if manager is not None:
        await manager.async_drain(timeout=10)
    # The slow kinds (research, code) are handed to the task engine rather than
    # run where they fire, so "whatever the manager just set off" now includes
    # whatever the engine picked up.
    engine = getattr(jarvis, "taskengine", None)
    if engine is not None:
        await engine.async_drain(timeout=10)
    await jarvis.async_block_till_done()


# --- reminders ------------------------------------------------------------------

async def test_a_reminder_fires_when_its_time_comes(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "title": "Bins", "message": "Take the bins out",
         "when": {"mode": "daily", "at": "19:00"}},
        allow_service=False,
    )
    await manager._tick()
    assert said.notified == [], "it fired thirteen hours early"

    clock.advance(hours=13, minutes=1)
    await manager._tick()
    await settle(jarvis)
    assert said.notified == ["Take the bins out"]


async def test_every_firing_shows_up_as_a_task(jarvis):
    """What "schedule tasks … should show as a progress bar" asked for."""
    clock = FrozenClock("2026-01-01T18:59")
    Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "title": "Bins", "message": "x",
         "when": {"mode": "daily", "at": "19:00"}},
        allow_service=False,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)

    scheduled = [t for t in jarvis.tasks.tasks if t.kind == "scheduled"]
    assert len(scheduled) == 1
    assert scheduled[0].title == "Bins"
    assert scheduled[0].status == "done"


async def test_a_daily_job_schedules_itself_again(jarvis):
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "title": "Bins", "message": "x",
         "when": {"mode": "daily", "at": "19:00"}},
        allow_service=False,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    job = next(iter(manager.jobs.values()))
    assert job.next_at == at("2026-01-02T19:00").timestamp()

    clock.advance(days=1)
    await manager._tick()
    await settle(jarvis)
    assert len(said.notified) == 2


async def test_a_one_shot_is_spent_after_it_runs(jarvis):
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "message": "once", "at": "2026-01-01T19:00:00"},
        allow_service=False,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert said.notified == ["once"]
    assert next(iter(manager.jobs.values())).next_at is None

    clock.advance(days=1)
    await manager._tick()
    await settle(jarvis)
    assert len(said.notified) == 1, "a spent one-shot fired again"


async def test_a_reminder_with_no_companion_still_leaves_a_record(jarvis):
    # The phone may be off, the desktop closed. The task's own result is the
    # record, and the task list is a surface, so the reminder is not lost.
    clock = FrozenClock("2026-01-01T18:59")
    manager = await manager_for(jarvis, clock)  # no Recorder: no companion at all
    await manager.async_add(
        {"kind": "notify", "message": "feed the cat", "at": "2026-01-01T19:00:00"},
        allow_service=False,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    task = next(t for t in jarvis.tasks.tasks if t.kind == "scheduled")
    assert task.status == "done"
    assert "feed the cat" in task.result


# --- coming back after being off ---------------------------------------------------

async def test_a_restart_does_not_replay_a_backlog(jarvis, tmp_path):
    """The loud failure: hourly, off for two days, comes back and speaks 48 times."""
    clock = FrozenClock("2026-01-01T00:00")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "message": "tick", "when": {"mode": "every", "minutes": 60}},
        allow_service=False,
    )

    clock.advance(days=2)
    await manager._async_settle()
    await settle(jarvis)
    assert said.notified == [], f"a backlog fired: {len(said.notified)} times"
    job = next(iter(manager.jobs.values()))
    assert job.missed > 40
    assert job.next_at > clock().timestamp()


async def test_a_reminder_missed_by_minutes_still_arrives(jarvis):
    clock = FrozenClock("2026-01-01T19:04")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    manager.jobs["j"] = job_from_dict(
        {
            "id": "j",
            "kind": "notify",
            "message": "the reminder",
            "at": "2026-01-01T19:00:00",
            "next_at": at("2026-01-01T19:00").timestamp(),
        }
    )
    await manager._async_settle()
    await settle(jarvis)
    assert said.notified == ["the reminder"]


async def test_a_reminder_missed_by_a_day_is_not_delivered_at_three_in_the_morning(jarvis):
    clock = FrozenClock("2026-01-02T03:00")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    manager.jobs["j"] = job_from_dict(
        {
            "id": "j",
            "kind": "notify",
            "message": "take the bins out",
            "at": "2026-01-01T19:00:00",
            "next_at": at("2026-01-01T19:00").timestamp(),
        }
    )
    await manager._async_settle()
    await settle(jarvis)
    assert said.notified == []
    # And it SAYS so, because an absence you infer is not information.
    job = manager.jobs["j"]
    assert job.missed == 1
    assert "not running" in job.last_result


async def test_jobs_survive_a_restart(jarvis, tmp_path):
    """A second manager over the same store, not a second whole Jarvis.

    The claim is about the store, and booting the app twice in one test buys
    nothing but a second attempt to reach Ollama.
    """
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "title": "Bins", "message": "x",
         "when": {"mode": "daily", "at": "19:00"}},
        allow_service=False,
    )
    await manager.stop()

    reborn = ScheduleManager(jarvis, store=Store(jarvis.config_dir, STORE_KEY))
    reborn.now = clock  # type: ignore[assignment]
    await reborn.async_load()
    assert [j.title for j in reborn.jobs.values()] == ["Bins"]
    assert next(iter(reborn.jobs.values())).next_at == at("2026-01-01T19:00").timestamp()


async def test_a_job_from_the_config_file_is_not_written_to_the_store(jarvis):
    # The file is the authority for its own jobs. Copying them into the store
    # would resurrect one somebody deleted from the file.
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(
        jarvis,
        clock,
        jobs=[{"id": "brief", "kind": "notify", "message": "morning",
               "when": {"mode": "daily", "at": "07:30"}}],
    )
    await manager.async_save()
    await manager.stop()

    reborn = ScheduleManager(jarvis, store=Store(jarvis.config_dir, STORE_KEY))
    reborn.now = clock  # type: ignore[assignment]
    await reborn.async_load()
    assert reborn.jobs == {}


# --- the gate ------------------------------------------------------------------------

async def test_a_scheduled_service_call_goes_through_the_same_gate_as_an_automation(jarvis):
    """Otherwise "schedule it" is the way round every Tier-3 control here.

    A held action, deferred by sixty seconds, arriving with nobody to ask.
    """
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    said.answer = "no"
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "service", "title": "Unlock", "service": "lock.unlock",
         "at": "2026-01-01T19:00:00"},
        allow_service=True,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)

    assert said.asked, "a gated service ran without anybody being asked"
    assert said.calls == [], "it unlocked the door anyway"


async def test_a_yes_lets_the_gated_action_through(jarvis):
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    said.answer = "yes"
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "service", "service": "lock.unlock", "at": "2026-01-01T19:00:00"},
        allow_service=True,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert [c[0] for c in said.calls] == ["lock.unlock"]


async def test_silence_is_a_no(jarvis):
    # Fail closed in every direction: no companion at all means nobody could be
    # asked, which is not the same as being told yes.
    clock = FrozenClock("2026-01-01T18:59")
    calls: list[str] = []

    async def unlock(call) -> dict:
        calls.append("ran")
        return {}

    jarvis.services.register("lock", "unlock", unlock, supports_response=True)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "service", "service": "lock.unlock", "at": "2026-01-01T19:00:00"},
        allow_service=True,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert calls == []


async def test_an_ungated_service_just_runs(jarvis):
    # The gate is for the dangerous verbs, not for everything.
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "service", "service": "demo.tell", "data": {"x": 1},
         "at": "2026-01-01T19:00:00"},
        allow_service=True,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert said.asked == []
    assert said.calls == [("demo.tell", {"x": 1})]


async def test_the_model_cannot_schedule_an_action_on_the_house(jarvis):
    """A tool that schedules service calls launders an injection through a delay.

    It arrives later, with no turn to attribute it to and no page to blame.
    """
    clock = FrozenClock("2026-01-01T06:00")
    Recorder(jarvis)
    manager = await manager_for(jarvis, clock)

    handler = jarvis.data["llm_tools"].get("schedule_task").handler
    result = await handler(
        {"kind": "service", "service": "lock.unlock", "at": "2026-01-01T19:00:00"}
    )
    assert result["status"] == "error"
    assert manager.jobs == {}


async def test_the_model_cannot_schedule_a_coding_job(jarvis):
    """Same hole as a scheduled service call, with a repository on the end.

    Starting a coding job directly is Tier 3 and asks a human. If the model
    could put one on a timer instead, the delay would be the way round the
    gate — and the diff would land at three in the morning with no turn to
    attribute it to.
    """
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    handler = jarvis.data["llm_tools"].get("schedule_task").handler
    result = await handler(
        {
            "kind": "code",
            "repo": "jarvis",
            "instruction": "delete the approval gate",
            "at": "2026-01-01T19:00:00",
        }
    )
    assert result["status"] == "error"
    assert manager.jobs == {}


async def test_the_console_may_schedule_a_coding_job_and_it_starts_one(jarvis, tmp_path):
    """The console's authority, and the job it actually mints.

    Asserted through the real `code` integration rather than a stub, because
    the thing worth pinning is that the schedule hands off to something that
    exists — a firing that logs "coding job started" while nothing runs is the
    exact failure the task registry was built to stop repeating.
    """
    from types import SimpleNamespace

    from jarvis.integrations import code as code_integration

    class Model:
        def chat(self, **kwargs):
            class _S:
                def __await__(inner):
                    async def _go():
                        return SimpleNamespace(content="nothing to do", tool_calls=[])

                    return _go().__await__()

            return _S()

    jarvis.data["llm"] = SimpleNamespace(client=Model(), model="m")
    await code_integration.async_setup(
        jarvis, {"repositories": [{"name": "proj", "path": str(tmp_path / "proj")}]}
    )

    clock = FrozenClock("2026-01-01T18:59")
    manager = await manager_for(jarvis, clock)
    added = await manager.async_add(
        {
            "kind": "code",
            "repo": "proj",
            "instruction": "add the missing null check",
            "when": {"mode": "daily", "at": "19:00"},
        },
        allow_service=True,
    )
    assert added["status"] == "ok"
    assert added["job"]["kind"] == "code"
    # The title says which repository, without one having to be written.
    assert "proj" in added["job"]["title"]

    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)

    started = [t for t in jarvis.tasks.tasks if t.kind == "code"]
    assert len(started) == 1
    assert "add the missing null check" in started[0].title
    assert "coding job started" in manager.jobs[added["job"]["id"]].last_result


async def test_a_scheduled_coding_job_needs_both_halves(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    for missing in (
        {"kind": "code", "repo": "proj", "when": {"mode": "daily", "at": "19:00"}},
        {"kind": "code", "instruction": "do a thing", "when": {"mode": "daily", "at": "19:00"}},
    ):
        assert (await manager.async_add(missing, allow_service=True))["status"] == "error"


async def test_a_job_with_a_repo_and_an_instruction_is_a_coding_job(jarvis):
    """Inferred, the way a `service` key already infers a service call.

    A configuration.yaml entry that plainly means one thing should not fail on
    a missing `kind:`.
    """
    job = job_from_dict(
        {"repo": "proj", "instruction": "do a thing", "when": {"mode": "daily", "at": "19:00"}}
    )
    assert job is not None
    assert job.kind == "code"


async def test_the_model_can_schedule_a_reminder(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    handler = jarvis.data["llm_tools"].get("schedule_task").handler
    result = await handler(
        {"kind": "notify", "message": "call your mother", "at": "2026-01-01T19:00:00"}
    )
    assert result["status"] == "ok"
    assert "19:00" in result["when"]
    assert len(manager.jobs) == 1


async def test_the_models_flat_arguments_become_a_schedule(jarvis):
    # A nested `when` is what a model gets wrong, usually by inventing a mode.
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    handler = jarvis.data["llm_tools"].get("schedule_task").handler

    assert (await handler({"kind": "notify", "message": "a", "daily_at": "07:30"}))["status"] == "ok"
    assert (
        await handler({"kind": "notify", "message": "b", "daily_at": "08:00", "days": ["mon"]})
    )["status"] == "ok"
    assert (
        await handler({"kind": "notify", "message": "c", "every_minutes": 30})
    )["status"] == "ok"
    modes = sorted(j.when.mode for j in manager.jobs.values())
    assert modes == ["daily", "every", "weekly"]


# --- editing ---------------------------------------------------------------------------

async def test_a_time_that_has_already_gone_is_refused(jarvis):
    clock = FrozenClock("2026-01-02T06:00")
    manager = await manager_for(jarvis, clock)
    result = await manager.async_add(
        {"kind": "notify", "message": "x", "at": "2026-01-01T19:00:00"}, allow_service=False
    )
    assert result["status"] == "error"
    assert "passed" in result["error"]


async def test_a_config_job_cannot_be_edited_or_removed_by_a_request(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(
        jarvis,
        clock,
        jobs=[{"id": "brief", "kind": "notify", "message": "morning",
               "when": {"mode": "daily", "at": "07:30"}}],
    )
    assert (await manager.async_remove("brief"))["status"] == "error"
    replaced = await manager.async_add(
        {"id": "brief", "kind": "notify", "message": "hijacked",
         "when": {"mode": "daily", "at": "07:30"}},
        allow_service=True,
    )
    assert replaced["status"] == "error"
    assert manager.jobs["brief"].payload["message"] == "morning"


async def test_a_job_can_be_turned_off_and_on(jarvis):
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    added = await manager.async_add(
        {"kind": "notify", "message": "x", "when": {"mode": "daily", "at": "19:00"}},
        allow_service=False,
    )
    job_id = added["job"]["id"]

    await manager.async_set_enabled(job_id, False)
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert said.notified == []

    await manager.async_set_enabled(job_id, True)
    await manager._tick()
    await settle(jarvis)
    assert said.notified == ["x"]


async def test_the_list_is_soonest_first(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "title": "late", "message": "x",
         "when": {"mode": "daily", "at": "23:00"}}, allow_service=False
    )
    await manager.async_add(
        {"kind": "notify", "title": "early", "message": "x",
         "when": {"mode": "daily", "at": "07:00"}}, allow_service=False
    )
    assert [j["title"] for j in manager.listing()] == ["early", "late"]


async def test_a_job_that_makes_no_sense_is_refused(jarvis):
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    assert (await manager.async_add({}, allow_service=False))["status"] == "error"
    assert (
        await manager.async_add({"kind": "research", "daily_at": "07:00"}, allow_service=False)
    )["status"] == "error"


def test_a_job_reads_its_kind_from_what_it_carries():
    assert job_from_dict({"service": "a.b", "at": "2026-01-01T00:00"}).kind == "service"
    assert job_from_dict({"question": "why", "at": "2026-01-01T00:00"}).kind == "research"
    assert job_from_dict({"message": "hi", "at": "2026-01-01T00:00"}).kind == KIND_NOTIFY


def test_a_service_job_needs_a_real_service_name():
    assert job_from_dict({"kind": "service", "service": "nodot", "at": "2026-01-01T00:00"}) is None


# --- the loop itself ----------------------------------------------------------------------

async def test_the_loop_sleeps_until_the_next_job_rather_than_polling(jarvis):
    """A schedule with nothing due for nine hours must not wake 1,600 times."""
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    assert manager._sleep_for() > 60  # nothing scheduled at all

    await manager.async_add(
        {"kind": "notify", "message": "x", "when": {"mode": "every", "minutes": 5}},
        allow_service=False,
    )
    # Real `time.time()` here, not the frozen clock, because that is what the
    # sleep is measured against.
    assert 0 < manager._sleep_for() <= 5 * 60 + 1


async def test_the_ticker_can_actually_be_stopped(jarvis):
    """A scheduler that cannot be stopped is one that fires during shutdown.

    The first version of the loop slept with `asyncio.wait_for(..., timeout)`
    and swallowed `TimeoutError` to mean "nothing woke us". But `wait_for` also
    raises `TimeoutError` when an outer cancellation races its timeout, so
    swallowing one swallowed the other: `cancel()` was requested, the loop ate
    it, and `stop()` waited for a task that was never going to end.

    This asserts the thing that was broken — cancel, and it is REALLY over.
    """
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "notify", "message": "x", "when": {"mode": "every", "minutes": 5}},
        allow_service=False,
    )
    ticker = manager._loop
    assert ticker is not None and not ticker.done()

    import asyncio

    await asyncio.wait_for(manager.stop(), 5)
    assert ticker.done(), "the ticker ignored its own cancellation"
    assert manager._loop is None


async def test_stopping_twice_is_not_an_error(jarvis):
    # `stop()` is both a shutdown callback and something a caller may do.
    clock = FrozenClock("2026-01-01T06:00")
    manager = await manager_for(jarvis, clock)
    await manager.stop()
    await manager.stop()


async def test_one_bad_job_does_not_stop_the_others(jarvis):
    clock = FrozenClock("2026-01-01T18:59")
    said = Recorder(jarvis)

    async def explode(call) -> dict:
        raise RuntimeError("this service is broken")

    jarvis.services.register("demo", "explode", explode, supports_response=True)
    manager = await manager_for(jarvis, clock)
    await manager.async_add(
        {"kind": "service", "title": "bad", "service": "demo.explode",
         "at": "2026-01-01T19:00:00"}, allow_service=True
    )
    await manager.async_add(
        {"kind": "notify", "title": "good", "message": "still here",
         "at": "2026-01-01T19:00:00"}, allow_service=False
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)

    assert said.notified == ["still here"]
    failed = next(t for t in jarvis.tasks.tasks if t.title == "bad")
    assert failed.status == "error"
    assert "broken" in failed.error


async def test_a_reminder_lands_in_the_inbox_whether_or_not_a_phone_is_paired(jarvis):
    """The reminder is a moment before it is a phone notification.

    With no companion paired, "remind me in a minute" fired into a task
    result and a log line — nothing a person watches. Whatever the phone
    does, the notifications inbox gets the reminder, with a kind the console
    can show as a tag and a title the rig can wait for.
    """
    inbox: list[dict[str, Any]] = []

    async def add(call: Any) -> dict[str, Any]:
        inbox.append(dict(call.data or {}))
        return {"recorded": True}

    jarvis.services.register("notifications", "add", add, supports_response=True)
    clock = FrozenClock("2026-01-01T18:59")
    manager = await manager_for(jarvis, clock)  # no Recorder: no companion at all
    await manager.async_add(
        {"kind": "notify", "message": "check the oven", "at": "2026-01-01T19:00:00"},
        allow_service=False,
    )
    clock.advance(minutes=2)
    await manager._tick()
    await settle(jarvis)
    assert inbox and inbox[0]["kind"] == "reminder" and inbox[0]["title"] == "check the oven"
    assert inbox[0]["source"] == "schedule"


async def test_the_same_alarm_asked_for_twice_in_a_minute_is_one_alarm(jarvis):
    """M78. "I asked it to set an alarm, why did it do it twice?" — the phone
    and the console both heard the sentence, and the model scheduled "Wake
    up" and "Wake-up alarm", both weekdays at 07:30, forty seconds apart. The
    second is refused and the first is named; a different time is a
    different alarm; the same alarm after the window is a deliberate one."""
    clock = FrozenClock("2026-01-01T06:00")
    Recorder(jarvis)
    manager = await manager_for(jarvis, clock)
    first = await manager.async_add(
        {"kind": "notify", "title": "Wake up", "message": "Wake up",
         "when": {"mode": "daily", "at": "07:30"}},
        allow_service=False,
    )
    assert first["status"] == "ok"
    clock.advance(seconds=40)
    second = await manager.async_add(
        {"kind": "notify", "title": "Wake-up alarm", "message": "Time to get up",
         "when": {"mode": "daily", "at": "07:30"}},
        allow_service=False,
    )
    assert second["status"] == "error", second
    assert "already scheduled" in second["error"] and "Wake up" in second["error"]
    assert second["job"]["id"] == first["job"]["id"]
    assert len(manager.jobs) == 1

    other = await manager.async_add(
        {"kind": "notify", "title": "Lunch", "message": "Lunch",
         "when": {"mode": "daily", "at": "12:30"}},
        allow_service=False,
    )
    assert other["status"] == "ok" and len(manager.jobs) == 2
