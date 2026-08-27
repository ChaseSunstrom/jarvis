"""The loop: plan, read, change, check, report.

This is Jarvis Code. It is shaped like the coding agents that work, and the
shape is not incidental — each part of it exists because the version without it
fails in a specific, boring way.

    plan      one model call -> a checklist of steps
    work      a bounded loop of tool calls: list, read, search, edit, write
    check     the repo's OWN commands (`pytest -q`), never a shell
    report    a diff, a summary, and a branch somebody can look at

## Why a plan step

Two reasons, and only one of them is about quality. A model that writes down
what it is going to do before touching anything makes better changes — but more
importantly, the plan **becomes the task's steps**, so the progress bar on the
console and the phone is a fraction of the model's own stated work rather than
a spinner. A bar that means something has to come from somewhere, and this is
where.

## Why it never gets a shell

`execute_command` exists in this house and is Tier 3, approval-gated, and runs
in a network-less sandbox. A coding loop that could call it would be asking for
approval fifty times a job, and a loop that could run commands *directly* would
be the largest hole anybody has ever put in this codebase.

So a job runs exactly the commands the repo's own configuration lists under
`checks:` — written by the operator, in a file, before the job existed. The
model chooses **whether** to run a check, never **what** it is. That is the
difference between "run the tests" and "run anything".

## Why it never commits to your branch

It makes `jarvis/<date>-<job>`, works there, and stops. The change reaches your
branch when a person merges it, with the tools they already have. An agent that
committed to `main` would be one nobody could leave running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...tasks import STATUS_DONE, STATUS_RUNNING
from .edits import EditError, apply_edit, numbered, search_text
from .workspace import GitError, PathRefused, Repo, Workspace, branch_name, check_argv

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

__all__ = ["CodeAgent", "CodeRun", "MAX_ROUNDS", "parse_plan"]

#: How many tool rounds one job may take. Reached, the job stops and reports
#: what it has — a half-finished branch with a diff beats a loop that ran all
#: night and produced the same thing.
MAX_ROUNDS = 40
#: And a wall clock, because a model that keeps reading the same file is not
#: making rounds quickly.
MAX_SECONDS = 20 * 60
MODEL_TIMEOUT = 240.0
MAX_STEPS = 12
CHECK_TIMEOUT = 300.0
#: Of one tool result. A model that reads a 5,000-line file has spent its whole
#: context on one tool call.
MAX_RESULT_CHARS = 12_000

#: How many times a job may be sent back to fix a failing check before it stops.
#:
#: The verify-until-green loop is the difference between an agent that writes
#: plausible code and one that leaves the tests passing, and the bound is the
#: difference between that and an agent that spends twenty minutes rewriting the
#: same function. Three is what it takes to fix a typo, notice the next error,
#: and fix that.
MAX_VERIFY_ROUNDS = 3

#: What a job may do without asking, per mode.
#:
#: The operator chooses this, never the model — the same argument as
#: `default_environment` in `sandbox.py`: a model that could read the list of
#: modes could hand itself the permissive one.
#:
#:   ask             every edit and every command is shown first. The mode for
#:                   a repository nobody wants surprised.
#:   accept-edits    edits land; anything that RUNS still asks. Reviewing a
#:                   diff at the end is the same review, done once.
#:   auto-run-tests  edits land and the repository's own declared checks run;
#:                   any other command asks. THE DEFAULT.
#:   full-auto       nothing asks except the destructive commands below, which
#:                   ask unless this task's `allow:` names them.
#:
#: `auto-run-tests` and not `ask`, deliberately. A job's edits land on a branch
#: nobody merges without looking, inside a container whose only host path is the
#: repository, and the review a person actually does is the diff at the end —
#: so asking forty times to write forty lines buys nothing and makes the feature
#: unusable from a room with no screen in it. What `ask` is for is a repository
#: where even the branch matters, and it is one line of config away.
MODES = ("ask", "accept-edits", "auto-run-tests", "full-auto")
DEFAULT_MODE = "auto-run-tests"

#: Commands that always ask, in every mode, unless the task explicitly allowed
#: them. Matched on the whole command line, so `rm -rf` inside a longer pipeline
#: is caught too.
#:
#: They run in a container with no network and one host path, so the blast
#: radius is the repository — which is somebody's work, and the reason this
#: exists. The list is deliberately about ACTIONS rather than about programs: a
#: `curl … | sh` is here because of the pipe, not because of curl.
DESTRUCTIVE = (
    r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*r",   # rm -r / rm -rf / rm -fr
    r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*[dfx])",
    r"\bgit\s+checkout\s+--\s",
    r"\b(mkfs|fdisk|dd)\b",
    r"\bchown\b|\bchmod\s+(-R|[0-7]{3,4}\s+/)",
    r"\b(shutdown|reboot|halt|poweroff|systemctl|service)\b",
    r"\bsudo\b|\bsu\s",
    r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh",     # fetch-and-run
    r">\s*/(dev|etc|usr|bin|boot)/",
    r"\btruncate\b|\bshred\b",
    r"\bdocker\b|\bkubectl\b",
)
_DESTRUCTIVE = tuple(re.compile(pattern) for pattern in DESTRUCTIVE)


def is_destructive(command: str) -> bool:
    """Would this command do something a person would want to be asked about?

    Conservative on purpose: a false positive costs one approval prompt, and a
    false negative costs somebody's working tree.
    """
    text = " ".join(str(command or "").split())
    return any(pattern.search(text) for pattern in _DESTRUCTIVE)


def normalise_mode(value: Any) -> str:
    """A mode string, or the safe one. Never raises — this is a config value."""
    text = str(value or "").strip().lower().replace("_", "-")
    return text if text in MODES else DEFAULT_MODE


@dataclass
class CodeRun:
    """What one job did, for the report and for the console."""

    repo: str
    instruction: str
    branch: str = ""
    plan: list[str] = field(default_factory=list)
    #: `(tool, argument summary, outcome)` per round, for the record.
    trail: list[tuple[str, str, str]] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    diff_stat: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)
    #: Every `run_command` this job ran, so the console can show the build as
    #: well as the diff. A job that installed six packages and then failed is
    #: only explicable with this.
    commands: list[dict[str, Any]] = field(default_factory=list)
    #: `{"sha": …, "message": …, "files": n}` per commit on the job branch.
    #: A job's output is a branch, and a branch with one enormous commit is a
    #: branch nobody can review.
    commits: list[dict[str, Any]] = field(default_factory=list)
    #: Every action a human was asked about: `{"kind", "summary", "approved"}`.
    #: In the record because "why did it not do the thing" is answered by this
    #: and by nothing else.
    approvals: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    rounds: int = 0
    #: True when the repository's own check passed at the end of the job. The
    #: distinction that matters in a report: "it finished" and "it works" are
    #: different claims, and only one of them was measured.
    verified: bool = False
    #: True when the loop hit `max_rounds` or `max_seconds` rather than the
    #: model saying it was finished. The job is over either way, but "over"
    #: and "did what it was asked" are different things and the task says so.
    stopped_early: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "instruction": self.instruction,
            "branch": self.branch,
            "plan": list(self.plan),
            "files_changed": list(self.files_changed),
            "diff_stat": self.diff_stat,
            "checks": list(self.checks),
            "commands": list(self.commands),
            "commits": list(self.commits),
            "approvals": list(self.approvals),
            "verified": self.verified,
            "summary": self.summary,
            "rounds": self.rounds,
            "stopped_early": self.stopped_early,
        }


PLAN_PROMPT = """You are about to change a code repository. Before touching \
anything, say what you are going to do.

