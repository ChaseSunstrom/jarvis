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

## Making repositories

Set one key and Jarvis can create its own:

```yaml
code:
  workspace: ~/jarvis/workspaces
```

Then "write me a Snake game in C++" has somewhere to go: `create_repository`
makes `~/jarvis/workspaces/snake-opengl`, `git init -b main`, a README and an
initial commit — and a coding job fills it in. The console's **CODE** tab grows
a NEW REPOSITORY button doing the same thing.

Unset (the default) means Jarvis may not create anything, and says so rather
than inventing a location.

Names are strict — lowercase letters, digits, dot, dash, underscore — because a
name becomes a directory, a branch prefix and a container mount. The refusal
says what is allowed. Nothing can leave the workspace root: a name goes through
the same resolver, with the same symlink check, as every other path here.

**Jarvis never deletes a repository.** "Forget" drops it from the listing and
leaves the files. `rm -rf` driven by a model — or by a mis-click in a browser —
is the one operation with no undo.

## GitHub and GitLab

A forge is a host, a token, and an **allow-list**:

```yaml
code:
  forges:
    - name: github
      kind: github            # github | gitlab
      token: !env_var GITHUB_TOKEN ""
      push: false             # cloning is reading; pushing is publishing
      allow:
        - chasesunstrom/jarvis
        - chasesunstrom/notes
        # - chasesunstrom/*   # a whole account, if you mean it
```

Your token can very likely reach every repository on the account. The
allow-list is what says which ones **Jarvis** may. Nothing outside it can be
cloned or pushed, matching is case-insensitive (both forges are), and there is
no "allow everything" setting — a forge with an empty `allow:` permits nothing,
which is the safe reading of a half-written config.

Repositories Jarvis creates in the workspace need no entry: it made them.

### The token

It never reaches the model (no tool returns it, the listing sends `has_token`),
never reaches the container (`sandbox.py` passes only the environment's own
declared `env:`, and clone/push happen on the host), and never appears in an
argv where `ps` would show it or in a URL that would end up in `.git/config`.
git gets it through `GIT_ASKPASS`, which is git's own mechanism for exactly
this.

### Pushing

`push_branch` is **Tier 3** and `code.push_branch` is in `GATED_SERVICES`: it
puts code on a server other people can see, and deleting a local file does not
undo it. Three further refusals, in code:

* only a `jarvis/…` branch — never `main`;
* never `--force`, and never `--force-with-lease`;
* `origin` must still point at the forge it was cloned from and carry no
  embedded credential. A remote can be rewritten by anything that can write
  `.git/config`, including a previous job.

## Environments — letting it run things

This is the setting that changes what a coding job *is*.

With **no** environment a job may read, edit, and — on a **read-only**
repository — run the exact strings in that repository's `checks:`. There is no
shell anywhere. That is the original design and it is still the default.

On a **writable** repository with no environment it may read and edit only: a
check runs files the job could have written, so running one on the host would
hand it the machine. See "What confines a job, exactly" below.

With **an** environment, a job may run *any* command — inside a throwaway
container whose only visible directory is that one repository. That is what
makes "install the dependencies, build it, run it, read the errors" possible,
and it needs Docker on the host.

```yaml
code:
  environments:
    - name: cpp
      image: gcc:14
      network: egress
      memory: 2g
      cpus: "2"
      setup:
        - apt-get update && apt-get install -y --no-install-recommends cmake libglfw3-dev

  repositories:
    - name: snake-opengl
      path: ~/jarvis/workspaces/snake-opengl
      writable: true
      environment: cpp
```

### One container per job, and tools that outlive it

A job gets **one** container, created when it first needs one and removed when
it finishes. Every `run_command` reaches it with `docker exec`, so an install
in one command is there for the next — which sounds obvious and was not: the
first version ran `docker run --rm` per command, so `pip install pygame`
installed into a container that was gone before the next line could import it.
Installing was, in effect, impossible.

By default the container is thrown away, so the next job starts from the image
again and reinstalls. If that is a minute of `apt-get` every run, say so:

