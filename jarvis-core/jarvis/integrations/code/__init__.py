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

from pathlib import Path

from ...services import ServiceCall
from ...tasks import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING
from .agent import CodeAgent, CodeRun, MAX_ROUNDS, Stopped
from .repos import RepoStore, check_name
from .forges import Forge, forge_from_dict, permits, split_project
from .sandbox import Environment, environment_from_dict
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
DATA_REPOS = "repos"

MAX_INSTRUCTION_CHARS = 2000
#: Finished runs kept in memory for the console to open. The task itself
#: outlives this; what expires is the diff, which is the large part.
MAX_KEPT = 20


@dataclass
class CodeConfig:
    repositories: dict[str, Repo] = field(default_factory=dict)
    #: Where Jarvis MAY create repositories. None means it may not.
    workspace: Path | None = None
    #: Named containers a job may run in. Empty means no job gets a shell.
    environments: dict[str, Environment] = field(default_factory=dict)
    #: GitHub/GitLab hosts, each with its own allow-list of repositories.
    forges: dict[str, Forge] = field(default_factory=dict)
    #: The environment a repository the MODEL creates gets. Empty means none,
    #: i.e. no shell — the safe default, and the operator's choice either way.
    default_environment: str = ""
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

        environments: dict[str, Environment] = {}
        for raw in data.get("environments") or []:
            built = environment_from_dict(raw)
            if built is None:
                continue
            if built.name in environments:
                _LOGGER.warning(
                    "code: two environments called %r; keeping the first", built.name
                )
                continue
            environments[built.name] = built

        # An `environment:` naming something that does not exist is a typo in
        # the setting that decides whether a job gets a shell. Refusing the
        # repository would be worse than dropping the reference — the job can
        # still read and edit — so it loses the shell and says so loudly.
        for repo in repos.values():
            if repo.environment and repo.environment not in environments:
                _LOGGER.warning(
                    "code: repository %s names environment %r, which is not "
                    "configured. It will run with no shell.",
                    repo.name,
                    repo.environment,
                )
                repo.environment = ""

        forges: dict[str, Forge] = {}
        for raw in data.get("forges") or []:
            built = forge_from_dict(raw)
            if built is None:
                continue
            if built.name in forges:
                _LOGGER.warning("code: two forges called %r; keeping the first", built.name)
                continue
            if not built.allow:
                # Not an error, and not a reason to drop it: an operator adding
                # a forge before deciding what to permit should see it listed
                # and refusing, rather than wonder why it vanished.
                _LOGGER.warning(
                    "code: forge %s permits no repositories, so nothing can be "
                    "cloned from it. Add paths under `allow:`.",
                    built.name,
                )
            forges[built.name] = built

        default_environment = str(data.get("default_environment") or "").strip()
        if default_environment and default_environment not in environments:
            _LOGGER.warning(
                "code: default_environment is %r, which is not configured. "
                "Repositories Jarvis creates will have no shell.",
                default_environment,
            )
            default_environment = ""

        raw_workspace = str(data.get("workspace") or "").strip()
        workspace = Path(raw_workspace).expanduser() if raw_workspace else None

        return cls(
            repositories=repos,
            workspace=workspace,
            environments=environments,
            forges=forges,
            default_environment=default_environment,
            model=str(data.get("model") or "").strip(),
            max_rounds=_int("max_rounds", MAX_ROUNDS, 4, 200),
            max_seconds=_int("max_minutes", 20, 1, 120) * 60.0,
            sandbox=sandbox,
        )

    def listing(self) -> list[dict[str, Any]]:
        rows = []
        for repo in self.repositories.values():
            entry = repo.as_dict()
            environment = self.environments.get(repo.environment)
            entry["environment_detail"] = environment.describe() if environment else ""
            entry["networked"] = bool(environment and environment.networked)
            rows.append(entry)
        return rows

    def environment_for(self, repo: Repo) -> Environment | None:
        return self.environments.get(repo.environment) if repo.environment else None


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
    from ...store import Store
    from .repos import STORE_KEY

    cfg = CodeConfig.from_config(config)
    store = _store(jarvis)
    store[DATA_CONFIG] = cfg
    store.setdefault(DATA_RUNS, {})
    store.setdefault(DATA_RESULTS, {})

    repos = RepoStore(Store(jarvis.config_dir, STORE_KEY), cfg.workspace)
    await repos.async_load()
    store[DATA_REPOS] = repos
    # Everything Jarvis made joins the same registry as everything declared, so
    # every surface below — the listing, the tool, the console — sees one kind
    # of repository. A second list would be a second thing to keep in step.
    for entry in repos.repos.values():
        if entry.name not in cfg.repositories:
            cfg.repositories[entry.name] = entry.as_repo()

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
        "code ready: %d repositor%s (%d writable), %d environment(s)%s, "
        "workspace %s",
        len(cfg.repositories),
        "y" if len(cfg.repositories) == 1 else "ies",
        sum(1 for r in cfg.repositories.values() if r.writable),
        len(cfg.environments),
        " — one or more can reach the network"
        if any(e.networked for e in cfg.environments.values())
        else "",
        cfg.workspace or "not set (Jarvis cannot create repositories)",
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
    async def handle_create(call: ServiceCall) -> dict[str, Any]:
        entry, why = await async_create_repository(
            jarvis,
            str(call.get("name") or ""),
            description=str(call.get("description") or ""),
            environment=str(call.get("environment") or ""),
        )
        if entry is None:
            return {"status": "error", "error": why}
        return {"status": "ok", "repository": entry.as_dict()}

    jarvis.services.register(
        DOMAIN,
        "create_repository",
        handle_create,
        supports_response=True,
        description=(
            "Create a new git repository inside the configured workspace. "
            "Refused unless `code: workspace:` is set."
        ),
        fields={
            "name": {"description": "Lowercase name; becomes a directory.", "required": True},
            "description": {"description": "What it is for."},
            "environment": {"description": "Which sandbox it builds in."},
        },
    )

    async def handle_clone(call: ServiceCall) -> dict[str, Any]:
        entry, why = await async_clone_repository(
            jarvis,
            str(call.get("forge") or ""),
            str(call.get("project") or call.get("repo") or ""),
            name=str(call.get("name") or ""),
            environment=str(call.get("environment") or ""),
        )
        if entry is None:
            return {"status": "error", "error": why}
        return {"status": "ok", "repository": entry.as_dict()}

    async def handle_push(call: ServiceCall) -> dict[str, Any]:
        ok, note = await async_push_branch(
            jarvis, str(call.get("repo") or ""), str(call.get("branch") or "")
        )
        return {"status": "ok" if ok else "error", "message": note}

    jarvis.services.register(
        DOMAIN,
        "clone_repository",
        handle_clone,
        supports_response=True,
        description=(
            "Clone a repository from a configured forge into the workspace. "
            "Only paths on that forge's allow-list may be cloned."
        ),
        fields={
            "forge": {"description": "Which forge, by name.", "required": True},
            "project": {"description": "owner/name", "required": True},
            "name": {"description": "Local name; defaults to the last segment."},
            "environment": {"description": "Which sandbox it builds in."},
        },
    )
    jarvis.services.register(
        DOMAIN,
        "push_branch",
        handle_push,
        supports_response=True,
        description=(
            "Push one `jarvis/…` branch back to the forge it was cloned from. "
            "Never main, never forced."
        ),
        fields={
            "repo": {"description": "Which repository.", "required": True},
            "branch": {"description": "The jarvis/… branch.", "required": True},
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

    from ...llm.tools import (
        TIER_APPROVAL,
        TIER_BACKGROUND,
        TIER_DIRECT,
        schema_object,
    )

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
    async def tool_create_repository(args: dict[str, Any], context: Any = None) -> Any:
        # `environment` is NOT taken from the model. It used to be, and that
        # let it hand itself the container of its choice: read the listing,
        # spot the one with `network: egress`, and create a repository of its
        # own using it — a networked shell in one Tier-2 call with no human
        # anywhere. The operator picks, through `default_environment` or by
        # naming one in `repositories:`; the console may still choose per
        # repository, because that request carried a bearer token.
        cfg = get_config(jarvis)
        entry, why = await async_create_repository(
            jarvis,
            str(args.get("name") or ""),
            description=str(args.get("description") or ""),
            environment=(cfg.default_environment if cfg else ""),
        )
        if entry is None:
            return {"status": "error", "error": why}
        return {
            "status": "ok",
            "repository": entry.name,
            "path": entry.path,
            "message": (
                f"Created {entry.name}. It is empty apart from a README, so the "
                "next step is a coding job to put something in it."
            ),
        }

    registry.register(
        name="create_repository",
        description=(
            "Create a new, empty git repository Jarvis can then work in. Use "
            "this when the user asks for something that does not exist yet — a "
            "new program, a new project — rather than saying there is nowhere "
            "to put it. Only works when a workspace is configured."
        ),
        parameters=schema_object(
            {
                "name": {
                    "type": "string",
                    "description": "lowercase, e.g. `snake-opengl`; becomes a directory",
                },
                "description": {"type": "string", "description": "one line on what it is"},
            },
            ["name"],
        ),
        handler=tool_create_repository,
        # Tier 2, not 3. It makes ONE empty directory inside a root the
        # operator named for exactly this, writes two files nobody will miss,
        # and cannot touch anything else — `write_file` is Tier 3 because it
        # overwrites things that already exist, and this cannot. Holding it for
        # a human would put an approval card between "write me a Snake game"
        # and anything happening at all.
        tier=TIER_BACKGROUND,
    )

    async def tool_clone(args: dict[str, Any], context: Any = None) -> Any:
        cfg = get_config(jarvis)
        entry, why = await async_clone_repository(
            jarvis,
            str(args.get("forge") or ""),
            str(args.get("project") or ""),
            # Not the model's choice, for the same reason as create_repository.
            environment=(cfg.default_environment if cfg else ""),
        )
        if entry is None:
            return {"status": "error", "error": why}
        return {
            "status": "ok",
            "repository": entry.name,
            "message": f"Cloned {entry.name}. Start a coding job to work in it.",
        }

    async def tool_push(args: dict[str, Any], context: Any = None) -> Any:
        ok, note = await async_push_branch(
            jarvis, str(args.get("repo") or ""), str(args.get("branch") or "")
        )
        return {"status": "ok" if ok else "error", "message": note}

    registry.register(
        name="clone_repository",
        description=(
            "Clone a repository from GitHub or GitLab into the workspace so a "
            "coding job can work in it. Only repositories the operator has "
            "permitted can be cloned — call list_code_repositories to see the "
            "forges and what each one allows."
        ),
        parameters=schema_object(
            {
                "forge": {"type": "string", "description": "the forge's name, e.g. `github`"},
                "project": {"type": "string", "description": "`owner/name`"},
            },
            ["forge", "project"],
        ),
        handler=tool_clone,
        # Reading something the operator said Jarvis may read, into a directory
        # they set aside for it. The allow-list is the gate, and it is enforced
        # in code rather than by asking.
        tier=TIER_BACKGROUND,
    )
    registry.register(
        name="push_branch",
        description=(
            "Push a finished `jarvis/…` branch back to the forge it came from, "
            "so a human can open a pull request. Never pushes main and never "
            "forces."
        ),
        parameters=schema_object(
            {
                "repo": {"type": "string", "description": "the repository's name"},
                "branch": {"type": "string", "description": "the jarvis/… branch"},
            },
            ["repo", "branch"],
        ),
        handler=tool_push,
        # Outward-facing: this puts code on a server other people can see, and
        # deleting a local file does not undo it.
        tier=TIER_APPROVAL,
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
# making a repository
# ---------------------------------------------------------------------------
def get_repos(jarvis: "Jarvis") -> RepoStore | None:
    found = _store(jarvis).get(DATA_REPOS)
    return found if isinstance(found, RepoStore) else None


async def async_create_repository(
    jarvis: "Jarvis",
    name: str,
    *,
    description: str = "",
    environment: str = "",
) -> tuple[Any, str]:
    """Make one, and put it in the live registry. `(entry, "")` or `(None, why)`.

    The registry update is the half that is easy to forget: a repository on
    disk that no listing knows about is one nothing can be started against, so
    creating it would look like it worked and then nothing would.
    """
    cfg = get_config(jarvis)
    repos = get_repos(jarvis)
    if cfg is None or repos is None:
        return None, "the code integration is not set up on this server"
    if not repos.enabled:
        return None, (
            "There is nowhere to put it. Set `code: workspace:` in "
            "configuration.yaml to a directory Jarvis may create repositories "
            "in, then restart."
        )

    wanted = str(environment or "").strip()
    if wanted and wanted not in cfg.environments:
        return None, (
            f"There is no environment called {wanted!r}. There is: "
            f"{', '.join(cfg.environments) or 'none configured'}."
        )

    entry, why = await repos.async_create(
        name,
        description=description,
        environment=wanted,
        # Declared repositories are the ones this must not collide with; the
        # store already knows about its own.
        taken={n for n, r in cfg.repositories.items() if not r.managed},
    )
    if entry is None:
        return None, why
    cfg.repositories[entry.name] = entry.as_repo()
    return entry, ""


async def async_clone_repository(
    jarvis: "Jarvis",
    forge_name: str,
    project: str,
    *,
    name: str = "",
    environment: str = "",
) -> tuple[Any, str]:
    """Clone a permitted repository. `(entry, "")` or `(None, why not)`.

    The allow-list check lives here so the refusal can name the forge and say
    what IS permitted — a model that guessed a path should be told the rule,
    not just "no".
    """
    cfg = get_config(jarvis)
    repos = get_repos(jarvis)
    if cfg is None or repos is None:
        return None, "the code integration is not set up on this server"
    forge = cfg.forges.get(str(forge_name or "").strip())
    if forge is None:
        return None, (
            f"There is no forge called {forge_name!r}. There is: "
            f"{', '.join(cfg.forges) or 'none configured'}."
        )
    if not forge.token:
        return None, (
            f"{forge.name} has no token, so nothing can be cloned from it. "
            "Set it in configuration.yaml."
        )
    if not split_project(project):
        return None, f"{project!r} is not a repository path like owner/name."
    if not permits(forge, project):
        return None, (
            f"{project} is not on {forge.name}'s allow-list, so Jarvis may not "
            f"touch it. Permitted: {', '.join(forge.allow) or 'nothing yet'}."
        )
    if environment and environment not in cfg.environments:
        return None, f"There is no environment called {environment!r}."

    entry, why = await repos.async_clone(
        forge,
        project,
        config_dir=jarvis.config_dir,
        name=name,
        environment=environment,
        taken={n for n, r in cfg.repositories.items() if not r.managed},
    )
    if entry is None:
        return None, why
    cfg.repositories[entry.name] = entry.as_repo()
    return entry, ""


async def async_push_branch(
    jarvis: "Jarvis", repo_name: str, branch: str
) -> tuple[bool, str]:
    """Push one `jarvis/…` branch back to the forge it came from."""
    cfg = get_config(jarvis)
    if cfg is None:
        return False, "the code integration is not set up on this server"
    repo = cfg.repositories.get(str(repo_name or "").strip())
    if repo is None:
        return False, f"There is no repository called {repo_name!r}."
    if not repo.origin:
        return False, (
            f"{repo.name} did not come from a forge, so there is nowhere to "
            "push it."
        )
    forge_name = repo.origin.split(":", 1)[0]
    forge = cfg.forges.get(forge_name)
    if forge is None:
        return False, f"{repo.name} came from {forge_name!r}, which is no longer configured."
    if not forge.push:
        return False, (
            f"{forge.name} is read-only. Set `push: true` on it in "
            "configuration.yaml to allow this."
        )
    project = repo.origin.split(":", 1)[1] if ":" in repo.origin else ""
    if not permits(forge, project):
        return False, f"{project} is no longer on {forge.name}'s allow-list."

    ws = Workspace(repo, environment=cfg.environment_for(repo))
    return await ws.push(forge, str(branch or ""), config_dir=jarvis.config_dir)


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
        workspace=Workspace(
            repo, sandbox=cfg.sandbox, environment=cfg.environment_for(repo)
        ),
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
    repos = get_repos(jarvis)
    return {
        "repositories": cfg.listing(),
        "jobs": jobs,
        "sandboxed": bool(cfg.sandbox),
        "environments": [e.as_dict() for e in cfg.environments.values()],
        # Never a token — `as_dict` sends `has_token` instead.
        "forges": [f.as_dict() for f in cfg.forges.values()],
        # Whether the console may offer a "new repository" form at all, and
        # where the files would land if it does.
        "can_create": bool(repos and repos.enabled),
        "workspace": str(cfg.workspace) if cfg.workspace else "",
    }


__all__ = [
    "CodeConfig",
    "async_create_repository",
    "check_name",
    "get_repos",
    "DOMAIN",
    "KIND",
    "async_setup",
    "async_start",
    "get_config",
    "listing_payload",
    "one_line_result",
    "result_payload",
]