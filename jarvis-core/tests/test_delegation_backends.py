"""Delegation across backends: one plan, several kinds of worker.

M20 gave the lead specialists. M42 lets a plan entry name a SUBSYSTEM instead —
a research run, a coding job — and the point of these tests is that the lead
does not care which: the same `Finding` comes back, with the child task id, so
the console draws one tree and the rollup reads as one answer.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.agents.backends import (
    BACKEND_AGENT,
    BACKEND_CODE,
    BACKEND_RESEARCH,
    split,
    wait_for_task,
)


def test_a_plan_entry_names_a_backend_or_a_specialist():
    assert split("research") == (BACKEND_RESEARCH, "")
    assert split("code") == (BACKEND_CODE, "")
    assert split("code:claude-code") == (BACKEND_CODE, "claude-code")
    # Anything else is a specialist's name, including one that looks odd.
    assert split("researcher") == (BACKEND_AGENT, "researcher")
    assert split("librarian") == (BACKEND_AGENT, "librarian")
    assert split("") == (BACKEND_AGENT, "")


class FakeTask:
    def __init__(self, task_id: str, status: str = "running") -> None:
        self.id = task_id
        self.status = status
        self.result = ""
        self.error = ""


class FakeRegistry:
    def __init__(self, task: FakeTask) -> None:
        self.task = task

    def get(self, task_id: str):
        return self.task if task_id == self.task.id else None


class FakeJarvis:
    def __init__(self, task: FakeTask) -> None:
        self.tasks = FakeRegistry(task)
        self.data: dict = {}


@pytest.mark.asyncio
async def test_waiting_stops_when_the_task_stops():
    task = FakeTask("t1")
    jarvis = FakeJarvis(task)

    async def finish_soon():
        await asyncio.sleep(0.05)
        task.status = "done"
        task.result = "the report"

    asyncio.ensure_future(finish_soon())
    waited = await wait_for_task(jarvis, "t1", timeout=5)
    assert waited.status == "done" and waited.result == "the report"


@pytest.mark.asyncio
async def test_a_cancelled_task_ends_the_wait_rather_than_timing_out():
    """An approval nobody answered ends a coding job. The lead must say so."""
    task = FakeTask("t2", status="cancelled")
    waited = await wait_for_task(FakeJarvis(task), "t2", timeout=5)
    assert waited.status == "cancelled"


@pytest.mark.asyncio
async def test_a_task_that_disappears_is_not_an_infinite_wait():
    assert await wait_for_task(FakeJarvis(FakeTask("t3")), "gone", timeout=5) is None


@pytest.mark.asyncio
async def test_research_reports_the_task_it_started():
    from jarvis.agents import backends

    class Services:
        def has_service(self, domain, service):
            return (domain, service) == ("research", "run")

        async def async_call(self, domain, service, data, **_kw):
            return {"status": "started", "task_id": "r1"}

    task = FakeTask("r1", status="done")
    task.result = "the boiler is serviced every March [1]"
    jarvis = FakeJarvis(task)
    jarvis.services = Services()
    outcome = await backends.run_research(jarvis, "when is the boiler serviced")
    assert outcome["ok"] is True
    assert outcome["task_id"] == "r1"
    assert "March" in outcome["text"]


@pytest.mark.asyncio
async def test_research_that_is_not_set_up_says_so():
    from jarvis.agents import backends

    class Services:
        def has_service(self, *_a):
            return False

    jarvis = FakeJarvis(FakeTask("x"))
    jarvis.services = Services()
    outcome = await backends.run_research(jarvis, "anything")
    assert outcome["ok"] is False and "not set up" in outcome["error"]


@pytest.mark.asyncio
async def test_a_failed_research_run_is_reported_as_failed():
    from jarvis.agents import backends

    class Services:
        def has_service(self, *_a):
            return True

        async def async_call(self, *_a, **_kw):
            return {"status": "started", "task_id": "r9"}

    task = FakeTask("r9", status="error")
    task.error = "no search returned anything"
    jarvis = FakeJarvis(task)
    jarvis.services = Services()
    outcome = await backends.run_research(jarvis, "q")
    assert outcome["ok"] is False and "no search" in outcome["error"]


@pytest.mark.asyncio
async def test_a_coding_job_will_not_guess_between_repositories():
    """Aiming a coding job at the wrong repository is expensive helpfulness."""
    from jarvis.agents import backends
    from jarvis.integrations.code import CodeConfig

    jarvis = FakeJarvis(FakeTask("c1"))
    jarvis.data["code"] = {"config": CodeConfig.from_config({
        "repositories": [
            {"name": "app", "path": "/tmp/app"},
            {"name": "site", "path": "/tmp/site"},
        ]
    })}
    outcome = await backends.run_code(jarvis, "fix the tests")
    assert outcome["ok"] is False and "which repository" in outcome["error"]
