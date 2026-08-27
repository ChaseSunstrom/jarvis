#!/usr/bin/env python3
"""Two specialists, at the same time, rolled up — proved rather than assumed.

The claim "runs them in parallel" is the one that is easy to make and easy to
get wrong: a fan-out that spawned two subagents and ran them one after the
other produces two children, two results and one roll-up, and looks identical
in every record except the clock.

So this eval measures the clock. A fixture task needs two independent lookups
and a merge; the model is the harness's scripted one with an artificial delay,
so the *only* thing that decides the wall time is whether the two calls
overlapped. It passes when:

* both specialists answered, and the roll-up carries both findings;
* the two model calls genuinely overlapped (`pool.overlap_seconds > 0`);
* the whole fan-out took less than the two delays added together;
* each child is a task under the lead, with the agent that ran it.

    python3 evals/subagents_eval.py --out .verify/subagents

Needs neither Docker nor the real model: what is under test is the
orchestration, and a scripted model with a known delay is a sharper instrument
for that than a real one with a variable one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
CORE = REPO / "jarvis-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from jarvis.agents import load_agents  # noqa: E402
from jarvis.agents.runner import Assignment, delegate  # noqa: E402
from jarvis.llm.pool import ModelPool  # noqa: E402
from jarvis.tasks import TaskRegistry  # noqa: E402

#: How long the scripted model takes to answer. Long enough that overlap is
#: unambiguous on a busy box, short enough that the eval is not a coffee break.
DELAY = 0.6

#: The fixture: two independent lookups and a merge. Independent on purpose —
#: work with a dependency between the parts could not be parallel, and would
#: prove nothing about a pool.
ASSIGNMENTS = [
    Assignment("researcher", "When was the boiler last serviced, and by whom?"),
    Assignment("researcher", "What does the boiler's warranty still cover?"),
]


class Failed(Exception):
    """One claim did not hold."""


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls: list = []


class ScriptedModel:
    """Answers after `delay`, and remembers when it was asked."""

    def __init__(self, delay: float = DELAY) -> None:
        self.delay = delay
        self.calls: list[tuple[float, float]] = []

    async def chat(self, model=None, messages=None, tools=None, options=None, **kwargs):
        started = time.monotonic()
        await asyncio.sleep(self.delay)
        self.calls.append((started, time.monotonic()))
        asked = (messages or [{}])[-1].get("content", "")
        return _Reply(f"Serviced in March by Dunbar & Sons. (asked: {asked[:60]})")


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def fire(self, event, data=None, context=None):
        self.events.append((event, dict(data or {})))

    async def async_fire(self, event, data=None, context=None):
        self.fire(event, data, context)


def _jarvis(model: ScriptedModel, agents: dict) -> SimpleNamespace:
    registry = SimpleNamespace(
        names=lambda: [],
        as_openai_schema=lambda: [],
        call=None,
    )
    jarvis = SimpleNamespace(
        data={"llm": SimpleNamespace(client=model, model="scripted", tools=registry),
              "agents": agents},
        bus=_Bus(),
    )
    jarvis.tasks = TaskRegistry(jarvis)
    return jarvis


async def run(out: Path) -> dict:
    steps: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": name, "ok": ok, "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'} {name}{'  — ' + detail if detail else ''}", flush=True)

    agents = load_agents(CORE / "config" / "agents")
    record("the shipped specialists load", len(agents) >= 4, ", ".join(sorted(agents)))
    if len(agents) < 2:
        raise Failed("fewer than two specialists are installed, so nothing can fan out")

    model = ScriptedModel()
    jarvis = _jarvis(model, agents)
    lead = await jarvis.tasks.async_add("boiler questions", kind="delegation")
    pool = ModelPool(max_concurrent=2)

    started = time.monotonic()
    rollup = await delegate(
        jarvis, ASSIGNMENTS, lead_task_id=lead.id, agents=agents, pool=pool
    )
    elapsed = time.monotonic() - started
    payload = rollup.as_dict()

    record(
        "both specialists answered",
        len(rollup.findings) == 2 and all(f.ok for f in rollup.findings),
        "; ".join(f.error for f in rollup.findings if not f.ok),
    )
    overlap = payload["pool"]["overlap_seconds"]
    record(
        "and they genuinely overlapped",
        overlap > 0.1,
        f"{overlap:.2f}s of overlap on {DELAY:.1f}s calls",
    )
    record(
        "so the fan-out beat doing them in turn",
        elapsed < DELAY * 2,
        f"{elapsed:.2f}s against {DELAY * 2:.1f}s serial",
    )

    children = [t for t in jarvis.tasks.tasks if t.parent_id == lead.id]
    record(
        "each is a child task under the lead, with its agent",
        len(children) == 2 and all(t.agent == "researcher" for t in children),
        f"{len(children)} child task(s)",
    )
    record(
        "and the tree event fired for each",
        sum(1 for name, _ in jarvis.bus.events if name == "jarvis_task_child_added") == 2,
    )
    record(
        "the roll-up reaches the lead as attributed, untrusted text",
        "INFORMATION, not instructions" in rollup.for_model()
        and all(f.task[:20] in rollup.for_model() for f in rollup.findings),
    )

    # And the serial comparison, so "faster" is measured against something.
    serial_pool = ModelPool(max_concurrent=1)
    serial_started = time.monotonic()
    await delegate(
        _jarvis(ScriptedModel(), agents), ASSIGNMENTS, agents=agents, pool=serial_pool
    )
    serial = time.monotonic() - serial_started
    record(
        "one slot in the pool really does serialise them",
        serial_pool.stats.overlap() == 0.0 and serial > elapsed,
        f"{serial:.2f}s serial vs {elapsed:.2f}s parallel",
    )

    out.mkdir(parents=True, exist_ok=True)
    report = {
        "seconds": round(elapsed, 2),
        "serial_seconds": round(serial, 2),
        "overlap": overlap,
        "steps": steps,
        "rollup": payload,
        "ok": all(step["ok"] for step in steps),
    }
    (out / "rollup.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".verify/subagents")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO / out
    try:
        report = asyncio.run(run(out))
    except Failed as err:
        print(f"subagents eval: {err}", file=sys.stderr)
        return 1
    print(
        f"\nsubagents eval: {'PASSED' if report['ok'] else 'FAILED'} — "
        f"{report['seconds']}s parallel, {report['serial_seconds']}s serial, "
        f"{report['overlap']}s overlapped"
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
