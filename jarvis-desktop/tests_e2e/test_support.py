"""The suite's own plumbing, checked without a server.

Everything in ``test_desktop_e2e.py`` reads its evidence through the helpers in
``support.py``: the prompt log, the audit log, the policy file, the REST call
that asks the server what it can see. A bug in any of those is invisible from
inside the end-to-end tests — a reader pointed at the wrong path returns an
empty list, and an empty list is what "nothing happened" looks like. So the
helpers are checked here, against real files and real sockets, with no
jarvis-core involved.

These skip alongside the rest of the suite rather than on their own merits.
They do not need a harness, but the CI job asserts that a run in which
*everything* skipped is a failure — that is how a missing harness is caught —
and a handful of always-green tests in the same JUnit file would quietly defeat
it.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import HARNESS_ERROR
from support import ControlDir, DesktopAgent, TcpProxy, TimedOut, service_call, wait_until

pytestmark = pytest.mark.skipif(
    HARNESS_ERROR is not None,
    reason=f"the shared end-to-end harness is not importable ({HARNESS_ERROR})",
)


def make_agent(tmp_path) -> DesktopAgent:
    """A ``DesktopAgent`` object that is never started. Only its files matter."""
    return DesktopAgent(
        server_url="ws://127.0.0.1:1",
        token="not-used",
        work_dir=tmp_path / "agent",
        device_id="plumbing",
        device_name="Plumbing",
    )


# ---------------------------------------------------------------------------
# the control directory
# ---------------------------------------------------------------------------
def test_the_control_directory_starts_and_returns_to_fail_closed(tmp_path):
    control = ControlDir(tmp_path / "control")
    assert json.loads(control.consent_path.read_text())["verdict"] == "denied"
    assert json.loads(control.answer_path.read_text())["status"] == "dismissed"

    control.set_consent("approved_always")
    control.set_answer("answered", "yes")
    assert json.loads(control.consent_path.read_text())["verdict"] == "approved_always"

    control.fail_closed()
    assert json.loads(control.consent_path.read_text())["verdict"] == "denied"
    assert json.loads(control.answer_path.read_text())["status"] == "dismissed"


def test_the_prompt_log_survives_a_partial_line(tmp_path):
    """The agent appends to this file from another process while it is read."""
    control = ControlDir(tmp_path / "control")
    control.prompts_path.write_text(
        '{"seq": 1, "action_id": "delete_file"}\n{"seq": 2, "action_i',
        encoding="utf-8",
    )
    prompts = control.prompts()
    assert [p["seq"] for p in prompts] == [1], prompts

    control.discard_records()
    assert control.prompts() == []
    assert not control.prompts_path.exists()


# ---------------------------------------------------------------------------
# the agent's own files
# ---------------------------------------------------------------------------
def test_reset_removes_what_an_earlier_run_left_behind(tmp_path):
    """CI points the work directory at the checkout, and a developer re-runs
    into the same one. Without this the closing sweep reads another run's
    audit log and the agent starts with something already remembered."""
    agent = make_agent(tmp_path)
    agent.audit_path.write_text('{"action": "run_command", "ok": true}\n', encoding="utf-8")
    agent.write_policy({"delete_file": "allow_always"})
    agent.control.set_consent("approved")
    agent.control.prompts_path.write_text('{"seq": 99}\n', encoding="utf-8")
    stale = agent.workspace_file("left-over.txt", "old\n")
    nested = agent.workspace / "sub" / "deep.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("old\n", encoding="utf-8")

    assert agent.audit() and agent.remembered() and agent.control.prompts()

    agent.reset()

    assert agent.audit() == []
    assert not agent.policy_path.exists()
    assert agent.remembered() == {}
    assert agent.control.prompts() == []
    assert json.loads(agent.control.consent_path.read_text())["verdict"] == "denied"
    assert not stale.exists()
    assert not nested.exists() and not nested.parent.exists()
    assert agent.workspace.is_dir(), "reset removed the workspace itself"


def test_the_policy_file_the_suite_writes_is_one_the_agent_can_read(tmp_path):
    """``write_policy`` exists so a test can flip the user's kill switches on a
    *running* agent. That is worthless if the file it writes is not the shape
    the shipping store parses, so it is parsed here by the shipping store."""
    from jarvis_desktop.policy import ActionTier, PolicyStore, UserPolicy

    agent = make_agent(tmp_path)
    agent.write_policy({"write_file": "allow_always", "delete_file": "never"}, panic=True)

    store = PolicyStore(agent.policy_path)
    assert store.policy_for("write_file") == UserPolicy.ALLOW_ALWAYS
    assert store.policy_for("delete_file") == UserPolicy.NEVER
    assert store.panic is True
    assert store.automation_enabled is True

    # And the other direction: what the store writes is what the suite reads.
    agent.forget_policy()
    assert agent.remembered() == {}
    fresh = PolicyStore(agent.policy_path)
    fresh.set_policy("write_file", UserPolicy.ALLOW_ALWAYS, ActionTier.NOTIFY)
    assert agent.remembered() == {"write_file": "allow_always"}
    assert agent.policy_path.exists()

    # The guard the end-to-end suite relies on, stated at the storage layer.
    fresh.set_policy("delete_file", UserPolicy.ALLOW_ALWAYS, ActionTier.CONFIRM)
    assert "delete_file" not in agent.remembered(), agent.policy()


# ---------------------------------------------------------------------------
# talking to the server
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps(
            {"service_response": {"path": self.path, "auth": self.headers.get("Authorization")}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def rest_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    # `shutdown()` waits out one poll interval, and the default half second is
    # most of this file's runtime.
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_session_puts_loopback_beyond_any_proxy():
    """``httpx`` and ``websockets`` decide this from the environment themselves,
    and neither is reachable from ``service_call``'s opener. The session fixture
    is the only thing covering them, so its effect is asserted rather than
    assumed — and asserted through ``proxy_bypass``, which is the function
    ``websockets`` actually calls before dialling a ``ws://`` URL."""
    import urllib.request

    for name in ("no_proxy", "NO_PROXY"):
        value = os.environ.get(name, "")
        assert "127.0.0.1" in value, (name, value)
        assert "localhost" in value, (name, value)
    assert urllib.request.proxy_bypass("127.0.0.1:8080")
    assert urllib.request.proxy_bypass("localhost:8080")


