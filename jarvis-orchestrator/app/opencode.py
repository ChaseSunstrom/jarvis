"""OpenCode-backed agentic coding jobs.

OpenCode (https://opencode.ai, open source) runs headless against local
Ollama. Jobs WRITE inside /workspace/<repo> only; nothing is run or
committed by this module — ``apply`` is a separate approval-gated endpoint,
and even then execution happens in the sandbox, not here.

If the ``opencode`` binary is missing (e.g. dev container), jobs fail with a
clear message instead of pretending. Alternatives (Aider, Continue) slot in
by replacing ``build_command``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

OPENCODE_TIMEOUT = 15 * 60
DIFF_LIMIT = 512 * 1024


@dataclass
class CodeJob:
    job_id: str
    repo: str
    instruction: str
    status: str = "queued"  # queued | running | done | error | applied
    summary: str = ""
    diff_stat: str = ""
    diff_path: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "job_id": self.job_id,
            "repo": self.repo,
            "instruction": self.instruction,
            "status": self.status,
            "summary": self.summary,
            "diff_stat": self.diff_stat,
            "diff_path": self.diff_path,
            "error": self.error,
        }


def safe_repo_dir(workspace: Path, repo: str) -> Path:
    """Resolve repo inside the workspace; reject traversal outside it."""
    candidate = (workspace / repo).resolve()
    if not str(candidate).startswith(str(workspace.resolve()) + "/") and candidate != workspace.resolve():
        raise ValueError("repo escapes the workspace")
    if candidate == workspace.resolve():
        raise ValueError("repo must be a subdirectory of the workspace")
    return candidate


def build_command(model: str, instruction: str) -> list[str]:
    """The coding agent's command line.

    The model name is passed through as the server calls it. It used to be
    prefixed `ollama/`, which is a provider name — on a house running llama-swap
    or vLLM behind an OpenAI-compatible URL that prefix names a provider that is
    not there, and opencode fails to resolve a model that exists. The provider
    belongs in opencode's own config, next to the base URL.
    """
    # OpenCode 1.x names a model `provider/model`; a bare name is the house's
    # own provider, written by `write_opencode_config` at startup.
    named = model if "/" in model else f"{HOUSE_PROVIDER}/{model}"
    return [
        "opencode", "run",
        "--model", named,
        instruction,
    ]


#: The provider id OpenCode is given for the house's own model server.
HOUSE_PROVIDER = "house"


def write_opencode_config(path: Path, base_url: str, api_key: str, models: list[str]) -> Path:
    """OpenCode's config for the house's OpenAI-compatible server (1.x shape).

    OpenCode knows nothing of LLM_URL: without this it has no provider, and a
    bare model name resolves to nothing. `@ai-sdk/openai-compatible` is what
    it uses for llama-swap, LiteLLM, vLLM and LM Studio alike; the models are
    listed so `--model house/<name>` is a name it has, not a guess. Written
    to a tmpfs path at startup and pointed at by OPENCODE_CONFIG, because the
    image's root is read-only.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    options: dict = {"baseURL": base_url.rstrip("/")}
    if api_key:
        options["apiKey"] = api_key
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            HOUSE_PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "House",
                "options": options,
                "models": {name: {"name": name} for name in dict.fromkeys(m for m in models if m)},
            }
        },
    }
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


