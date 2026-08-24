---
paths:
  - "jarvis-core/jarvis/integrations/code/**"
  - "jarvis-core/tests/test_code_*.py"
  - "jarvis-core/tests/test_gated_services.py"
  - "jarvis-core/docs/code.md"
---

# Jarvis Code sandbox — invariants every edit here must keep

Read `jarvis-core/docs/code.md` ("What confines a job, exactly", "Git is an execution primitive") and the module docstring of `sandbox.py` before changing anything in this directory. The fences *are* the safety argument, and the tests named below are what make each one a claim rather than a comment. If a change legitimately moves one, change the test in the same commit and say why in the docstring. Never weaken a test to make a change pass.

- `container_argv()` stays a pure function returning `list[str]`, so every fence is provable on a machine with no Docker (`tests/test_code_sandbox.py`). It must emit `--rm`, `--network none` unless the environment asked for `egress`, `--user <host uid>:<gid>`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit`, `--memory`, `--cpus`, a bounded tmpfs `/tmp`, `--ulimit fsize` and `nofile`, and exactly one host path (`-v <repo>:/work -w /work`). Never `--privileged`, host networking, `/var/run/docker.sock`, or the operator's environment (`env:` is an explicit allow-list). It refuses to build a command line when jarvis-core runs as root.
- `--ulimit` is not inherited by `docker exec`; `exec_argv()` re-applies it in the shell, and that is the only reason it holds. `--ulimit fsize` is in bytes, `ulimit -f` in 512-byte blocks — two constants, keep both.
- One container per job, created lazily, reached by `docker exec`, removed at job end. The old per-command `run_in_container` was deleted as a second execution path — do not reintroduce one.
- The tier is decided in code, never by the model, and may only ever be raised. A held Tier-3 action returns `approval_required` and ends the model's turn — no retry, no self-approval. Arguments are pinned at request creation. The model never picks its environment: `default_environment` is the operator's choice (otherwise the model could read the listing and hand itself the one with `egress`).
- A writable repository with no `environment:` and no `sandbox:` wrapper is not offered `run_check` at all and refuses it if named (`Workspace.unconfined_check_refusal`): a check executes files out of the working tree (`conftest.py`, `package.json`, the Makefile), so an allow-list of command strings bounds nothing.
- Git runs on the host and is an execution primitive. `HOST_GIT_GUARDS` (`workspace.py`) and `tests/test_code_git_escape.py` pin: `.git/config` checked before **every** host git call (textual scan *and* `git config --list --includes --name-only`; refuse if either objects), `[include]` followed, global/system config read from `/dev/null`, `core.hooksPath=/dev/null`, an executable hook refused outright, `.git` writes refused host-side.
- Forge tokens reach git only through `GIT_ASKPASS` — never argv, the container, the model's context, or `.git/config` (`tests/test_code_forges.py`). Push is Tier 3, `jarvis/…` branches only, never `--force`/`--force-with-lease`, refused on a rewritten origin. An empty `allow:` permits nothing; `tests/contracts/forge_allow_list.json` is shared with the console's vitest suite so the two decisions cannot drift.
- Every Tier-3 tool has a service twin in `GATED_SERVICES` (`jarvis/const.py`) so an automation cannot call it at Tier 1; `tests/test_gated_services.py` fails if the two tables disagree. New Tier-3 tool → new twin.
- Jarvis never deletes a repository: "forget" drops the row and leaves the files.
- The compose service keeps `network_mode: none` (`tests/test_packaging.py`, `scripts/egress-audit.sh`). `network: egress` is Docker's bridge — the gateway host and the whole LAN, including jarvis-core's own API. Any UI or doc text must say so; `network_name:` is how an operator narrows it.
- What is deliberately *not* claimed — the container can write `.git`, a hook left in `.git/hooks` outlives the job for the operator's own shell, `persist: true` carries installs between jobs, disk is bounded only per file, and Docker's own confinement is not proved — is listed in the `sandbox.py` docstring. Do not add a claim there without a test that demonstrates it.