def test_service_call_ignores_a_proxy_that_cannot_route_loopback(
    rest_server, monkeypatch
):
    """A runner behind a proxy must not send the fixtures' REST calls to it.

    ``no_proxy`` is cleared here on purpose: with it set (which the session
    fixture arranges, and which this container happens to have anyway) the call
    would succeed whether or not ``service_call`` disabled proxy lookup, and
    the test would prove nothing. The blackhole is asserted to be effective
    first, so the second half cannot pass by accident.
    """
    import urllib.error
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{dead}")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{dead}")
    # ``urlopen`` builds its opener once and keeps it for the life of the
    # process, and a ``ProxyHandler`` only grows an ``http_open`` method if an
    # http proxy existed when it was built. Something earlier in the session
    # will already have built one — so without clearing the cache the control
    # below would sail past the proxy and this test would prove nothing.
    monkeypatch.setattr(urllib.request, "_opener", None, raising=False)

    # Control: anything that honours the environment cannot get through. It has
    # to fail at the *transport*, not with an HTTPError from the real server —
    # HTTPError is a URLError subclass, so a bare `raises(URLError)` would be
    # satisfied by a plain 501 and this whole test would be theatre.
    with pytest.raises(urllib.error.URLError) as blocked:
        urllib.request.urlopen(f"{rest_server}/healthz", timeout=5)  # noqa: S310
    assert not isinstance(blocked.value, urllib.error.HTTPError), blocked.value
    assert "Connection refused" in str(blocked.value), blocked.value

    body = service_call(rest_server, "tok", "device_control", "list_devices", timeout=10)
    assert body["path"] == "/api/services/device_control/list_devices?return_response=true"
    assert body["auth"] == "Bearer tok"


# ---------------------------------------------------------------------------
# the socket the test can cut
# ---------------------------------------------------------------------------
@pytest.fixture
def echo_server():
    """A trivial TCP echo, so the relay can be checked byte for byte."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    # A blocking accept() is not reliably woken by close() on Linux, and the
    # teardown would then wait out its whole join timeout on every test.
    listener.settimeout(0.05)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=pump, args=(conn,), daemon=True).start()

    def pump(conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                try:
                    conn.sendall(chunk)
                except OSError:
                    return

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=5)


def test_the_relay_passes_bytes_through_and_can_cut_a_live_connection(echo_server):
    host, port = echo_server
    relay = TcpProxy(host, port).start()
    try:
        with socket.create_connection(("127.0.0.1", relay.port), timeout=10) as client:
            client.sendall(b"hello jarvis\n")
            assert client.recv(64) == b"hello jarvis\n"
            wait_until(lambda: relay.live == 1, 10, "the relay to register the pair")
            assert relay.established == 1

            assert relay.drop_all() == 1
            client.settimeout(10)
            # An aborted connection is either an empty read or a reset; both
            # mean the same thing to the agent, and both are what a dying
            # network looks like.
            try:
                assert client.recv(64) == b""
            except OSError:
                pass
        assert relay.live == 0
    finally:
        relay.stop()


def test_a_blocked_relay_refuses_new_connections_and_counts_them(echo_server):
    """``block()`` is what holds the disconnect gap open long enough to assert
    on it, so "it really refused" has to be observable."""
    host, port = echo_server
    relay = TcpProxy(host, port).start()
    try:
        relay.block()
        with socket.create_connection(("127.0.0.1", relay.port), timeout=10) as client:
            client.settimeout(10)
            try:
                assert client.recv(16) == b""
            except OSError:
                pass
        wait_until(lambda: relay.refused == 1, 10, "the relay to count the refusal")
        assert relay.established == 0

        relay.unblock()
        with socket.create_connection(("127.0.0.1", relay.port), timeout=10) as client:
            client.sendall(b"back\n")
            assert client.recv(16) == b"back\n"
        assert relay.established == 1
        assert "refused=1" in relay.stats()
    finally:
        relay.stop()


# ---------------------------------------------------------------------------
# the waits
# ---------------------------------------------------------------------------
def test_wait_until_says_what_it_was_waiting_for_and_what_it_last_saw():
    with pytest.raises(TimedOut) as caught:
        wait_until(lambda: None, 0.05, "something that never happens")
    assert "something that never happens" in str(caught.value)
    assert "last saw" in str(caught.value)


def test_wait_until_gives_up_early_when_the_process_is_gone():
    """Otherwise a dead agent costs the whole timeout before anyone is told."""
    calls = []

    with pytest.raises(TimedOut) as caught:
        wait_until(
            lambda: calls.append(1),
            60.0,
            "a registration",
            on_dead=lambda: "the agent exited with status 2",
        )
    assert "the agent exited with status 2" in str(caught.value)
    assert not calls, "the check ran even though the process was already dead"


def test_wait_until_treats_a_raising_check_as_not_yet_rather_than_a_failure():
    state = {"n": 0}

    def check() -> bool:
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionRefusedError("not up yet")
        return True

    assert wait_until(check, 10, "a server that starts late", interval=0.01) is True