```yaml
    - name: cpp
      image: gcc:14
      network: egress
      persist: true
```

With `persist`, the container is committed to `jarvis-code-env-cpp:latest` when
the job ends, and the next job starts from that. `apt-get install cmake`
happens once, not once a job.

**The image persists; the container never does.** That distinction is the
design. A long-lived container would have to keep its mounts — and mounts are
fixed when a container is created — so reusing one across repositories would
mean mounting the whole workspace and letting every job see its siblings.
Committing keeps the tools and keeps the one-repository mount.

**What it costs, plainly:** a job can leave something in that image, and every
later job in the same environment starts from it. That is real cross-job
influence, and it is why `persist` is off by default. When an environment gets
into a state you do not like:

```
code.reset_environment  name: cpp
```

throws the image away and goes back to the `image:` you configured.

If you want cheaper rebuilds without that, `cache: true` keeps only the package
*caches* (pip, npm, cargo, go) in a named volume — downloads are reused,
nothing is installed ahead of time. It is deliberately mounted only at cache
paths: a volume over `site-packages` would make installs persist by accident,
which is what `persist` is for and what `cache` is not.

### `network:` is the choice worth making deliberately

| | |
|---|---|
| `none` (default) | nothing reaches out. Fine when dependencies are vendored. |
| `egress` | the container can reach the internet. `pip install` works — and a job can read that repository **and** make outbound connections. |

Give `egress` to scratch projects happily. Give it to a repository holding
something you care about deliberately, or not at all.

### The fences

Not optional and not configurable. Every one is a string in the argv that
`sandbox.py::container_argv()` builds, and every one has a test that fails if
it is dropped:

* `--rm` — the container and everything it installed die with the job.
* `--network none` unless the environment asked for egress.
* `--user <your uid>` — files it writes through the mount are yours, not root's.
* `--cap-drop ALL`, `--security-opt no-new-privileges`.
* `--pids-limit`, `--memory`, `--cpus` — a fork bomb hits a wall.
* a tmpfs for `/tmp`, size-bounded.
* **exactly one host path: the repository.** Not the workspace root, not the
  parent. A job cannot see its neighbours.

And the negatives, which matter as much: no `--privileged`, no host networking,
no `/var/run/docker.sock` (a container that can reach the daemon can start one
that mounts anything), and none of your environment variables — `env:` on the
environment is an explicit allow-list.

`container_argv()` is a pure function returning a list of strings, so all of
the above is proved by unit tests on a machine with no Docker installed.

### Disk, and the one thing that is not bounded

`--memory`, `--cpus`, `--pids-limit` and a 512 MB tmpfs on `/tmp` all leave the
bind mount alone, because `/work` is your filesystem and Docker cannot put a
quota on a bind mount (`--storage-opt size=` covers the container's own layer,
and only on some storage drivers). So a job could fill your disk.

`--ulimit fsize` now caps any single file at `max_file_mb` (2 GB by default),
which the kernel enforces everywhere. It does **not** stop a million small
files. If that matters, put a filesystem quota on the workspace directory —
that one is yours to set, and no container flag substitutes for it.

### What is NOT proved by those tests

That Docker itself confines what it says it confines. The argv is verified;
the kernel is not. If a container escape matters to your threat model, run
jarvis-core on a machine you would be willing to lose, or use rootless Docker.

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
4. **There is no shell**, and on a writable repository no host execution at
   all. `run_check` matches a whole string against `checks:`, splits it with
   `shlex`, and runs it with `create_subprocess_exec`. The model chooses
   *whether* to run a check, never *what* it is: `pytest -q; curl evil` does
   not match `pytest -q`.

   Choosing *whether* is still too much on a repository the job can write,
   because a check runs FILES out of the working tree — `pytest` imports
   `conftest.py`, `npm test` runs `package.json`, `make` runs the Makefile —
   and writing one of those is a single `write_file`. So a **writable**
   repository with neither an `environment:` nor a `sandbox:` wrapper is not
   offered `run_check` at all, and the tool refuses if the model names it
   anyway. Read-only repositories are unaffected: the job cannot have written
   what the check runs. If your checks stopped running, that is this, and the
   fix is an `environment:`, a `sandbox:` wrapper, or `writable: false`.
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

