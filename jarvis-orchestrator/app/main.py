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
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .exec_gate import ExecGate, GateError
from .fanout import fan_out
from .opencode import CodeJobRunner
from .sandbox_queue import SandboxQueue

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "qwen3:8b")
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
    app.state.gate = ExecGate(APPROVAL_SECRET)
    app.state.queue = SandboxQueue(WORKSPACE)
    app.state.coder = CodeJobRunner(
        WORKSPACE, CODER_MODEL, HA_WEBHOOK_URL or None
    )
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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ------------------------------------------------------------- delegation
class DelegateBody(BaseModel):
    tasks: list[str] = Field(min_length=1, max_length=8)


@app.post("/delegate", dependencies=[Depends(require_token)])
async def delegate(body: DelegateBody):
    return await fan_out(body.tasks, OLLAMA_URL, PLANNER_MODEL)


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
