"""Plumbing for the desktop end-to-end suite: a proxy, a subprocess, a control dir.

Nothing here fakes any part of the agent. It is the scaffolding that lets a
test *drive* the real one:

:class:`TcpProxy`
    The agent dials the harness through this rather than directly, which gives
    a test one thing it cannot otherwise get: the ability to kill the socket
    out from under a live session (:meth:`TcpProxy.drop_all`) and then watch
    the real reconnect path run. It also counts connections, so "did it
    reconnect?" is a number rather than an inference.

:class:`ControlDir`
    A directory of small JSON files the test writes and the agent's stubbed
    consent prompt and question dialog read. It is the only seam in the whole
    setup: there is no human in CI to click Approve, so the two backends that
    *would* put something on a screen are replaced — and every prompt they
    receive is appended to a JSONL file, which is how a test asserts "it asked
    again" rather than merely "it refused".

:class:`DesktopAgent`
    ``python -m jarvis_desktop run`` as a real subprocess, with its own state
    directory, workspace, config file and log. Everything else — the channel,
    the action registry, the policy engine, the audit log, presence, the
    companion handler — is the shipping code.

Every wait in this file is a wait for a *condition* with a deadline. There is
no bare ``sleep`` anywhere: a poll loop that finishes the moment the condition
holds is both faster and immune to a slow CI box.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "AGENT_DIR",
    "DEVICE_ID",
    "DEVICE_NAME",
    "RUNNER",
    "ControlDir",
    "DesktopAgent",
    "TcpProxy",
    "TimedOut",
    "async_wait_until",
    "service_call",
    "wait_until",
]

#: The package under test, and the launcher that starts it with the two
#: screen-facing backends stubbed. Both absolute: the suite must not care what
#: directory pytest was invoked from.
AGENT_DIR = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().parent / "agent_runner.py"

#: Kept stable so a failure names something recognisable in the server's log.
DEVICE_ID = "e2e-desktop-agent"
DEVICE_NAME = "E2E Desktop"

POLL_INTERVAL = 0.05


class TimedOut(AssertionError):
    """A condition never became true. Carries what was last seen."""


def wait_until(
    check: Callable[[], Any],
    timeout: float,
    what: str,
    interval: float = POLL_INTERVAL,
    on_dead: Callable[[], str | None] | None = None,
) -> Any:
    """Poll ``check`` until it returns something truthy, or give up loudly.

    ``on_dead`` is consulted on every pass; if it returns a string, the wait is
    abandoned immediately with that as the reason. That is what stops a test
    from spending its whole timeout waiting for a process that died in the
    first second.
    """
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        if on_dead is not None:
            dead = on_dead()
            if dead:
                raise TimedOut(f"gave up waiting for {what}: {dead}")
        try:
            last = check()
        except Exception as err:  # noqa: BLE001 - a transient error is not a failure
            last = f"({type(err).__name__}: {err})"
        else:
            if last:
                return last
        if time.monotonic() >= deadline:
            raise TimedOut(f"{what} did not happen within {timeout:g}s (last saw: {last!r})")
        time.sleep(interval)


async def async_wait_until(
    check: Callable[[], Any],
    timeout: float,
    what: str,
    interval: float = POLL_INTERVAL,
) -> Any:
    """:func:`wait_until` for an async test. ``check`` may be a coroutine function."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        try:
            value = check()
            last = await value if asyncio.iscoroutine(value) else value
        except Exception as err:  # noqa: BLE001
            last = f"({type(err).__name__}: {err})"
        else:
            if last:
                return last
        if time.monotonic() >= deadline:
            raise TimedOut(f"{what} did not happen within {timeout:g}s (last saw: {last!r})")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# talking to the server without an event loop
