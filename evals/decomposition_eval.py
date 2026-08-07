#!/usr/bin/env python3
"""P8 ship/no-ship gate for Tier-3 multi-agent fan-out.

A 3-part request must decompose into 3 scoped sub-tasks that merge into one
coherent answer. If this fails on your model, DOCUMENT it in DEVIATIONS.md
and ship Tier-2 only — the orchestrator still runs, delegate_to_agents just
isn't advertised to the agent.

Backends:
  --backend orchestrator   POST /delegate on jarvis-orchestrator (real path;
                           env ORCHESTRATOR_URL, ORCHESTRATOR_TOKEN). The
                           model both decomposes (in HA, upstream) and here
                           we test the fan-out+synthesis directly by handing
                           it pre-split tasks AND a compound one via a
                           planner call.
  --backend ollama         Direct Ollama; planner decomposes, we score.

Scoring is heuristic + assertable, not another LLM judge, so the gate is
deterministic: each case lists 3 concept groups; a pass requires the
synthesis to touch all three (>=1 keyword per group) and the decomposition
to yield exactly/at least 3 non-trivial sub-tasks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

PLANNER_SYSTEM = (
    "Split the user's request into the minimal set of independent sub-tasks, "
    "one per line, prefixed 'TASK: '. Do not answer them. If the request has "
    "three distinct parts, produce three TASK lines."
)


def load_cases() -> list[dict]:
    return yaml.safe_load((HERE / "decomposition_cases.yaml").read_text())["cases"]


def ollama_chat(system: str, user: str) -> str:
    import httpx

    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    model = os.environ.get("PLANNER_MODEL", "qwen3:8b")
    r = httpx.post(
        f"{url}/api/chat",
        json={"model": model, "stream": False,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def parse_tasks(text: str) -> list[str]:
    tasks = [m.group(1).strip()
             for line in text.splitlines()
             if (m := re.match(r"\s*TASK:\s*(.+)", line))]
    return [t for t in tasks if len(t) > 3]


def delegate_via_orchestrator(tasks: list[str]) -> dict:
    import httpx

    url = os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8188")
    token = os.environ["ORCHESTRATOR_TOKEN"]
    r = httpx.post(f"{url}/delegate", json={"tasks": tasks},
                   headers={"Authorization": f"Bearer {token}"}, timeout=600)
    r.raise_for_status()
    return r.json()


def synthesis_covers(synthesis: str, groups: list[list[str]]) -> list[bool]:
    s = synthesis.lower()
    return [any(k.lower() in s for k in g) for g in groups]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["orchestrator", "ollama"], default="ollama")
    args = ap.parse_args(argv)

    cases = load_cases()
    results, passed = [], 0
    for c in cases:
        plan = ollama_chat(PLANNER_SYSTEM, c["request"])
        tasks = parse_tasks(plan)
        enough_tasks = len(tasks) >= c.get("min_tasks", 3)

        if not tasks:
            tasks = c["fallback_tasks"]  # so synthesis is still exercised
        if args.backend == "orchestrator":
            out = delegate_via_orchestrator(tasks)
            synthesis = out.get("synthesis", "")
        else:
            from fanout import fan_out  # noqa
            import asyncio

            out = asyncio.run(
                fan_out(tasks, os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                        os.environ.get("PLANNER_MODEL", "qwen3:8b"))
            )
            synthesis = out.get("synthesis", "")

        covered = synthesis_covers(synthesis, c["concept_groups"])
        ok = enough_tasks and all(covered)
        passed += ok
        results.append({"id": c["id"], "ok": ok, "n_tasks": len(tasks),
                        "enough_tasks": enough_tasks,
                        "coverage": covered, "synthesis": synthesis[:300]})
        print(f"[{'PASS' if ok else 'FAIL'}] {c['id']}: "
              f"tasks={len(tasks)} coverage={covered}")

    rate = passed / len(cases)
    gate = rate >= 0.6  # ship Tier-3 if a clear majority decompose+merge
    (HERE / "last_decomposition_run.json").write_text(
        json.dumps({"rate": rate, "gate_passed": gate, "results": results}, indent=2)
    )
    print(f"\n{passed}/{len(cases)} passed (rate {rate:.0%}). "
          f"Tier-3 ship gate: {'PASS' if gate else 'FAIL — ship Tier-2, see DEVIATIONS.md'}")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
