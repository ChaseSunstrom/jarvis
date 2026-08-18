# Jarvis Code

A coding agent that works in repositories you name. Ask for a change in the
console's **CODE** tab, or say it out loud; Jarvis reads the repository, makes
the change on a branch of its own, runs that repository's own checks, and
leaves you a diff.

```
plan      one model call -> a checklist of steps
work      a bounded loop: list_files, read_file, search, edit_file, write_file
check     the repository's OWN commands (`pytest -q`), never a shell
report    a branch, a diff, a summary, and what the checks said
```

It is the local twin of `orchestrator.code_task`. That one posts to a container
and polls a job id; this one runs in jarvis-core, in repositories the operator
declared, and reports through the task registry — so its progress is the same
bar as everything else, on the console and on the phone, and cancelling it
stops it.

## Configure it

Nothing is enabled by default. With no `repositories:` the tools register and
answer *"no repositories are configured"*, which is the honest state for a box
that was just installed.

```yaml
code:
  repositories:
    - name: jarvis
      path: ~/src/jarvis
      description: the assistant itself
      writable: true
      checks:
        - pytest -q
        - ruff check .

  sandbox: ""             # a command prefix every check runs behind
  max_rounds: 40          # tool calls one job may take (4-200)
  max_minutes: 20         # wall clock (1-120)
  model: ""               # empty means the conversation model
```

| key | what |
|---|---|
| `name` | how you and the model refer to it |
| `path` | a directory on the server. It must be a git repository |
| `description` | free text, shown to the model and on the Code page |
| `writable` | **false by default.** False means read and report, never change |
| `checks` | the only commands a job may run. Up to eight |

## What confines a job, exactly

"Sandboxed" is a word that gets used to mean anything, so here is the list, and
nothing outside it is claimed.

1. **A repository is opt-in.** Only paths in `repositories:` exist. There is no
   tool that takes an arbitrary directory.
2. **Writing is opt-in per repository.** With `writable: false` the edit tools
   are not offered to the model at all — and are refused if it invents the call
   anyway, because a guarantee that lives only in a schema list is one a
   confused server can step around.
3. **Paths are confined** by `integrations/files/paths.py` — the same resolver,
   with the same symlink check, that the `files:` integration uses. Not a
   second implementation: two path checkers is one path checker and a bug.
4. **There is no shell.** `run_check` matches a whole string against `checks:`,
   splits it with `shlex`, and runs it with `create_subprocess_exec`. The model
   chooses *whether* to run a check, never *what* it is. `pytest -q; curl evil`
   does not match `pytest -q`.
5. **A job never touches your branch.** It refuses to start on a dirty tree,
   makes `jarvis/<date>-<job>`, works there, and stops. The change reaches your
   branch when a person merges it.

### `sandbox:` — the honest one

A check command *is* arbitrary code: it is your test suite. It is code you
wrote, before the job existed, but a test suite that pulls from the network is
still a test suite that pulls from the network. `sandbox:` is a command prefix
every check runs behind, and `{repo}` becomes the repository's absolute path:

```yaml
  sandbox: "docker run --rm --network none -v {repo}:/w -w /w python:3.12"
  sandbox: "bwrap --unshare-net --bind {repo} {repo} --chdir {repo}"
```

It is your wrapper because only you know what your checks need. With it empty,
a check runs as jarvis-core does — and the Code page says exactly that rather
than implying an isolation that is not there. The rest of the list above does
not depend on it.

## Two doors, different widths

The model's `start_coding_job` tool is **Tier 3**: it asks a human before a job
starts, and `code.run` is in `GATED_SERVICES` so an automation cannot reach
around it. Starting a job edits files on a real disk.

The console's own START button is not gated. That request carried a bearer
token; a tool call may have been shaped by a page the model read. Same
asymmetry, same reason, as the console being able to schedule a service call
when the assistant cannot.

## The progress bar is the model's own plan

The first model call asks for a checklist, and that checklist becomes the
task's steps. Until it comes back the task is `open_ended` — an honest
indeterminate bar — and afterwards it is a real fraction, because there is a
real denominator. The model moves it with a `plan_step` tool as it goes.

This is why the planning step exists at all. A model that writes down what it
is going to do makes better changes, but more importantly it is the only
honest source for a number on a screen.

## Cancelling

`api/common.py` is careful to say that a cancelled task may still be running if
its worker does not check. This worker checks — between every round and between
every tool call — and stops.

What it does **not** do is throw the work away. The branch stays, with whatever
was done to it, because somebody who cancelled half way through usually wants
to see how far it got, and `git checkout <your branch>` is one command they
already know. Discarding without being asked is the one thing that cannot be
undone.

## From the console

`jarvis/code/list`, `jarvis/code/start` and `jarvis/code/result` over the
websocket; `GET /api/code`, `POST /api/code/jobs`, `GET /api/code/jobs/{id}`
over REST. See [clients.md](clients.md).

## Services and tools

The tool is `start_coding_job`, not `code_task`: `orchestrator` already
registers a `code_task` — the remote one, at Tier 2 — and two integrations
meaning different things by one name is an ordering accident waiting to
happen. Whichever loaded second would win, or the registry would refuse the
second and log it.

| service | what |
|---|---|
| `code.run` | `repo`, `instruction` → a task id. **Approval-gated** |
| `code.repositories` | what Jarvis may work in |
| `code.result` | the branch, diff, checks and trail of a finished job |

| tool | tier | what |
|---|---|---|
| `list_code_repositories` | 1 | the names, so the model picks one that exists |
| `start_coding_job` | 3 | start a job. Returns a task id, not a diff |

## Scheduling one

`schedule:` has a `code` kind, so a job can run at three in the morning and
leave you a branch to look at over breakfast:

```yaml
schedule:
  jobs:
    - id: nightly_lint
      kind: code
      when: {mode: daily, at: "03:00"}
      repo: jarvis
      instruction: fix every ruff warning without changing behaviour
```

The console can add one too, from the **Scheduled** panel on the Tasks page.
The **assistant cannot** — `code` is not in `MODEL_KINDS`, for the same reason
`service` is not: starting a job asks a human, and a timer must not be the way
round that.

## What it deliberately does not do

**It does not open a pull request.** It makes a branch. Pushing anywhere is a
credential this process does not have and should not want, and "it opened a PR
against main" is not a thing to discover after the fact.

**It does not ask follow-up questions.** A job runs on its own, so a
three-word instruction is three minutes of guessing. The console refuses one
too short to act on and says why.

**It does not commit unless it changed something.** A job that concludes
nothing needed changing says so, which is a legitimate and useful answer.