# ---------------------------------------------------------------------------
#: Loopback only, and explicitly *not* through any proxy. ``urlopen`` honours
#: ``http_proxy`` from the environment, so on a runner behind one every REST
#: call the fixtures make would be handed to a proxy that cannot route
#: 127.0.0.1 — and the symptom would be "the agent never registered" ninety
#: seconds later rather than the truth. ``ProxyHandler({})`` disables the
#: lookup outright; the harness is always on this machine.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def service_call(
    base_url: str,
    token: str,
    domain: str,
    service: str,
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """``POST /api/services/<domain>/<service>?return_response=true``, synchronously.

    The session fixtures need to ask the server questions before any event loop
    exists, so this is stdlib urllib rather than the harness's async client.
    """
    url = f"{base_url.rstrip('/')}/api/services/{domain}/{service}?return_response=true"
    payload = json.dumps(data or {}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with _DIRECT.open(request, timeout=timeout) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8") or "{}")
    return body.get("service_response", body)


# ---------------------------------------------------------------------------
# the socket the test can cut
# ---------------------------------------------------------------------------
class _Pair:
    """One proxied connection: the socket in, and the socket out."""

    __slots__ = ("client", "upstream")

    def __init__(self, client: Any, upstream: Any) -> None:
        self.client = client
        self.upstream = upstream

    def kill(self) -> None:
        for writer in (self.client, self.upstream):
            transport = getattr(writer, "transport", None)
            # abort() drops the connection now rather than flushing first,
            # which is the point: this is meant to look like the network going
            # away, not like a polite shutdown.
            with contextlib.suppress(Exception):
                if transport is not None and hasattr(transport, "abort"):
                    transport.abort()
                else:
                    writer.close()


class TcpProxy:
    """A byte-for-byte TCP relay in a background thread.

    Its own event loop, so it is a plain synchronous object from the test's
    point of view and can live for the whole session regardless of how
    pytest-asyncio scopes loops.
    """

    def __init__(self, target_host: str, target_port: int, host: str = "127.0.0.1") -> None:
        self.target_host = target_host
        self.target_port = int(target_port)
        self.host = host
        self.port = 0
        #: Total accepted connections, including any refused while blocked.
        self.opened = 0
        #: Connections actually relayed to the server. A reconnect is this
        #: going up by one.
        self.established = 0
        #: Connections turned away by :meth:`block`.
        self.refused = 0
        self._accepting = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._live: set[_Pair] = set()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> "TcpProxy":
        ready = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._server = loop.run_until_complete(
                    asyncio.start_server(self._handle, self.host, 0)
                )
                self.port = int(self._server.sockets[0].getsockname()[1])
            except BaseException as err:  # noqa: BLE001
                failure.append(err)
                ready.set()
                loop.close()
                return
            ready.set()
            try:
                loop.run_forever()
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=run, name="jarvis-e2e-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(30):
            raise TimedOut("the proxy thread never came up")
        if failure:
            raise failure[0]
        return self

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(10)
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._loop = None

    async def _shutdown(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        self._kill_live()

    # --- the interesting bit ----------------------------------------------
    @property
    def live(self) -> int:
        return len(self._live)

    def stats(self) -> str:
        """One line for a failure message. Reconnect failures are unreadable
        without knowing how many times the agent actually dialled."""
        return (
            f"proxy :{self.port} -> {self.target_host}:{self.target_port} "
            f"opened={self.opened} established={self.established} "
            f"refused={self.refused} live={self.live} "
            f"accepting={self._accepting}"
        )

    def block(self) -> None:
        """Refuse new connections until :meth:`unblock`.

        Blocking before cutting a live connection is what makes "the server saw
        the gap" a fact rather than a race: without it the agent can be back
        before the test has looked, and the assertion becomes a bet on
        scheduling.
        """
        self._accepting = False

    def unblock(self) -> None:
        self._accepting = True

    def drop_all(self) -> int:
        """Cut every connection currently open. Returns how many were cut."""
        loop = self._loop
        if loop is None:
            return 0
        future = asyncio.run_coroutine_threadsafe(self._drop(), loop)
        return int(future.result(10))

    async def _drop(self) -> int:
        return self._kill_live()

    def _kill_live(self) -> int:
        pairs = list(self._live)
        self._live.clear()
        for pair in pairs:
            pair.kill()
        return len(pairs)

    # --- relay ------------------------------------------------------------
    async def _handle(self, reader: Any, writer: Any) -> None:
        self.opened += 1
        if not self._accepting:
            self.refused += 1
            with contextlib.suppress(Exception):
                writer.close()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except OSError:
            with contextlib.suppress(Exception):
                writer.close()
            return
        pair = _Pair(writer, upstream_writer)
        self.established += 1
        self._live.add(pair)
        try:
            await asyncio.gather(
                self._pump(reader, upstream_writer),
                self._pump(upstream_reader, writer),
            )
        finally:
            self._live.discard(pair)
            pair.kill()

    @staticmethod
    async def _pump(reader: Any, writer: Any) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()


# ---------------------------------------------------------------------------
# the seam: what the human would have clicked
# ---------------------------------------------------------------------------
class ControlDir:
    """The files the stubbed consent prompt and question dialog read and write.

    Written by the test, read by the agent. Every write is atomic
    (``os.replace``) so the agent can never read a half-written verdict.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.consent_path = self.path / "consent.json"
        self.answer_path = self.path / "answer.json"
        self.prompts_path = self.path / "prompts.jsonl"
        self.asks_path = self.path / "asks.jsonl"
        # Fail closed by default: a test that forgets to say otherwise gets a
        # denial, never an approval.
        self.fail_closed()

    def fail_closed(self) -> None:
        """Back to deny/dismiss.

        Called before every test as well as at construction. Without it the
        verdict a test set survives into the next one, so a security test that
        does not set its own verdict — and one that fails half way through
        having set ``approved`` — would silently decide what the *following*
        test was measuring. An approval must never be inherited.
        """
        self.set_consent("denied")
        self.set_answer(status="dismissed")

    def discard_records(self) -> None:
        """Drop the prompt and question logs. Only used before the agent starts.

        CI points the work directory at a checked-out artifacts path, and a
        developer re-runs into the same one, so a second run would otherwise
        read the first run's prompts.
        """
        for path in (self.prompts_path, self.asks_path):
            with contextlib.suppress(OSError):
                path.unlink()

    # --- what the test sets ------------------------------------------------
    def set_consent(self, verdict: str) -> None:
        """``denied`` | ``approved`` | ``approved_always`` | ``timeout``."""
        _write_json(self.consent_path, {"verdict": verdict})

    def set_answer(self, status: str = "answered", answer: str | None = None) -> None:
        _write_json(self.answer_path, {"status": status, "answer": answer})

    # --- what the agent recorded -------------------------------------------
    def prompts(self) -> list[dict[str, Any]]:
        """Every Tier-2/Tier-3 confirmation the agent put in front of a human."""
        return _read_jsonl(self.prompts_path)

    def asks(self) -> list[dict[str, Any]]:
        """Every ``companion.ask`` question the agent rendered."""
        return _read_jsonl(self.asks_path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temp, path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line still being appended; it will be there next poll
        if isinstance(entry, dict):
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# the agent itself
# ---------------------------------------------------------------------------
class DesktopAgent:
    """``python -m jarvis_desktop run``, as a real child process."""

    def __init__(
        self,
        server_url: str,
        token: str,
        work_dir: Path,
        device_id: str,
        device_name: str,
        python: str | None = None,
    ) -> None:
        self.server_url = server_url
        self.token = token
        self.device_id = device_id
        self.device_name = device_name
        self.python = python or sys.executable

        self.work_dir = Path(work_dir)
        self.state_dir = self.work_dir / "state"
        self.workspace = self.work_dir / "workspace"
        self.config_path = self.work_dir / "config.json"
        self.log_path = self.work_dir / "agent.log"
        for directory in (self.state_dir, self.workspace):
            directory.mkdir(parents=True, exist_ok=True)

        self.control = ControlDir(self.work_dir / "control")
        self._process: subprocess.Popen[Any] | None = None
        self._log: Any = None

    # --- files it owns ----------------------------------------------------
    @property
    def policy_path(self) -> Path:
        return self.state_dir / "policy.json"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "audit.jsonl"

    def policy(self) -> dict[str, Any]:
        """The persisted policy store, or ``{}`` before anything is remembered.

        Note what ``{}`` means: on a green run this file does not exist at all,
        because nothing the suite does is ever remembered. That makes "the
        policies map is empty" a weak assertion on its own — it would hold just
        as well if this were reading the wrong path. ``test_the_policy_store_is
        _real_and_a_tier_two_answer_can_be_remembered`` is the positive control
        that pins this path to the one the agent really writes; the emptiness
        assertions elsewhere lean on it.
        """
        try:
            loaded = json.loads(self.policy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def remembered(self) -> dict[str, Any]:
        """Just the ``policies`` map."""
        policies = self.policy().get("policies")
        return policies if isinstance(policies, dict) else {}

    def write_policy(
        self,
        policies: dict[str, str] | None = None,
        automation_enabled: bool = True,
        panic: bool = False,
    ) -> None:
        """Edit the policy file from outside the agent, as the CLI or a human would.

        ``PolicyStore`` re-stats the file on every read, so the running agent
        picks this up on its next decision without a restart — which is exactly
        the property that makes the panic switch worth having, and the only way
        to test it against a live process.
        """
        _write_json(
            self.policy_path,
            {
                "version": 1,
                "automation_enabled": bool(automation_enabled),
                "panic": bool(panic),
                "policies": dict(policies or {}),
            },
        )

    def forget_policy(self) -> None:
        """Remove the policy file entirely: back to the shipped defaults."""
        with contextlib.suppress(OSError):
            self.policy_path.unlink()

    def audit(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.audit_path)

    def audit_for(self, action_id: str) -> list[dict[str, Any]]:
        return [e for e in self.audit() if e.get("action") == action_id]

    def workspace_file(self, name: str, content: str = "not yours to delete\n") -> Path:
        """Put a real file in the agent's workspace, from outside the agent.

        The test owns this directory too, so "did the handler run?" can be
        answered by looking at the filesystem rather than by trusting a status
        string the agent sent about itself.
        """
        target = self.workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_config(self) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    # This suite dispatches a couple of dozen commands back to
                    # back, which the shipping default (10 burst, 1/s) is not
                    # sized for. The rate limiter has its own unit tests; what
                    # is under test here is everything downstream of it, so the
                    # limit is configured out of the way rather than slept
                    # around. Both of these are ordinary config keys.
                    "command_rate_capacity": 200.0,
                    "command_rate_per_second": 100.0,
                    "event_rate_capacity": 200.0,
                    "event_rate_per_second": 100.0,
                    # A stub answers instantly; anything slower than this is a
                    # hang, and failing in 20s beats failing in 60.
                    "consent_timeout_s": 20.0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def reset(self) -> None:
        """Delete everything a previous run of this suite left behind.

        The work directory is not always fresh: CI points it at a path inside
        the checkout and a developer re-runs into the same one. A stale
        ``audit.jsonl`` would be read by the closing sweep as though this run
        had produced it, and a stale ``policy.json`` — which only a failed run
        can leave — would start the agent with something already remembered.
        Only files this suite owns are removed, and only before the agent is
        started.
        """
        for path in (self.policy_path, self.audit_path):
            with contextlib.suppress(OSError):
                path.unlink()
        self.control.discard_records()
        self.control.fail_closed()
        if self.workspace.exists():
            for child in self.workspace.iterdir():
                with contextlib.suppress(OSError):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> "DesktopAgent":
        if self._process is not None:
            return self
        self.reset()
        self.write_config()
        env = dict(os.environ)
        env.update(
            {
                "JARVIS_TOKEN": self.token,
                "JARVIS_STATE_DIR": str(self.state_dir),
                "JARVIS_DEVICE_ID": self.device_id,
                "JARVIS_DEVICE_NAME": self.device_name,
                "JARVIS_E2E_CONTROL": str(self.control.path),
                "PYTHONUNBUFFERED": "1",
                # The agent must not inherit the harness's token file or any
                # ambient config from the box it is running on.
                "PYTHONPATH": os.pathsep.join(
                    [str(AGENT_DIR), env.get("PYTHONPATH", "")]
                ).rstrip(os.pathsep),
                # Presence reports `screen_on` from whether a graphical session
                # is attached. CI has none, and a device with the screen off is
                # ranked BACKGROUND, which is never sent a question — so the
                # variable is set to give the agent something to report. It is
                # not used for anything else: the two backends that would draw
                # on it are replaced by the runner.
                "DISPLAY": env.get("DISPLAY") or ":99",
            }
        )
        env.pop("JARVIS_TOKEN_FILE", None)
        # `http_request` builds a urllib opener, which picks up `http_proxy`
        # from the environment. On a runner behind a proxy that would send the
        # loopback fetch of the harness — the negative control for the SSRF
        # tests — through it and fail for a reason that has nothing to do with
        # this agent. Existing entries are kept.
        no_proxy = ",".join(
            part
            for part in (env.get("no_proxy") or env.get("NO_PROXY") or "", "localhost,127.0.0.1,::1")
            if part
        )
        env["no_proxy"] = no_proxy
        env["NO_PROXY"] = no_proxy
        argv = [
            self.python,
            "-u",
            str(RUNNER),
            "run",
            "--server",
            self.server_url,
            "--config",
            str(self.config_path),
            "--workspace",
            str(self.workspace),
            "-v",
        ]
        self._log = self.log_path.open("w", encoding="utf-8")
        # Its own process group on POSIX, so a child that ignores SIGTERM can
        # still be cleaned up by group. The argument is POSIX-only.
        extra = {"start_new_session": True} if os.name == "posix" else {}
        self._process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(AGENT_DIR),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            **extra,
        )
        return self

    def dead_reason(self) -> str | None:
        """A sentence for a wait to abandon on, or None while it is running."""
        if self._process is None:
            return "the agent was never started"
        code = self._process.poll()
        if code is None:
            return None
        return f"the agent exited with status {code}\n{self.log_tail()}"

    def log_tail(self, lines: int = 40) -> str:
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(no agent log)"
        tail = content.splitlines()[-lines:]
        return "----- agent.log (last {} lines) -----\n{}".format(len(tail), "\n".join(tail))

    def stop(self, timeout: float = 10.0) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            # The agent installs SIGTERM handlers and shuts the channel down
            # cleanly; the group kill is only for a child that ignores that.
            with contextlib.suppress(Exception):
                process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    if os.name == "posix":
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                with contextlib.suppress(Exception):
                    process.wait(timeout=5)
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log.close()
            self._log = None

    # --- what the server can see about it ---------------------------------
    def registration(self, base_url: str) -> dict[str, Any] | None:
        """This agent's entry in ``device_control.list_devices``, or None."""
        try:
            response = service_call(base_url, self.token, "device_control", "list_devices")
        except (urllib.error.URLError, OSError, ValueError):
            return None
        for device in response.get("devices", []) or []:
            if device.get("device_id") == self.device_id:
                return device
        return None

    def presence(self, base_url: str, need: str = "notify") -> dict[str, Any]:
        """``{"device": <this agent's presence>, "route": <where a message goes>}``."""
        try:
            report = service_call(
                base_url, self.token, "companion", "presence", {"need": need}
            )
        except (urllib.error.URLError, OSError, ValueError):
            return {}
        mine = next(
            (d for d in report.get("devices", []) or [] if d.get("device_id") == self.device_id),
            None,
        )
        return {"device": mine, "route": report.get("route") or {}, "raw": report}

    def wait_registered(self, base_url: str, timeout: float = 90.0) -> dict[str, Any]:
        return wait_until(
            lambda: self.registration(base_url),
            timeout,
            f"the agent to register as {self.device_id!r}",
            on_dead=self.dead_reason,
        )

    def wait_present(self, base_url: str, timeout: float = 60.0) -> dict[str, Any]:
        """Wait for a presence *event* to have been applied on the server.

        ``screen_on`` is False on a freshly registered ``DevicePresence``, so
        seeing it true means a ``device_event``/``presence`` frame arrived and
        was folded in — which is the thing worth waiting for before asking the
        server to route a question here.
        """

        def check() -> dict[str, Any] | None:
            snapshot = self.presence(base_url)
            device = snapshot.get("device")
            if device and device.get("screen_on") and device.get("connected"):
                return snapshot
            return None

        return wait_until(
            check,
            timeout,
            "a presence report from the agent to reach the server",
            on_dead=self.dead_reason,
        )
