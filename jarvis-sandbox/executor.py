#!/usr/bin/env python3
"""Sandbox command executor — the only process in the jail.

Runs inside a container with: network_mode none, non-root uid 10001,
read-only rootfs, cap_drop ALL, no-new-privileges, 1 GiB mem, 128 pids,
tmpfs /tmp. It polls /workspace/.exec/queue for job files the orchestrator
enqueues AFTER human approval, runs each with resource limits, and writes
results to /workspace/.exec/results.

Defence in depth, not the gate itself: the approval gate lives in the
orchestrator + Home Assistant. If something ever slipped past it, this box
still has no network, no host mounts, no capabilities and no secrets.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import time
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))
QUEUE = WORKSPACE / ".exec" / "queue"
RESULTS = WORKSPACE / ".exec" / "results"
POLL_SECONDS = 0.2
MAX_OUTPUT = 64 * 1024
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    os.setsid()  # own process group so we can kill the whole tree


def clamp_timeout(value) -> int:
    try:
        t = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(1, min(t, MAX_TIMEOUT))


def run_job(job: dict) -> dict:
    command = job.get("command", "")
    timeout = clamp_timeout(job.get("timeout"))
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=WORKSPACE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=_limits,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
            },
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            timed_out = True
        return {
            "id": job.get("id"),
            "exit_code": -9 if timed_out else proc.returncode,
            "stdout": out.decode(errors="replace")[:MAX_OUTPUT],
            "stderr": err.decode(errors="replace")[:MAX_OUTPUT],
            "timed_out": timed_out,
            "duration_s": round(time.monotonic() - started, 3),
        }
    except Exception as e:
        return {
            "id": job.get("id"),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"executor error: {e}",
            "timed_out": False,
            "duration_s": round(time.monotonic() - started, 3),
        }


def write_result(result: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS / f".{result['id']}.tmp"
    tmp.write_text(json.dumps(result))
    os.rename(tmp, RESULTS / f"{result['id']}.json")


def main() -> None:
    QUEUE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    print(f"jarvis-sandbox executor polling {QUEUE}", flush=True)
    while True:
        for path in sorted(QUEUE.glob("*.json")):
            try:
                job = json.loads(path.read_text())
            except json.JSONDecodeError:
                time.sleep(0.05)
                continue
            path.unlink(missing_ok=True)  # claim
            print(f"exec {job.get('id')}: {job.get('command')!r}", flush=True)
            write_result(run_job(job))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
