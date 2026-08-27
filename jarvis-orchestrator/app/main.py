"""jarvis-orchestrator — fan-out, agentic coding, gated command execution.

Auth: every endpoint except /healthz requires `Authorization: Bearer
$ORCHESTRATOR_TOKEN`. The execute/approve, execute/deny and code apply
endpoints ADDITIONALLY require `X-Approval-Secret: $APPROVAL_SECRET` — that
header is only ever sent by Home Assistant's approval scripts after a human
tapped Approve. See app/exec_gate.py for the invariants and
tests/test_api.py for the adversarial gate tests.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .exec_gate import ExecGate, GateError
from .fanout import fan_out
from .opencode import CodeJobRunner, write_opencode_config
from .sandbox_queue import SandboxQueue

# The OpenAI-compatible model endpoint — llama-swap, llama.cpp's server, vLLM,
# LM Studio, LiteLLM. The same URL jarvis-core uses, because two components
# talking two dialects to one server is how a house ends up with a working
# assistant and a delegate tool that answers 404. OLLAMA_URL is still read as a
# fallback for an install that has not been repointed yet, with /v1 appended.
def _model_base_url() -> str:
    explicit = os.environ.get("LLM_URL", "").rstrip("/")
    if explicit:
        return explicit
    legacy = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    return legacy if legacy.endswith("/v1") else f"{legacy}/v1"


LLM_URL = _model_base_url()
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "qwen3:8b")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
CODER_MODEL = os.environ.get("CODER_MODEL", "qwen2.5-coder:7b")
WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
ORCHESTRATOR_TOKEN = os.environ.get("ORCHESTRATOR_TOKEN", "")
APPROVAL_SECRET = os.environ.get("APPROVAL_SECRET", "")
HA_WEBHOOK_URL = os.environ.get("HA_WEBHOOK_URL", "")  # e.g. http://ha:8123/api/webhook/jarvis_code_done
EXEC_RESULT_TIMEOUT = float(os.environ.get("EXEC_RESULT_TIMEOUT", "120"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ORCHESTRATOR_TOKEN or not APPROVAL_SECRET:
        raise RuntimeError(
            "ORCHESTRATOR_TOKEN and APPROVAL_SECRET must be set (see .env.example)"
        )
    # OpenCode (M82): a writable home on the tmpfs (the image's root is
    # read-only), and its config for the house's model server, before any
    # coding job asks it to run.
    home = Path(os.environ.get("OPENCODE_HOME", "/tmp/home"))
    try:
        home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HOME", str(home))
        config_path = write_opencode_config(
            home / ".config" / "opencode" / "opencode.json", LLM_URL, LLM_API_KEY,
            [CODER_MODEL, PLANNER_MODEL],
        )
        os.environ["OPENCODE_CONFIG"] = str(config_path)
    except OSError as err:  # a full tmpfs must not stop the broker
        logging.getLogger(__name__).warning("could not write OpenCode's config: %s", err)
    # Which binary will run a coding job, read once: the console's Code screen
    # says "opencode 1.18.23" or "not installed" from this (M101), instead of
    # a green tick over an image whose install had silently failed (M82).
    app.state.opencode_version = probe_opencode_version()
    app.state.gate = ExecGate(APPROVAL_SECRET)
    app.state.queue = SandboxQueue(WORKSPACE)
    app.state.coder = CodeJobRunner(
        WORKSPACE, CODER_MODEL, HA_WEBHOOK_URL or None
    )
    # Bring finished and in-flight jobs back. `load_persisted` existed since the
    # runner was written and nothing called it, so every restart silently forgot
    # every job: a client polling `/code_task/{id}` got a 404 for work that had
    # finished minutes earlier, with the diff sitting on disk beside a record
    # that no longer existed.
    try:
        restored = app.state.coder.load_persisted()
        if restored:
            logging.getLogger(__name__).info("reloaded %d code job(s)", restored)
    except Exception:  # pragma: no cover - a bad record is not a boot failure
        logging.getLogger(__name__).exception("could not reload persisted code jobs")
    yield


app = FastAPI(title="jarvis-orchestrator", lifespan=lifespan)


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {ORCHESTRATOR_TOKEN}"
    if not authorization or not hmac.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise HTTPException(401, "bad or missing bearer token")


def require_approval_secret(
    x_approval_secret: str | None = Header(default=None),
) -> None:
    if not x_approval_secret or not hmac.compare_digest(
        x_approval_secret.encode(), APPROVAL_SECRET.encode()
    ):
        raise HTTPException(403, "approval secret missing or wrong")


def probe_opencode_version(timeout: float = 5.0) -> str:
    """`opencode --version`, or "" when the binary is absent or will not answer.

    Never raises: the broker must come up on an image whose install failed,
    and say so through /healthz rather than refuse to start.
    """
    import shutil
    import subprocess

    binary = shutil.which("opencode")
    if not binary:
        return ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (out.stdout or out.stderr or "").strip().splitlines()
    return text[-1].strip()[:40] if text else ""


@app.get("/healthz")
def healthz():
    # What it would talk to, so a console can say "delegation: no model"
    # instead of a green tick over a 404 (the services audit, 27 Aug 2026).
    # The URL and model names only — never the key. And which binary will run
    # a job (M101): "" when OpenCode is not in the image.
    return {
        "status": "ok",
        "llm_url": LLM_URL,
        "planner_model": PLANNER_MODEL,
        "coder_model": CODER_MODEL,
        "llm_key_set": bool(LLM_API_KEY),
        "backend": "opencode",
        "opencode_version": str(getattr(app.state, "opencode_version", "") or ""),
    }


# ------------------------------------------------------------- delegation
class DelegateBody(BaseModel):
    tasks: list[str] = Field(min_length=1, max_length=8)


@app.post("/delegate", dependencies=[Depends(require_token)])
async def delegate(body: DelegateBody):
    return await fan_out(body.tasks, LLM_URL, PLANNER_MODEL, api_key=LLM_API_KEY)


# ------------------------------------------------------------ code tasks
class CodeTaskBody(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=8000)


@app.post("/code_task", dependencies=[Depends(require_token)])
async def code_task(body: CodeTaskBody):
    try:
        job = app.state.coder.create(body.repo, body.instruction)
    except ValueError as e:
        raise HTTPException(422, str(e))
    asyncio.get_running_loop().create_task(app.state.coder.run(job))
    return {"job_id": job.job_id, "status": job.status}


@app.get("/code_task/{job_id}", dependencies=[Depends(require_token)])
async def code_task_status(job_id: str):
    job = app.state.coder.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.public()


@app.post(
    "/code_task/{job_id}/apply",
    dependencies=[Depends(require_token), Depends(require_approval_secret)],
)
async def code_task_apply(job_id: str, body: dict | None = None):
    mode = (body or {}).get("mode", "commit")
    try:
        return await app.state.coder.apply(job_id, mode)
    except KeyError:
        raise HTTPException(404, "unknown job")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------- gated execution (Tier 3)
class ExecRequestBody(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    why: str = Field(default="", max_length=1000)


class ExecIdBody(BaseModel):
    request_id: str


@app.post("/execute/request", dependencies=[Depends(require_token)])
async def execute_request(body: ExecRequestBody):
    try:
        req = app.state.gate.request(body.command, body.why)
    except GateError as e:
        raise HTTPException(e.status_code, e.detail)
    # NOTE: returns the VERBATIM stored command so HA's approval prompt
    # shows the truth, not the model's paraphrase. Nothing is enqueued here.
    return {"request_id": req.request_id, "command": req.command,
            "why": req.why, "status": req.state}


@app.post(
    "/execute/approve",
    dependencies=[Depends(require_token), Depends(require_approval_secret)],
)
async def execute_approve(body: ExecIdBody):
    gate: ExecGate = app.state.gate
    try:
        req = gate.approve(body.request_id, APPROVAL_SECRET)
    except GateError as e:
        raise HTTPException(e.status_code, e.detail)
    # Only now — post-approval — does anything reach the sandbox.
    queue: SandboxQueue = app.state.queue
    queue.enqueue(req.request_id, req.command)
    result = await asyncio.to_thread(
        queue.wait_result, req.request_id, EXEC_RESULT_TIMEOUT
    )
    gate.mark_done(req.request_id)
    return {
        "status": "timeout" if result.get("timed_out") else "done",
        "request_id": req.request_id,
        "command": req.command,
        "exit_code": result.get("exit_code", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


@app.post(
    "/execute/deny",
    dependencies=[Depends(require_token), Depends(require_approval_secret)],
)
async def execute_deny(body: ExecIdBody):
    try:
        req = app.state.gate.deny(body.request_id, APPROVAL_SECRET)
    except GateError as e:
        raise HTTPException(e.status_code, e.detail)
    return {"request_id": req.request_id, "status": req.state}
