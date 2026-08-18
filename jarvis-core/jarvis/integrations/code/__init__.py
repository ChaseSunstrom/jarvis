"""`code` — Jarvis Code: a coding agent that works in your repositories.

    code:
      model: ""                 # override the conversation model for this work
      max_rounds: 40            # tool calls one job may take
      max_minutes: 20
      sandbox: ""               # a command prefix every check runs behind
      repositories:
        - name: jarvis
          path: ~/src/jarvis
          description: the assistant itself
          writable: true
          checks:
            - pytest -q
            - ruff check .

One task kind (`code`), four services, one tool, and a page on the console.

## What it is

    plan      one model call -> a checklist, which becomes the task's steps
    work      a bounded loop: list, read, search, edit, write, run_check
    check     the repository's OWN commands, never a shell
    report    a branch, a diff, and a summary

It is the local twin of `orchestrator.code_task`. That one posts a repository
name to a container and polls a job id; this one runs here, on repositories the
operator named, and reports through `jarvis/tasks.py` — so the console draws a
real progress bar out of the model's own plan rather than a spinner, and
cancelling it stops it.

## The sandbox, stated exactly

"Sandboxed" is a word that gets used to mean anything, so here is the list, and
nothing outside it is claimed:

1. **A repository is opt-in.** Only paths in `repositories:` exist. There is no
   tool that takes an arbitrary directory.
2. **Writing is opt-in per repository.** `writable: false` is the default and
   the edit tools are not even offered to the model — it cannot call what it
   cannot see.
3. **Paths are confined** by `integrations/files/paths.py`: the same resolver,
   with the same symlink check, used by the files integration. Not a second
   implementation.
4. **There is no shell.** `run_check` runs a whole string from the repository's
   own `checks:` list, matched exactly, split with `shlex`, executed with
   `create_subprocess_exec`. The model chooses *whether*, never *what*.
5. **A job never touches your branch.** It refuses to start on a dirty tree,
   makes `jarvis/<date>-<job>`, and stops. A person merges it, or does not.
6. **`sandbox:`**, if set, is a command prefix — `docker run --rm --network
   none …`, `bwrap …`, `firejail …` — that every check runs behind. It is the
   operator's own wrapper because only they know what their checks need; when
   it is empty, a check runs as this process does, and the console says so
   rather than implying an isolation that is not there.

Point 6 is the honest one. A check command *is* arbitrary code — it is the
repository's test suite. It is the operator's arbitrary code, written before
the job existed, but a test suite that pulls from the network is still a test
suite that pulls from the network, and that is what the wrapper is for.

## Why the model's tool is Tier 3

Starting a job on a writable repository edits files on a real disk. The tool
form (`start_coding_job`) is approval-gated and `code.run` is in
`GATED_SERVICES`, so an automation cannot reach around it.

The name is `start_coding_job` and not `code_task` because `orchestrator`
already registers a `code_task` — the remote one, at Tier 2 — and two
integrations meaning different things by one tool name is an ordering
accident waiting to happen. `tests/test_tool_names.py` pins that no two
integrations claim a name.

The console's own button is not gated: that request carried a bearer token,
whereas a tool call may have been shaped by a page the model read. Same
asymmetry, same reason, as scheduling a service call — and `schedule:`'s `code`
kind follows the same rule, so a timer cannot be the way round the gate
either.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...services import ServiceCall
from ...tasks import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING
from .agent import CodeAgent, CodeRun, MAX_ROUNDS, Stopped
from .workspace import GitError, PathRefused, Repo, Workspace, repo_from_dict

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...tasks import Task

_LOGGER = logging.getLogger(__name__)

DOMAIN = "code"
#: The model is not optional — planning and the loop are both model calls — and
#: `llm` is also where the tool registry lives.
DEPENDENCIES = ["llm"]

KIND = "code"
DATA_CONFIG = "config"
DATA_RUNS = "runs"
DATA_RESULTS = "results"

MAX_INSTRUCTION_CHARS = 2000
#: Finished runs kept in memory for the console to open. The task itself
#: outlives this; what expires is the diff, which is the large part.
MAX_KEPT = 20


@dataclass
class CodeConfig:
    repositories: dict[str, Repo] = field(default_factory=dict)
    model: str = ""
    max_rounds: int = MAX_ROUNDS
    max_seconds: float = 20 * 60
    #: A command prefix every check runs behind. Empty means none, and the
    #: console says as much rather than implying isolation there is not.
    sandbox: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Any) -> "CodeConfig":
        data = config if isinstance(config, dict) else {}
        repos: dict[str, Repo] = {}
        for raw in data.get("repositories") or []:
            repo = repo_from_dict(raw)
            if repo is None:
                _LOGGER.warning("code: ignoring a repository with no name or path")
                continue
            if repo.name in repos:
                _LOGGER.warning("code: two repositories called %r; keeping the first", repo.name)
                continue
            repos[repo.name] = repo

        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(data.get(key, default) or default)
            except (TypeError, ValueError):
                value = default
            return max(low, min(value, high))

        raw_sandbox = data.get("sandbox") or ""
        if isinstance(raw_sandbox, (list, tuple)):
            sandbox = [str(part) for part in raw_sandbox if str(part).strip()]
        else:
            sandbox = shlex.split(str(raw_sandbox))

        return cls(
            repositories=repos,
            model=str(data.get("model") or "").strip(),
            max_rounds=_int("max_rounds", MAX_ROUNDS, 4, 200),
            max_seconds=_int("max_minutes", 20, 1, 120) * 60.0,
            sandbox=sandbox,
        )

    def listing(self) -> list[dict[str, Any]]:
        return [repo.as_dict() for repo in self.repositories.values()]


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def _store(jarvis: "Jarvis") -> dict[str, Any]:
    return jarvis.data.setdefault(DOMAIN, {})


def get_config(jarvis: "Jarvis") -> CodeConfig | None:
    store = jarvis.data.get(DOMAIN)
    if not isinstance(store, dict):
        return None
    cfg = store.get(DATA_CONFIG)
    return cfg if isinstance(cfg, CodeConfig) else None


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = CodeConfig.from_config(config)
    store = _store(jarvis)
    store[DATA_CONFIG] = cfg
    store.setdefault(DATA_RUNS, {})
    store.setdefault(DATA_RESULTS, {})

    _register_services(jarvis)
    _register_tools(jarvis)

    async def _shutdown() -> None:
        """Stop every job in flight.

        The tasks are left `running` in the store deliberately: `Task.restored()`
        turns those into errors on the next load, which is the honest record —
        the job did not finish and nothing will resume it. The BRANCH survives,
        with whatever the job had done to it, which is the point of working on
        one.
        """
        runs = list(store.get(DATA_RUNS, {}).values())
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "code ready: %d repositor%s, %d writable, checks %s",
        len(cfg.repositories),
        "y" if len(cfg.repositories) == 1 else "ies",
        sum(1 for r in cfg.repositories.values() if r.writable),
        "sandboxed" if cfg.sandbox else "not sandboxed",
    )
    return True


def _register_services(jarvis: "Jarvis") -> None:
    async def handle_run(call: ServiceCall) -> dict[str, Any]:
        task = await async_start(
            jarvis,
            str(call.get("repo") or call.get("repository") or ""),
            str(call.get("instruction") or call.get("task") or ""),
            source=str(call.get("source") or "service"),
        )
        if isinstance(task, str):
            return {"status": "error", "error": task}
        return {"status": "started", "task_id": task.id, "title": task.title}

    async def handle_repos(_call: ServiceCall) -> dict[str, Any]:
        cfg = get_config(jarvis) or CodeConfig()
        return {"status": "ok", "repositories": cfg.listing()}

    async def handle_result(call: ServiceCall) -> dict[str, Any]:
        found = result_payload(jarvis, str(call.get("task_id") or ""))
        if found is None:
            return {"status": "error", "error": "no such coding job"}
        return {"status": "ok", **found}

    jarvis.services.register(
        DOMAIN,
        "run",
        handle_run,
        supports_response=True,
        description=(
            "Start a coding job in one of the configured repositories. Runs in "
            "the background on a branch of its own and reports through the task "
            "list."
        ),
        fields={
            "repo": {"description": "Which repository, by name.", "required": True},
            "instruction": {"description": "What to change.", "required": True},
            "source": {"description": "Who asked, for the task's record."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        "repositories",
        handle_repos,
        supports_response=True,
        description="List the repositories Jarvis may work in.",
    )
    jarvis.services.register(
        DOMAIN,
        "result",
        handle_result,
        supports_response=True,
        description="The branch, diff and checks from a finished coding job.",
        fields={"task_id": {"description": "The job's task id.", "required": True}},
    )


def _register_tools(jarvis: "Jarvis") -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("code: no LLM tool registry; the services still work")
        return

    from ...llm.tools import TIER_APPROVAL, TIER_DIRECT, schema_object

    async def tool_start_coding_job(args: dict[str, Any], context: Any = None) -> Any:
        task = await async_start(
            jarvis,
            str(args.get("repo") or ""),
            str(args.get("instruction") or ""),
            source="conversation",
        )
        if isinstance(task, str):
            return {"status": "error", "error": task}
        return {
            "status": "started",
            "task_id": task.id,
            "message": (
                "The coding job has started and is running now. Tell the user it "
                "is under way, that it works on a branch of its own and will not "
                "touch their working branch, and that its progress is on the "
                "Code page. Do not invent a diff — there is none yet."
            ),
        }

    async def tool_repos(_args: dict[str, Any], context: Any = None) -> Any:
        cfg = get_config(jarvis) or CodeConfig()
        if not cfg.repositories:
            return {
                "status": "ok",
                "repositories": [],
                "message": (
                    "No repositories are configured, so there is nothing to code "
                    "in. They are added under `code:` in configuration.yaml."
                ),
            }
        return {"status": "ok", "repositories": cfg.listing()}

    registry.register(
        name="list_code_repositories",
        description=(
            "List the code repositories Jarvis may work in, with what each one "
            "is and whether it may be changed. Call this before starting a "
            "coding job so you use a name that exists."
        ),
        parameters=schema_object({}, []),
        handler=tool_repos,
        tier=TIER_DIRECT,
    )
    registry.register(
        name="start_coding_job",
        description=(
            "Start a coding job: read a repository, make a change, run its "
            "checks, and leave the result on a branch. Returns a task id "
            "immediately, NOT a diff — it takes minutes. Say what to change in "
            "full; the job cannot ask you follow-up questions."
        ),
        parameters=schema_object(
            {
                "repo": {
                    "type": "string",
                    "description": "the repository's name, from list_code_repositories",
                },
                "instruction": {
                    "type": "string",
                    "description": "what to change, in full and in one message",
                },
            },
            ["repo", "instruction"],
        ),
        handler=tool_start_coding_job,
        tier=TIER_APPROVAL,
    )


# ---------------------------------------------------------------------------
# starting a job
# ---------------------------------------------------------------------------
async def async_start(
    jarvis: "Jarvis", repo_name: str, instruction: str, *, source: str = ""
) -> "Task | str":
    """Record the task, start the worker, return at once.

    Returns the `Task`, or a sentence saying why not. A string rather than
    `None` because "which repository?" and "no repositories are configured" are
    different problems with different fixes, and the caller relays it verbatim.
    """
    cfg = get_config(jarvis)
    if cfg is None:
        return "the code integration is not set up on this server"
    name = str(repo_name or "").strip()
    instruction = " ".join(str(instruction or "").split())[:MAX_INSTRUCTION_CHARS]
    if not instruction:
        return "I need to know what to change."
    if not cfg.repositories:
        return (
            "No repositories are configured. They are added under `code:` in "
            "configuration.yaml."
        )
    repo = cfg.repositories.get(name)
    if repo is None:
        return (
            f"There is no repository called {name!r}. There is: "
            f"{', '.join(cfg.repositories)}."
        )

    registry = getattr(jarvis, "tasks", None)
    if registry is None:  # pragma: no cover - core always builds one
        _LOGGER.error("code: no task registry; refusing to run untracked work")
        return "this server has no task registry, so nothing could report progress"

    task = await registry.async_add(
        f"{repo.name}: {instruction}",
        kind=KIND,
        # One known step. Open-ended until the plan comes back and says how
        # many there are — a percentage before then would be invented.
        steps=["plan the work"],
        open_ended=True,
        source=source,
        detail="planning",
    )
    runs = _store(jarvis).setdefault(DATA_RUNS, {})
    run = asyncio.ensure_future(_drive(jarvis, cfg, repo, task.id, instruction))
    runs[task.id] = run
    run.add_done_callback(lambda _f, tid=task.id: runs.pop(tid, None))
    return task


# ---------------------------------------------------------------------------
# the worker
# ---------------------------------------------------------------------------
async def _drive(
    jarvis: "Jarvis", cfg: CodeConfig, repo: Repo, task_id: str, instruction: str
) -> None:
    registry = jarvis.tasks
    agent = CodeAgent(
        jarvis,
        repo,
        model=cfg.model,
        max_rounds=cfg.max_rounds,
        max_seconds=cfg.max_seconds,
        workspace=Workspace(repo, sandbox=cfg.sandbox),
    )
    try:
        await registry.async_update(task_id, status=STATUS_RUNNING)
        run = await agent.execute(instruction, task_id)
        _keep(jarvis, task_id, run)
        if run.stopped_early:
            # Over, but not finished. `error` rather than `done` on purpose:
            # `done` closes every step in the registry, so the bar would read
            # 100% above a result line saying it stopped at round forty. This
            # way the bar keeps the ground it actually covered, which is the
            # same rule `research` follows for a step that failed.
            await registry.async_update(
                task_id,
                status=STATUS_ERROR,
                detail=run.branch,
                result=one_line_result(run),
                error=run.summary,
            )
        else:
            await registry.async_update(
                task_id,
                status=STATUS_DONE,
                detail=run.branch,
                result=one_line_result(run),
            )
    except Stopped:
        _LOGGER.info("code job %s stopped at the user's request", task_id)
        _keep(jarvis, task_id, agent.run)
        await _tidy(agent)
    except asyncio.CancelledError:
        # Shutdown. Left `running` deliberately, as research does: the honest
        # record of work that did not finish and that nothing will resume.
        raise
    except (GitError, PathRefused) as err:
        _keep(jarvis, task_id, agent.run)
        await registry.async_update(task_id, status=STATUS_ERROR, error=str(err)[:400])
    except Exception as err:  # noqa: BLE001 - a worker must never take the loop down
        _LOGGER.exception("code job %s failed", task_id)
        _keep(jarvis, task_id, agent.run)
        await registry.async_update(
            task_id, status=STATUS_ERROR, error=f"{type(err).__name__}: {err}"[:400]
        )


async def _tidy(agent: CodeAgent) -> None:
    """After a cancel, leave the branch but say what is on it.

    Deliberately NOT `discard()`. Somebody who cancelled a job half way through
    usually wants to see how far it got — and if they do not, `git checkout
    <their branch>` is one command they already know. Throwing away work
    without being asked is the one thing that cannot be undone.
    """
    try:
        patch, stat = await agent.ws.diff()
        agent.run.diff = patch
        agent.run.diff_stat = stat
    except GitError:  # pragma: no cover - best effort on the way out
        pass


def one_line_result(run: CodeRun) -> str:
    """What the task list shows without opening anything."""
    parts: list[str] = []
    if run.branch:
        parts.append(run.branch)
    if run.files_changed:
        count = len(run.files_changed)
        parts.append(f"{count} file{'' if count == 1 else 's'} changed")
    elif run.diff_stat:
        parts.append(run.diff_stat.splitlines()[-1].strip())
    else:
        parts.append("no changes")
    failed = [c for c in run.checks if not c.get("ok")]
    if run.checks:
        parts.append(
            f"{len(run.checks) - len(failed)}/{len(run.checks)} checks passed"
        )
    if run.summary:
        parts.append(run.summary.split("\n")[0][:200])
    return " · ".join(parts)[:500]


def _keep(jarvis: "Jarvis", task_id: str, run: CodeRun) -> None:
    results: dict[str, CodeRun] = _store(jarvis).setdefault(DATA_RESULTS, {})
    results[task_id] = run
    while len(results) > MAX_KEPT:
        results.pop(next(iter(results)))


def result_payload(jarvis: "Jarvis", task_id: str) -> dict[str, Any] | None:
    """Everything one job produced, for the console's detail view."""
    results = _store(jarvis).get(DATA_RESULTS) or {}
    run = results.get(str(task_id or ""))
    if run is None:
        return None
    payload = run.as_dict()
    payload["diff"] = run.diff
    payload["trail"] = [
        {"tool": tool, "args": args, "outcome": outcome}
        for tool, args, outcome in run.trail
    ]
    return payload


def listing_payload(jarvis: "Jarvis") -> dict[str, Any]:
    """What the console's Code page needs to draw itself."""
    cfg = get_config(jarvis) or CodeConfig()
    registry = getattr(jarvis, "tasks", None)
    jobs = registry.listing(kind=KIND) if registry is not None else []
    return {
        "repositories": cfg.listing(),
        "jobs": jobs,
        "sandboxed": bool(cfg.sandbox),
    }


__all__ = [
    "CodeConfig",
    "DOMAIN",
    "KIND",
    "async_setup",
    "async_start",
    "get_config",
    "listing_payload",
    "one_line_result",
    "result_payload",
]