REPOSITORY: {repo}{description}
WHAT IS THERE:
{tree}

THE JOB: {instruction}

Reply with ONLY a JSON array of at most {limit} short steps, in order, like
["read src/app.py to find the handler", "add the missing null check", \
"run the tests"]. No prose."""


SYSTEM_PROMPT = """You are Jarvis, changing one code repository on the user's \
own machine. You work on a branch of your own; nothing you do reaches their \
work until they merge it.

Your plan for this job:
{plan}

Rules that are not negotiable:
- READ a file before you change it. An edit against text you have not seen is \
a guess.
- `edit_file` replaces text that appears EXACTLY ONCE. If it appears twice, \
include more surrounding lines rather than guessing which one.
- Change the least you can. Do not reformat, do not rename things you were not \
asked to rename, do not "tidy" code you are passing through.
{shell}
- Call `plan_step` as you move through the plan above. Somebody is watching \
a progress bar drawn from it, and a bar that never moves is worse than no bar.
- When the job is done, say so plainly in one or two sentences and stop \
calling tools. If you could not do it, say that instead — an honest "I could \
not find where this is handled" is worth more than a change that compiles and \
is wrong."""


def parse_plan(raw: str, *, limit: int = MAX_STEPS) -> list[str]:
    """Read the planner's answer, however it formatted it.

    Same problem and the same ladder as the research planner: asked for JSON,
    models return prose, fenced code, or a numbered list. The fallback is a
    single generic step rather than nothing, because a job with no plan still
    has to run — the plan is how the progress bar is drawn, not a gate.
    """
    text = str(raw or "").strip()
    found: list[str] = []
    block = re.search(r"\[.*?\]", text, re.DOTALL)
    if block:
        try:
            parsed = json.loads(block.group(0))
            if isinstance(parsed, list):
                found = [str(x) for x in parsed if isinstance(x, (str, int, float))]
        except ValueError:
            found = []
    if not found:
        for line in text.splitlines():
            stripped = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line)
            if stripped == line:
                continue  # unmarked prose, not a step
            stripped = stripped.strip().strip('",').strip()
            if len(stripped) > 2 and not stripped.endswith(":"):
                found.append(stripped)

    out: list[str] = []
    for item in found:
        step = " ".join(str(item).split())[:160]
        if step and step not in out:
            out.append(step)
        if len(out) >= limit:
            break
    return out or ["make the change"]


class Stopped(Exception):
    """The job was cancelled from a client. Not an error."""


def _summarise_args(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments small enough to put on a wire, with file contents left out.

    A `write_file` call carries the whole file; a status line wants the path.
    """
    out: dict[str, Any] = {}
    for key, value in list(args.items())[:6]:
        if key in ("content", "text", "body", "replacement"):
            out[key] = f"<{len(str(value))} chars>"
        else:
            out[key] = str(value)[:120]
    return out


