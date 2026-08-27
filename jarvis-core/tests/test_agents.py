"""Specialists in a folder, and what running one is allowed to do.

The format is the one `skills/` already uses, for the same reason: an operator
who has written one should not have to learn a second thing. What is pinned
here is the part that is not cosmetic — a definition cannot grant itself a
tool, cannot exceed its context budget, and cannot spawn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agents import DEFAULT_CONTEXT_BUDGET, load_agents, parse_agent
from jarvis.agents.runner import Assignment, delegate
from jarvis.llm.pool import ModelPool

pytestmark = pytest.mark.asyncio

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "agents"

GOOD = """---
name: researcher
role: Finds things out.
tools: [web_search, web_fetch]
max_tokens: 900
context_budget: 2000
---

You are the researcher.
"""


# --- the format --------------------------------------------------------------


def test_a_definition_is_frontmatter_and_a_prompt():
    agent = parse_agent(GOOD)
    assert agent.name == "researcher"
    assert agent.tools == ("web_search", "web_fetch")
    assert agent.max_tokens == 900
    assert agent.prompt.startswith("You are the researcher")


def test_a_definition_with_no_role_is_refused():
    """The lead picks a specialist by reading one line about each."""
    with pytest.raises(ValueError, match="no `role`"):
        parse_agent("---\nname: x\n---\n\nbody")


def test_a_definition_with_no_body_is_refused():
    with pytest.raises(ValueError, match="body is empty"):
        parse_agent("---\nname: x\nrole: does things\n---\n")


def test_an_absurd_budget_is_bounded_rather_than_obeyed():
    agent = parse_agent("---\nname: x\nrole: r\ncontext_budget: 999999999\n---\nbody")
    assert agent.context_budget <= 60_000
    agent = parse_agent("---\nname: x\nrole: r\ncontext_budget: nonsense\n---\nbody")
    assert agent.context_budget == DEFAULT_CONTEXT_BUDGET


def test_a_broken_file_does_not_stop_the_others_loading(tmp_path):
    (tmp_path / "good.md").write_text(GOOD)
    (tmp_path / "broken.md").write_text("no frontmatter here")
    found = load_agents(tmp_path)
    assert set(found) == {"researcher"}


def test_the_four_shipped_specialists_load():
    found = load_agents(SHIPPED)
    assert set(found) == {"researcher", "coder", "verifier", "summarizer"}
    for agent in found.values():
        assert agent.role and agent.prompt


def test_none_of_them_can_delegate():
    """One level deep, by construction: recursion is how a fan-out becomes forty."""
    for agent in load_agents(SHIPPED).values():
        assert "delegate_to_agents" not in agent.tools, agent.name


def test_a_definition_cannot_grant_itself_a_tool_that_does_not_exist():
    agent = parse_agent("---\nname: x\nrole: r\ntools: [web_fetch, unlock_everything]\n---\nb")
    assert agent.allowed(["web_fetch", "get_state"]) == ["web_fetch"]


def test_and_one_with_no_tools_gets_the_leads():
    """"Said nothing" means "whatever the lead has", not "nothing"."""
    agent = parse_agent("---\nname: x\nrole: r\n---\nbody")
    assert agent.allowed(["a", "b"]) == ["a", "b"]


# --- running them ------------------------------------------------------------


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls: list = []


class _Model:
    """A model that takes a moment, so overlap is a real measurement."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.prompts: list[str] = []

    async def chat(self, model=None, messages=None, tools=None, options=None, **kwargs):
        self.prompts.append(messages[-1]["content"])
        await asyncio.sleep(self.delay)
        return _Reply(f"answer to: {messages[-1]['content'][:40]}")


def _jarvis(model: _Model, agents: dict) -> SimpleNamespace:
    registry = SimpleNamespace(
        names=lambda: ["web_search", "web_fetch"],
        as_openai_schema=lambda: [],
        call=None,
    )
    return SimpleNamespace(
        data={"llm": SimpleNamespace(client=model, model="m", tools=registry), "agents": agents},
        tasks=None,
    )


async def test_two_specialists_run_at_the_same_time():
    agents = load_agents(SHIPPED)
    model = _Model(delay=0.08)
    pool = ModelPool(max_concurrent=2)
    rollup = await delegate(
        _jarvis(model, agents),
        [Assignment("researcher", "one"), Assignment("researcher", "two")],
        agents=agents,
        pool=pool,
    )
    assert len(rollup.findings) == 2
    assert all(f.ok for f in rollup.findings)
    # The claim "in parallel", measured rather than asserted by structure.
    assert pool.stats.overlap() > 0.03, pool.snapshot()