The **CODE** tab has two buttons above the repository list: **NEW REPOSITORY**
makes an empty one in the workspace, and **CLONE FROM A FORGE** pulls one down.
The clone form shows the forge's allow-list before you type and refuses a path
outside it without a round trip — that refusal is a copy of `permits()`, made
for the message and never for the decision; jarvis-core refuses independently,
and `tests/contracts/forge_allow_list.json` is read by both suites so the copy
cannot drift. A forge with no token says so too: a public clone works without
one, a private one fails asking for a password.

`jarvis/code/list`, `jarvis/code/start`, `jarvis/code/result`,
`jarvis/code/create_repo`, `jarvis/code/clone_repo`, `jarvis/code/forget_repo`
and `jarvis/code/push` over the websocket; `GET /api/code`,
`POST /api/code/jobs`, `GET /api/code/jobs/{id}`, `POST /api/code/repos`,
`POST /api/code/clone` and `POST /api/code/push` over REST. See
[clients.md](clients.md).

## Services and tools

The tool is `start_coding_job`, not `code_task`: `orchestrator` already
registers a `code_task` — the remote one, at Tier 2 — and two integrations
meaning different things by one name is an ordering accident waiting to
happen. Whichever loaded second would win, or the registry would refuse the
second and log it.

| service | what |
|---|---|
| `code.run` | `repo`, `instruction` → a task id. **Approval-gated** |
| `code.create_repository` | `name` → a new repository in the workspace |
| `code.clone_repository` | `forge`, `project` → a clone, if the allow-list permits it |
| `code.reset_environment` | `name` → throws away a persisting environment's image |
| `code.push_branch` | `repo`, `branch` → pushes it. **Approval-gated** |
| `code.repositories` | what Jarvis may work in |
| `code.result` | the branch, diff, checks and trail of a finished job |

| tool | tier | what |
|---|---|---|
| `list_code_repositories` | 1 | the names, so the model picks one that exists |
| `create_repository` | 2 | make an empty repository in the workspace |
| `clone_repository` | 2 | clone a **permitted** forge repository into the workspace |
| `push_branch` | 3 | send one `jarvis/…` branch back to its forge |
| `start_coding_job` | 3 | start a job. Returns a task id, not a diff |

`create_repository` is Tier 2 rather than 3 deliberately: it makes one empty
directory inside a root the operator named for exactly this, and cannot touch
anything else. `write_file` is Tier 3 because it overwrites things that already
exist; this cannot. Holding it for a human would put an approval card between
"write me a Snake game" and anything happening at all.

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

## Git is an execution primitive, and that is handled

Worth knowing about, because it is not obvious. git runs commands out of files
that live **inside** a repository — which is exactly what a coding job edits:

* `.git/hooks/post-checkout` runs on the `git checkout -B` that starts a job;
* `diff.<name>.textconv` / `diff.external` in `.git/config` run on the `git
  diff` that ends one;
* `filter.<name>.clean` runs on the `git add -A` that ends one.

All three execute on the **host**, outside any container. All three were
verified running before they were closed. Three answers, in `workspace.py`:

1. the agent cannot write into `.git` at all (`resolve_for_write`);
2. every host git call carries `core.hooksPath=/dev/null`,
   `core.fsmonitor=` and `protocol.ext.allow=never`, and `diff` adds
   `--no-ext-diff --no-textconv`;
3. clean/smudge filters have no disabling flag — the driver is named by
   `.gitattributes`, so no fixed `-c` covers it — so a repository whose
   `.git/config` sets one makes the job **refuse to start**.

## What it deliberately does not do

**It does not open a pull request.** It pushes a branch, when you have allowed
that; opening the PR is yours.

**It does not ask follow-up questions.** A job runs on its own, so a
three-word instruction is three minutes of guessing. The console refuses one
too short to act on and says why.

**It does not commit unless it changed something.** A job that concludes
nothing needed changing says so, which is a legitimate and useful answer.