class CodeAgent:
    """One job, from instruction to branch."""

    def __init__(
        self,
        jarvis: "Jarvis",
        repo: Repo,
        *,
        model: str = "",
        max_rounds: int = MAX_ROUNDS,
        max_seconds: float = MAX_SECONDS,
        workspace: Workspace | None = None,
        mode: str = DEFAULT_MODE,
        allow: Sequence[str] = (),
        approvals: Any = None,
    ) -> None:
        self.jarvis = jarvis
        self.repo = repo
        self.model = model
        self.max_rounds = max(1, int(max_rounds))
        self.max_seconds = float(max_seconds)
        self.ws = workspace or Workspace(repo)
        self.run = CodeRun(repo=repo.name, instruction="")
        self._task_id = ""
        self._started = 0.0
        #: 1-based, the furthest plan step the model has claimed.
        self._plan_at = 0
        #: The environment's `setup:` runs once per job, not per command.
        self._setup_done = False
        #: What may happen without asking. The operator's choice, per task.
        self.mode = normalise_mode(mode)
        #: Commands this task may run even though they look destructive. Exact
        #: whole-string matches, normalised for whitespace — not prefixes, for
        #: the reason `_do_check` gives about `pytest -q; curl evil`.
        self.allow = {" ".join(str(item).split()) for item in allow if str(item).strip()}
        #: Where a held action waits. None means nothing can be held, which is
        #: only true in unit tests; `_gate` treats that as "ask nobody, refuse".
        self.approvals = approvals

    # --- the whole job ----------------------------------------------------
    async def execute(self, instruction: str, task_id: str = "") -> CodeRun:
        self.run = CodeRun(repo=self.repo.name, instruction=instruction)
        self._task_id = task_id
        self._started = time.monotonic()
        self._plan_at = 0
        self._setup_done = False

        # BEFORE `is_repo`, which itself runs git: a poisoned repository would
        # otherwise be reported as "not a git repository", which sends the
        # operator to look for the wrong thing entirely.
        unsafe = await self.ws.async_unsafe_git_config()
        if unsafe:
            raise GitError(unsafe)
        if not await self.ws.is_repo():
            raise GitError(f"{self.repo.name} is not a git repository")
        if await self.ws.is_dirty():
            raise GitError(
                f"{self.repo.name} has uncommitted changes. Jarvis will not work "
                "on top of them — commit or stash first."
            )

        await self._planning(STATUS_RUNNING)
        tree = await self._tree()
        planned = await self._ask(
            PLAN_PROMPT.format(
                repo=self.repo.name,
                description=f" — {self.repo.description}" if self.repo.description else "",
                tree=tree,
                instruction=instruction,
                limit=MAX_STEPS,
            )
        )
        self.run.plan = parse_plan(planned)
        await self._plan_into_task()

        self.run.branch = await self.ws.start_branch(
            branch_name(self._task_id or "job")
        )
        try:
            await self._work(instruction)
            await self._verify_until_green(instruction)
            await self._commit_work()
            await self._finish()
        finally:
            # However the job ends. A container left running holds the
            # repository mounted and, with `persist`, never commits what it
            # installed — so the next job reinstalls it.
            await self.ws.close_session()
        return self.run

    def _shell_rule(self) -> str:
        """What this repository lets the model run, in its own words.

        Two genuinely different machines, so two different sentences. Telling a
        sandboxed job it has no shell would waste it; telling an unsandboxed
        one it has a container would have it call a tool that is not there.
        """
        if not self.ws.sandboxed:
            if self.ws.unconfined_check_refusal() or not self.repo.checks:
                return (
                    "- You have no shell and nothing to run. Read, reason and "
                    "edit; you cannot execute anything here."
                )
            return (
                "- You have no shell. `run_check` runs the checks this "
                "repository declares, and nothing else exists."
            )
        environment = self.ws.environment
        reach = (
            "It can reach the network, so install whatever you need."
            if getattr(environment, "networked", False)
            else "It has NO network, so work with what is already there — "
            "an install will fail."
        )
        return (
            f"- `run_command` runs any shell command in a throwaway container "
            f"({getattr(environment, 'image', '?')}) whose only visible "
            f"directory is this repository. {reach} Nothing you do in there "
            "touches the rest of the machine and nothing survives the job, so "
            "do not be timid: build it, run it, and read the errors."
        )

    async def _tree(self) -> str:
        try:
            files = self.ws.listing("", depth=2)
        except (FileNotFoundError, PathRefused):
            return "(could not read the repository)"
        return "\n".join(f"{'  ' if not f.is_dir else ''}{f.path}" for f in files[:120])

    # --- the loop ---------------------------------------------------------
    async def _work(self, instruction: str) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                plan="\n".join(f"  {i + 1}. {s}" for i, s in enumerate(self.run.plan)),
                shell=self._shell_rule(),
            )},
            {"role": "user", "content": instruction},
        ]
        tools = self._tool_schemas()

        for round_number in range(self.max_rounds):
            # Yield first, or the check below can only see a cancel that landed
            # while something else suspended. A real model always suspends; a
            # fake one and a cached read never did, and on Python 3.12 the loop
            # ran all twenty calls before the client's cancel was ever run
            # (test_cancelling_a_job_actually_stops_it). The guarantee must not
            # depend on what the last call happened to await.
            await asyncio.sleep(0)
            self._check_stopped()
            if time.monotonic() - self._started > self.max_seconds:
                self.run.stopped_early = True
                self.run.summary = (
                    f"stopped after {self.max_seconds / 60:.0f} minutes; what was "
                    "done so far is on the branch"
                )
                return
            self.run.rounds = round_number + 1

            result = await self._chat(messages, tools)
            calls = getattr(result, "tool_calls", None) or []
            if not calls:
                self.run.summary = (result.content or "").strip()[:2000]
                return

            messages.append(self._assistant_message(result))
            for call in calls:
                await asyncio.sleep(0)
                self._check_stopped()
                # Per CALL, not only per round: ten `sleep 890`s in one
                # assistant message is ten times the environment's timeout
                # inside a single round, and the round-level check never runs.
                if time.monotonic() - self._started > self.max_seconds:
                    self.run.stopped_early = True
                    self.run.summary = (
                        f"stopped after {self.max_seconds / 60:.0f} minutes; what "
                        "was done so far is on the branch"
                    )
                    return
                name, args = _call_parts(call)
                outcome = await self._dispatch(name, args)
                self.run.trail.append((name, _summarise(args), outcome[:120]))
                messages.append(self._tool_message(call, outcome))

        self.run.stopped_early = True
        self.run.summary = (
            f"stopped after {self.max_rounds} rounds; what was done so far is on "
            "the branch"
        )

    # --- what needs asking ------------------------------------------------
    def needs_approval(self, kind: str, command: str = "") -> bool:
        """Whether this action is shown to a human before it happens.

        `kind` is `edit` (a change to a file), `check` (one of the repository's
        own declared commands) or `command` (anything the model wrote).
        """
        if kind == "command" and is_destructive(command):
            # Every mode, including full-auto. The only way past is the task's
            # own allow-list, which a person wrote before the job started.
            return " ".join(str(command).split()) not in self.allow
        if self.mode == "full-auto":
            return False
        if self.mode == "auto-run-tests":
            return kind == "command"
        if self.mode == "accept-edits":
            return kind != "edit"
        return True  # ask

    async def _gate(self, kind: str, summary: str, detail: str = "") -> str:
        """Hold one action. Returns "" to proceed, or what to tell the model.

        The refusal is a sentence rather than an exception because the job
        continues either way: a denied command is a fact the model has to work
        around, exactly like a failing test.
        """
        if not self.needs_approval(kind, summary if kind == "command" else ""):
            return ""
        if self.approvals is None:
            # Nothing can hold it, so nothing may release it. Refusing is the
            # only answer that keeps "approval required" meaning anything.
            self.run.approvals.append({"kind": kind, "summary": summary, "approved": False})
            return (
                "This action needs approval and there is nowhere to ask, so it has "
                "NOT run. Do not retry it."
            )
        decision = await self.approvals.ask(
            task_id=self._task_id, kind=kind, summary=summary, detail=detail
        )
        self.run.approvals.append(
            {"kind": kind, "summary": summary, "approved": bool(decision.approved)}
        )
        return "" if decision.approved else decision.refusal

    def _registry(self) -> Any:
        """The task registry, when this job is attached to a task."""
        registry = getattr(self.jarvis, "tasks", None)
        return registry if registry is not None and self._task_id else None

    async def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Run one tool call, and say so while it is running.

        The trail used to be appended to `run.trail` and surfaced when the whole
        job finished — so a job that called nine tools over four minutes showed
        a spinner for four minutes and nine lines at the end. These are the same
        events a chat turn fires, on the task, so the console can watch a coding
        job the way it watches a turn.
        """
        registry = self._registry()
        started = time.monotonic()
        call_id = ""
        if registry is not None:
            call_id = registry.tool_started(
                self._task_id, name=name, arguments=_summarise_args(args)
            )
        ok, failure = True, ""
        try:
            result = await self._dispatch_one(name, args)
            # The tools report refusals as values rather than exceptions, which
            # is right for the model and wrong for a status line: "refused: …"
            # is not a call that worked.
            lowered = result[:60].lower()
            ok = not lowered.startswith(("refused:", "no:", "not found:", "there is no tool"))
            if not ok:
                failure = result[:200]
            return result
        except Exception as err:
            ok, failure = False, f"{type(err).__name__}: {err}"[:200]
            raise
        finally:
            if registry is not None:
                registry.tool_finished(
                    self._task_id,
                    name=name,
                    call_id=call_id,
                    ok=ok,
                    error=failure,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

    async def _dispatch_one(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "list_files":
                return self._do_list(args)
            if name == "read_file":
                return self._do_read(args)
            if name == "search":
                return self._do_search(args)
            if name in ("edit_file", "write_file"):
                # Not offered to a read-only repository — and refused here as
                # well. "The model cannot see the tool" is a property of the
                # schema list, and a server that emits a call for a tool that
                # was never in it is a real thing that happens: some proxies
                # merge tool sets, some models hallucinate a name they saw in
                # an earlier turn. Enforcing the rule only by omission means
                # the guarantee lives in a list rather than in the code that
                # writes to disk.
                if not self.repo.writable:
                    return (
                        f"{self.repo.name} is read-only. Say what you would "
                        "change and why; nothing here can write to it."
                    )
                if name == "edit_file":
                    return await self._do_edit(args)
                return await self._do_write(args)
            if name == "run_check":
                return await self._do_check(args)
            if name == "run_command":
                return await self._do_command(args)
            if name == "plan_step":
                return await self._do_plan_step(args)
            return f"there is no tool called {name!r}"
        except PathRefused as err:
            return f"refused: {err}"
        except (EditError, ValueError) as err:
            return f"no: {err}"
        except FileNotFoundError as err:
            return f"not found: {err}"
        except OSError as err:
            return f"could not do that: {err}"

    # --- the tools --------------------------------------------------------
    def _tool_schemas(self) -> list[dict[str, Any]]:
        from ...llm.tools import schema_object

        checks = ", ".join(self.repo.checks) or "none are configured"
        writable = self.repo.writable
        tools = [
            (
                "list_files",
                "List what is in a folder of the repository.",
                schema_object(
                    {"path": {"type": "string", "description": "folder, or omit for the top"}},
                    [],
                ),
            ),
            (
                "read_file",
                "Read a file. Always read before you change.",
                schema_object(
                    {
                        "path": {"type": "string", "description": "path inside the repo"},
                        "start": {"type": "integer", "description": "first line, 1-based"},
                        "lines": {"type": "integer", "description": "how many lines"},
                    },
                    ["path"],
                ),
            ),
            (
                "search",
                "Find a regular expression across the repository. Returns paths and line numbers.",
                schema_object(
                    {
                        "pattern": {"type": "string", "description": "a regular expression"},
                        "path": {"type": "string", "description": "restrict to this folder"},
                    },
                    ["pattern"],
                ),
            ),
            (
                "plan_step",
                "Say which step of your plan you are on now. Everything before it "
                "is marked done. This is what moves the progress bar the user is "
                "watching, so call it as you go.",
                schema_object(
                    {
                        "step": {
                            "type": "integer",
                            "description": "the step number from the plan, 1-based",
                        },
                        "note": {"type": "string", "description": "one short line"},
                    },
                    ["step"],
                ),
            ),
        ]
        if writable:
            tools.append(
                (
                    "edit_file",
                    "Replace text in a file. `old` must appear EXACTLY ONCE — include "
                    "surrounding lines to make it unique. This is how you change code.",
                    schema_object(
                        {
                            "path": {"type": "string"},
                            "old": {"type": "string", "description": "the text to replace, exactly"},
                            "new": {"type": "string", "description": "what it becomes"},
                        },
                        ["path", "old", "new"],
                    ),
                )
            )
            tools.append(
                (
                    "write_file",
                    "Create a NEW file, or replace a whole one. Prefer edit_file for a "
                    "file that already exists: rewriting one loses the parts you did "
                    "not think about.",
                    schema_object(
                        {"path": {"type": "string"}, "content": {"type": "string"}},
                        ["path", "content"],
                    ),
                )
            )
        if self.ws.sandboxed:
            tools.append(
                (
                    "run_command",
                    "Run any shell command in this repository's sandboxed "
                    "environment: install dependencies, build, run tests, "
                    "whatever the job needs. It runs in a throwaway container "
                    "whose only visible directory is this repository, so "
                    "nothing you do here can touch the rest of the machine and "
                    "nothing survives the job. Prefer this over guessing "
                    "whether a dependency is present — just install it.",
                    schema_object(
                        {
                            "command": {
                                "type": "string",
                                "description": "a shell command, e.g. `pip install -e . && pytest -q`",
                            }
                        },
                        ["command"],
                    ),
                )
            )
        if self.repo.checks and not self.ws.unconfined_check_refusal():
            tools.append(
                (
                    "run_check",
                    f"Run one of this repository's own checks: {checks}. You cannot "
                    "run anything else.",
                    schema_object(
                        {"command": {"type": "string", "description": f"one of: {checks}"}},
                        ["command"],
                    ),
                )
            )
        return [
            {
                "type": "function",
                "function": {"name": name, "description": description, "parameters": schema},
            }
            for name, description, schema in tools
        ]

    def _do_list(self, args: dict[str, Any]) -> str:
        entries = self.ws.listing(str(args.get("path") or ""), depth=1)
        if not entries:
            return "(empty)"
        return "\n".join(
            f"{'[dir] ' if e.is_dir else ''}{e.path}" for e in entries
        )[:MAX_RESULT_CHARS]

    def _do_read(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "")
        text = self.ws.read(path)
        start = max(1, int(args.get("start") or 1))
        count = int(args.get("lines") or 0)
        lines = text.split("\n")
        window = lines[start - 1 : (start - 1 + count) if count else None]
        # Numbered, because that is what lets the model name a place and lets
        # the reader check the edit landed where it said.
        body = numbered("\n".join(window), start=start)
        if len(body) > MAX_RESULT_CHARS:
            return (
                body[:MAX_RESULT_CHARS]
                + f"\n… cut at {MAX_RESULT_CHARS} characters; read a range with start/lines"
            )
        return body

    def _do_search(self, args: dict[str, Any]) -> str:
        pattern = str(args.get("pattern") or "")
        base = self.ws.resolve(str(args.get("path") or ""))
        hits: list[str] = []
        for file in self.ws.files_for_search():
            if base != self.ws.root and base not in file.parents and file != base:
                continue
            try:
                text = file.read_text("utf-8", errors="replace")
            except OSError:
                continue
            relative = str(file.relative_to(self.ws.root))
            for hit in search_text(relative, text, pattern, limit=5):
                hits.append(f"{hit.path}:{hit.line}: {hit.text.strip()}")
                if len(hits) >= 60:
                    return "\n".join(hits) + "\n… more matches; narrow the pattern"
        return "\n".join(hits) if hits else "(nothing matched)"

    async def _do_edit(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "")
        source = self.ws.read(path)
        result = apply_edit(source, str(args.get("old") or ""), str(args.get("new") or ""))
        # Held with the change itself, not with a summary of it: an approval
        # that says "edit app.py" asks somebody to trust a description, and the
        # whole point of a gate is that they do not have to.
        refusal = await self._gate(
            "edit",
            f"edit {path} at line {result.line}",
            detail=_preview(str(args.get("old") or ""), str(args.get("new") or "")),
        )
        if refusal:
            return refusal
        self.ws.write(path, result.text)
        if path not in self.run.files_changed:
            self.run.files_changed.append(path)
        return f"edited {path} at line {result.line} ({result.how} match)"

    async def _do_write(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        existed = False
        try:
            existed = self.ws.resolve(path).is_file()
        except PathRefused:
            raise
        refusal = await self._gate(
            "edit",
            f"{'replace' if existed else 'create'} {path}",
            detail=content[:4000],
        )
        if refusal:
            return refusal
        written = self.ws.write(path, content)
        if path not in self.run.files_changed:
            self.run.files_changed.append(path)
        return f"{'replaced' if existed else 'created'} {path} ({written} bytes)"

    async def _do_check(self, args: dict[str, Any]) -> str:
        """Run one of the repo's OWN checks. The model picks which, never what.

        Matched against the configured list as a whole string. Not "starts
        with", not "is a prefix of": `pytest -q` and `pytest -q; curl evil` do
        not differ by a prefix check, and a coding agent choosing its own
        command line is the thing this design exists to avoid.
        """
        wanted = " ".join(str(args.get("command") or "").split())
        allowed = {" ".join(c.split()): c for c in self.repo.checks}
        if wanted not in allowed:
            return (
                f"{wanted!r} is not one of this repository's checks. "
                f"They are: {', '.join(self.repo.checks) or 'none'}"
            )
        refusal = await self._gate("check", allowed[wanted])
        if refusal:
            return refusal
        # In the container when there is one. A check that ran on the host
        # while `run_command` ran in a container would be two different
        # machines with two different dependency sets, and the job would be
        # debugging the difference rather than the code.
        if self.ws.sandboxed:
            code, out = await self.ws.run_sandboxed(allowed[wanted])
        else:
            # Checked here as well as at `_tool_schemas`, because a model can
            # name a tool it was never offered — the recovery path in
            # `llm/toolcalls.py` exists precisely to turn narrated calls into
            # real ones, and it does not know which were withheld.
            refusal = self.ws.unconfined_check_refusal()
            if refusal:
                return refusal
            code, out = await self._spawn(
                self.ws.sandbox_argv(check_argv(allowed[wanted]))
            )
        registry = self._registry()
        if registry is not None and out:
            # The output of a check is the thing somebody actually watches, and
            # it used to arrive only in the finished job's record.
            registry.output(
                self._task_id, out[-4000:], stream="stdout" if code == 0 else "stderr"
            )
        record = {"command": wanted, "ok": code == 0, "output": out[-4000:]}
        self.run.checks.append(record)
        head = "passed" if code == 0 else f"failed (exit {code})"
        return f"{wanted}: {head}\n{out[-MAX_RESULT_CHARS:]}"

    async def _do_plan_step(self, args: dict[str, Any]) -> str:
        """The model says where it is; the bar follows.

        Only forwards. A model that re-announces step 2 after step 5 is not
        undoing step 5, and a progress bar that goes backwards reads as a
        fault rather than as a correction.
        """
        try:
            wanted = int(args.get("step") or 0)
        except (TypeError, ValueError):
            return "step must be a number from the plan"
        if not 1 <= wanted <= len(self.run.plan):
            return f"the plan has steps 1..{len(self.run.plan)}"
        if wanted <= self._plan_at:
            return f"already past step {wanted}"
        self._plan_at = wanted
        await self._advance(wanted, str(args.get("note") or ""))
        return f"noted: step {wanted} of {len(self.run.plan)}"

    async def _do_command(self, args: dict[str, Any]) -> str:
        """Run anything, inside the container and nowhere else.

        This is the one tool that takes a command the model wrote, and it is
        offered ONLY when the repository has an environment. The safety comes
        from where it runs, not from what it says: see `sandbox.py` for the
        fences, of which the load-bearing one is that the container's only host
        path is this repository.
        """
        from .sandbox import SandboxError

        command = str(args.get("command") or "").strip()
        if not command:
            return "run_command needs a command."
        refusal = await self._gate("command", command)
        if refusal:
            return refusal
        # The operator's `setup:` runs first, once per job. It was parsed and
        # then never used — an operator who wrote `apt-get install libglfw3-dev`
        # got a container without it and a build failure they could not explain.
        # Prepended rather than run in its own container, because `--rm` means
        # a separate container would throw the installation away.
        await self._run_setup_once()
        try:
            code, out = await self.ws.run_sandboxed(command)
        except SandboxError as err:
            return f"no: {err}"
        record = {"command": command, "ok": code == 0, "output": out[-4000:]}
        registry = self._registry()
        if registry is not None and out:
            registry.output(
                self._task_id, out[-4000:], stream="stdout" if code == 0 else "stderr"
            )
        self.run.commands.append(record)
        head = "ok" if code == 0 else f"exit {code}"
        body = out[-MAX_RESULT_CHARS:] or "(no output)"
        return f"{command}: {head}\n{body}"

    async def _run_setup_once(self) -> None:
        """The environment's `setup:` commands, before the first real one."""
        if self._setup_done:
            return
        self._setup_done = True
        if not self.ws.sandboxed:
            return
        session = await self.ws.open_session()
        outcome = await session.run_setup()
        if outcome is None:
            return
        code, out = outcome
        self.run.commands.append(
            {"command": "(environment setup)", "ok": code == 0, "output": out[-4000:]}
        )
        if code != 0:
            _LOGGER.warning(
                "code: setup for environment %s failed: %s",
                getattr(self.ws.environment, "name", "?"),
                out[-400:],
            )

    async def _spawn(self, argv: list[str]) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.ws.root),
                # Same reason as git: a check that prompts must fail, not wait.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, ValueError) as err:
            return 1, f"could not run it: {err}"
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()
            return 1, f"timed out after {CHECK_TIMEOUT:.0f}s"
        return proc.returncode or 0, out.decode("utf-8", "replace")

    # --- finishing --------------------------------------------------------
    # --- until the tests pass ---------------------------------------------
    async def _verify_until_green(self, instruction: str) -> None:
        """Run the repository's checks, and send failures back to be fixed.

        The difference between an agent that writes plausible code and one that
        leaves the tests passing. A model asked to "fix the failing test"
        genuinely believes it is finished the moment it has written something
        that looks right — it stops calling tools, and without this the job
        would report success over a red suite.

        Bounded three ways, because the failure mode on the other side is a
        model rewriting the same function until the clock runs out: at most
        `MAX_VERIFY_ROUNDS` attempts, the job's own wall clock, and the round
        budget it shares with `_work`.
        """
        if not self.repo.checks or not self.repo.writable:
            return
        # A writable repository with no container and no sandbox wrapper is not
        # offered `run_check` at all (`Workspace.unconfined_check_refusal` says
        # why: a check executes files out of the working tree the job has just
        # been editing). Looping three times to be refused three times would
        # turn that rule into a delay.
        if not self.ws.sandboxed and self.ws.unconfined_check_refusal():
            return
        # Deliberately NOT skipped when the job changed nothing.
        #
        # "It changed nothing, so there is nothing to verify" sounds right and
        # is the single most expensive assumption here: a model that decides
        # it is finished before it starts — "Very good, Sir", empty diff, task
        # `done` — is the commonest failure of a coding agent, and it looks
        # exactly like success. Running the repository's own check is what
        # turns that into a red result and one more attempt, which is the whole
        # point of the loop. A run that genuinely had nothing to do pays one
        # container start for the privilege of being sure.
        command = self.repo.checks[0]
        for attempt in range(MAX_VERIFY_ROUNDS):
            self._check_stopped()
            if time.monotonic() - self._started > self.max_seconds:
                return
            await self._do_check({"command": command})
            if self.run.checks and self.run.checks[-1]["ok"]:
                self.run.verified = True
                return
            if self.run.approvals and not self.run.approvals[-1]["approved"]:
                # The check itself was refused. Sending the model back to fix a
                # failure nobody let it see would be a loop with no information
                # in it.
                return
            if attempt == MAX_VERIFY_ROUNDS - 1:
                break
            failure = (self.run.checks[-1]["output"] if self.run.checks else "")[
                -MAX_RESULT_CHARS:
            ]
            await self._work(
                f"{instruction}\n\n{command} does not pass. Fix it.\n{failure}"
            )
        self.run.summary = (
            (self.run.summary or "") + " The check still fails; what was done is on the branch."
        ).strip()

    async def _commit_work(self) -> None:
        """One commit on the job branch, if anything changed.

        On the branch and never on the operator's: `start_branch` made
        `jarvis/…` before the first edit, and this is the only thing here that
        writes history. A job whose work is committed is one somebody can
        `git log`, cherry-pick and revert; a job that leaves a dirty working
        tree is one they have to reconstruct.
        """
        if not self.repo.writable:
            return
        patch, stat = await self.ws.diff()
        if not patch.strip():
            return
        subject = " ".join(self.run.instruction.split()).rstrip(".")
        subject = (subject[:68] + "…") if len(subject) > 69 else subject
        body = [subject or "Jarvis Code", ""]
        if self.run.plan:
            body += [f"- {step}" for step in self.run.plan[:MAX_STEPS]] + [""]
        if self.run.checks:
            last = self.run.checks[-1]
            body.append(f"Check: {last['command']} — {'passed' if last['ok'] else 'failed'}")
        try:
            sha = await self.ws.commit("\n".join(body))
        except GitError as err:
            _LOGGER.warning("could not commit the job's work: %s", err)
            return
        self.run.commits.append(
            {"sha": sha, "message": subject, "stat": stat, "files": len(self.run.files_changed)}
        )

    async def _finish(self) -> None:
        patch, stat = await self.ws.diff()
        self.run.diff = patch
        self.run.diff_stat = stat
        if not self.run.summary:
            self.run.summary = "finished" if patch else "nothing needed changing"

    # --- the model --------------------------------------------------------
    def _client(self) -> Any:
        agent = self.jarvis.data.get("llm")
        client = getattr(agent, "client", None)
        if client is None:
            raise RuntimeError("no model is configured, so there is nothing to code with")
        return client, agent

    async def _ask(self, prompt: str) -> str:
        from ...llm.agent import ThinkStripper

        client, agent = self._client()
        stream = client.chat(
            model=self.model or getattr(agent, "model", None) or None,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            think=False,
        )
        result = await asyncio.wait_for(stream, MODEL_TIMEOUT)
        stripper = ThinkStripper()
        return (stripper.feed(result.content or "") + stripper.flush()).strip()

    async def _chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        client, agent = self._client()
        stream = client.chat(
            model=self.model or getattr(agent, "model", None) or None,
            messages=messages,
            tools=tools,
            stream=False,
            think=False,
        )
        return await asyncio.wait_for(stream, MODEL_TIMEOUT)

    def _assistant_message(self, result: Any) -> dict[str, Any]:
        client, _ = self._client()
        builder = getattr(client, "assistant_message", None)
        if builder is not None:
            return builder(result)
        return {"role": "assistant", "content": getattr(result, "content", "") or ""}

    def _tool_message(self, call: Any, content: str) -> dict[str, Any]:
        client, _ = self._client()
        builder = getattr(client, "tool_message", None)
        if builder is not None:
            return builder(call, content[:MAX_RESULT_CHARS])
        return {"role": "tool", "content": content[:MAX_RESULT_CHARS]}

    # --- the task -----------------------------------------------------------
    def _check_stopped(self) -> None:
        """Stop if this job was cancelled. The registry owns the question."""
        if not self._task_id:
            return
        registry = getattr(self.jarvis, "tasks", None)
        if registry is None:
            return
        task = registry.get(self._task_id)
        if task is None or task.finished:
            raise Stopped(self._task_id)

    async def _plan_into_task(self) -> None:
        """The model's own plan becomes the task's steps.

        This is what makes the progress bar mean something: the denominator is
        the work the model said it would do, not a number this file invented.
        """
        registry = getattr(self.jarvis, "tasks", None)
        if registry is None or not self._task_id:
            return
        await registry.async_update(
            self._task_id,
            step=0,
            step_status=STATUS_DONE,
            step_detail=f"{len(self.run.plan)} step(s)",
            add_steps=[*self.run.plan, "write it up"],
            open_ended=False,
            detail="working",
        )

    async def _planning(self, status: str) -> None:
        """Step 0 — the one step a job has before it has a plan."""
        registry = getattr(self.jarvis, "tasks", None)
        if registry is None or not self._task_id:
            return
        await registry.async_update(self._task_id, step=0, step_status=status)

    async def _advance(self, plan_step: int, note: str) -> None:
        """Close every plan step before this one and light this one up.

        The task's step list is `[plan the work, *the plan, write it up]`, so a
        1-based plan step is a task step of the same index — index 0 being the
        planning that produced it.
        """
        registry = getattr(self.jarvis, "tasks", None)
        if registry is None or not self._task_id:
            return
        for earlier in range(1, plan_step):
            await registry.async_update(
                self._task_id, step=earlier, step_status=STATUS_DONE
            )
        await registry.async_update(
            self._task_id,
            step=plan_step,
            step_status=STATUS_RUNNING,
            step_detail=" ".join(note.split())[:200],
            detail=self.run.plan[plan_step - 1] if self.run.plan else "",
        )


def _preview(old: str, new: str, width: int = 2000) -> str:
    """The change, as the two halves a person actually compares."""
    return f"- {old[:width]}\n+ {new[:width]}"


def _call_parts(call: Any) -> tuple[str, dict[str, Any]]:
    name = str(getattr(call, "name", "") or "")
    args = getattr(call, "arguments", None)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return name, args if isinstance(args, dict) else {}


def _summarise(args: dict[str, Any]) -> str:
    """One line of arguments for the trail, without a whole file in it."""
    parts = []
    for key in ("path", "pattern", "command"):
        if args.get(key):
            parts.append(f"{key}={str(args[key])[:60]}")
    return " ".join(parts) or "—"