async def test_a_slow_specialist_does_not_lose_the_others():
    agents = load_agents(SHIPPED)

    class _Mixed(_Model):
        async def chat(self, model=None, messages=None, tools=None, options=None, **kwargs):
            if "slow" in messages[-1]["content"]:
                raise RuntimeError("the model server went away")
            return _Reply("fine")

    rollup = await delegate(
        _jarvis(_Mixed(), agents),
        [Assignment("researcher", "slow one"), Assignment("researcher", "quick one")],
        agents=agents,
        pool=ModelPool(max_concurrent=2),
    )
    assert [f.ok for f in rollup.findings] == [False, True]
    assert "went away" in rollup.findings[0].error


async def test_an_unknown_specialist_is_a_finding_not_a_crash():
    agents = load_agents(SHIPPED)
    rollup = await delegate(
        _jarvis(_Model(), agents),
        [Assignment("astrologer", "what is my sign")],
        agents=agents,
        pool=ModelPool(),
    )
    assert rollup.findings[0].ok is False
    assert "no agent called" in rollup.findings[0].error
    # And it says what there IS, because the lead is a model and can retry.
    assert "researcher" in rollup.findings[0].error


async def test_the_task_a_specialist_is_given_is_cut_to_its_budget():
    agents = {"researcher": parse_agent(GOOD)}   # context_budget: 2000
    model = _Model()
    await delegate(
        _jarvis(model, agents),
        [Assignment("researcher", "x" * 9000)],
        agents=agents,
        pool=ModelPool(),
    )
    assert len(model.prompts[0]) <= 2000
    assert "truncated" in model.prompts[0]


async def test_a_fan_out_is_capped():
    agents = load_agents(SHIPPED)
    rollup = await delegate(
        _jarvis(_Model(delay=0.0), agents),
        [Assignment("researcher", f"q{i}") for i in range(20)],
        agents=agents,
        pool=ModelPool(max_concurrent=4),
    )
    assert len(rollup.findings) == 6


async def test_children_are_tasks_under_the_lead():
    from jarvis.tasks import TaskRegistry

    class _Bus:
        def __init__(self):
            self.events = []

        def fire(self, event, data=None, context=None):
            self.events.append((event, data))

    agents = load_agents(SHIPPED)
    jarvis = _jarvis(_Model(delay=0.0), agents)
    jarvis.bus = _Bus()
    jarvis.tasks = TaskRegistry(jarvis)
    lead = await jarvis.tasks.async_add("lead", kind="delegation")

    await delegate(
        jarvis,
        [Assignment("researcher", "one"), Assignment("verifier", "two")],
        lead_task_id=lead.id,
        agents=agents,
        pool=ModelPool(max_concurrent=2),
    )
    children = [t for t in jarvis.tasks.tasks if t.parent_id == lead.id]
    assert len(children) == 2
    assert {t.agent for t in children} == {"researcher", "verifier"}
    assert all(t.status == "done" for t in children)
    # And the tree event fired, which is what the console hangs them from.
    assert any(name == "jarvis_task_child_added" for name, _ in jarvis.bus.events)


# --- the tool, which is the only way anything reaches a specialist ------------


async def test_the_tool_acknowledges_and_reports_rather_than_waiting():
    """Tier 2 is "acknowledge, then report", and a fan-out is the clearest case.

    Four specialists reading four pages is minutes. A conversational turn that
    waited for them timed out at 240 seconds in the live rig, which is what
    sent this to the background — the findings arrive on the task, where
    `task_status` answers "how is that going".
    """
    import asyncio as _asyncio

    from jarvis.integrations.agents import async_setup
    from jarvis.llm.tools import ToolRegistry
    from jarvis.tasks import TaskRegistry

    class _Bus:
        def __init__(self):
            self.events = []

        def fire(self, event, data=None, context=None):
            self.events.append((event, data))

    model = _Model(delay=0.02)
    registry = ToolRegistry(jarvis=None)
    jarvis = SimpleNamespace(
        data={"llm": SimpleNamespace(client=model, model="m", tools=registry),
              "llm_tools": registry},
        config_dir=str(SHIPPED.parent),
        bus=_Bus(),
    )
    jarvis.tasks = TaskRegistry(jarvis)
    spawned: list = []
    jarvis.async_create_task = lambda coro: spawned.append(_asyncio.ensure_future(coro))

    assert await async_setup(jarvis, {"path": "agents"}) is True
    answer = await registry.call(
        "delegate_to_agents",
        {"tasks": [{"agent": "researcher", "task": "one"},
                   {"agent": "researcher", "task": "two"}]},
    )
    # It came back at once, with a task to watch and no invented findings.
    assert answer["status"] == "started"
    assert answer["task_id"]
    assert "findings" not in answer
    assert "do NOT invent their findings" in answer["message"]

    await _asyncio.gather(*spawned)
    lead = jarvis.tasks.get(answer["task_id"])
    assert lead.status == "done"
    # And the result is the findings themselves, because that is what
    # `task_status` reads when somebody asks how it went.
    assert "answer to:" in lead.result
    assert len([t for t in jarvis.tasks.tasks if t.parent_id == lead.id]) == 2