async def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", f"timed out after {timeout:.0f}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class CodeJobRunner:
    def __init__(self, workspace: str | Path, coder_model: str,
                 notify_url: str | None = None):
        self.workspace = Path(workspace)
        self.model = coder_model
        self.notify_url = notify_url
        self.jobs: dict[str, CodeJob] = {}
        self.jobs_dir = self.workspace / ".code_jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def create(self, repo: str, instruction: str) -> CodeJob:
        safe_repo_dir(self.workspace, repo)  # raises on traversal
        job = CodeJob(uuid.uuid4().hex[:12], repo, instruction)
        self.jobs[job.job_id] = job
        self.persist(job)
        return job

    # --- surviving a restart -------------------------------------------------
    #
    # Jobs lived in a dict and nothing else. A restart forgot every one of them:
    # a client polling `/code_task/{id}` got a 404 for work that had finished
    # minutes earlier, and the diff was sitting on disk beside a record that no
    # longer existed. `load_persisted` was written for this and never called.

    def _record_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def persist(self, job: CodeJob) -> None:
        """Write one job's record beside its diff. Best effort, never fatal."""
        try:
            self._record_path(job.job_id).write_text(json.dumps({**job.public(), "created": job.created}))
        except OSError:
            _LOGGER.exception("could not persist job %s", job.job_id)

    def load_persisted(self) -> int:
        """Read every job record back. Returns how many were restored."""
        restored = 0
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                _LOGGER.warning("ignoring unreadable job record %s", path.name)
                continue
            job_id = str(raw.get("job_id") or "")
            if not job_id or job_id in self.jobs:
                continue
            job = CodeJob(job_id, str(raw.get("repo") or ""), str(raw.get("instruction") or ""))
            # A job that was RUNNING when the process died is not running now,
            # and saying otherwise leaves a client polling for ever.
            status = str(raw.get("status") or "queued")
            job.status = "error" if status in ("queued", "running") else status
            if job.status == "error" and not raw.get("error"):
                job.error = "the orchestrator restarted while this job was running"
            else:
                job.error = str(raw.get("error") or "")
            job.summary = str(raw.get("summary") or "")
            job.diff_stat = str(raw.get("diff_stat") or "")
            job.diff_path = str(raw.get("diff_path") or "")
            self.jobs[job_id] = job
            restored += 1
        return restored

    async def run(self, job: CodeJob) -> None:
        job.status = "running"
        self.persist(job)
        try:
            repo_dir = safe_repo_dir(self.workspace, job.repo)
            if not repo_dir.is_dir():
                raise FileNotFoundError(f"no such workspace repo: {job.repo}")
            if shutil.which("opencode") is None:
                raise RuntimeError(
                    "opencode binary not installed in orchestrator image"
                )
            # ensure a git baseline so we can diff afterwards
            if not (repo_dir / ".git").is_dir():
                await _run(["git", "init", "-q"], repo_dir, 30)
                await _run(["git", "add", "-A"], repo_dir, 60)
                await _run(
                    ["git", "-c", "user.email=jarvis@local",
                     "-c", "user.name=jarvis", "commit", "-qm", "baseline",
                     "--allow-empty"],
                    repo_dir, 60,
                )
            code, out, err = await _run(
                build_command(self.model, job.instruction),
                repo_dir, OPENCODE_TIMEOUT,
            )
            if code != 0:
                raise RuntimeError(f"opencode exited {code}: {err[-2000:]}")

            _, diff, _ = await _run(["git", "diff"], repo_dir, 60)
            _, stat, _ = await _run(["git", "diff", "--stat"], repo_dir, 60)
            diff_file = self.jobs_dir / f"{job.job_id}.diff"
            diff_file.write_text(diff[:DIFF_LIMIT])
            job.diff_path = str(diff_file)
            job.diff_stat = stat.strip()[-500:]
            job.summary = out.strip()[-500:] or "opencode finished"
            job.status = "done"
        except Exception as e:
            job.status = "error"
            job.error = str(e)
        finally:
            # On disk before the notification goes out: a client told the job is
            # done and then handed a 404 by a restarted orchestrator is worse
            # than one told a little late.
            self.persist(job)
            await self._notify(job)

    async def _notify(self, job: CodeJob) -> None:
        if not self.notify_url:
            return
        try:
            import httpx

            async with httpx.AsyncClient() as c:
                await c.post(self.notify_url, json={
                    "job_id": job.job_id,
                    "status": job.status,
                    "summary": job.summary or job.error,
                }, timeout=10)
        except Exception:
            pass  # notification is best-effort

    async def apply(self, job_id: str, mode: str = "commit") -> dict:
        """Called ONLY from the approval-gated endpoint."""
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != "done":
            raise ValueError(f"job is {job.status}, not applyable")
        repo_dir = safe_repo_dir(self.workspace, job.repo)
        if mode == "commit":
            await _run(["git", "add", "-A"], repo_dir, 60)
            code, out, err = await _run(
                ["git", "-c", "user.email=jarvis@local", "-c",
                 "user.name=jarvis", "commit", "-m",
                 f"jarvis code_task {job.job_id}: {job.instruction[:60]}"],
                repo_dir, 60,
            )
            job.status = "applied"
            return {"status": "applied", "mode": mode,
                    "detail": (out or err).strip()[-500:]}
        raise ValueError(
            "mode 'run' goes through the sandbox: approve an execute_command "
            "for the specific run invocation instead"
        )


def load_persisted(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}
