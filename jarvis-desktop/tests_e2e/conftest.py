"""Fixtures for the desktop end-to-end suite.

One real jarvis-core (via the shared harness in ``testing/harness``), one real
``jarvis_desktop`` process, one TCP proxy between them, for the whole session.
Booting either per test would multiply the runtime without adding coverage —
what each test needs is a *clean starting point*, which it gets by owning its
own file in the workspace and its own slice of the prompt log.

Everything here tears down in a ``finally``, including when a test fails or the
whole session is interrupted, so a red run does not leave a jarvis-core, a fake
Ollama, a fake Wyoming stack and an agent behind.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
REPO_ROOT = AGENT_DIR.parent
# ``AGENT_DIR`` matters when pytest was invoked from the repo root and
# jarvis-desktop is not pip-installed: ``testing.harness`` comes from
# REPO_ROOT, ``support`` from HERE, and ``jarvis_desktop`` itself from here.
# CI installs the package, so without this the suite works there and fails on a
# developer's machine for a reason that has nothing to do with the agent.
for entry in (str(REPO_ROOT), str(AGENT_DIR), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from support import DEVICE_ID, DEVICE_NAME, DesktopAgent, TcpProxy  # noqa: E402

#: The harness is written by another part of this repo. Until it lands, this
#: suite has nothing to boot the server with, so it skips with a sentence that
#: says exactly that rather than erroring out at import time.
HARNESS_ERROR: str | None = None
try:
    from testing.harness import Harness, JarvisClient  # noqa: E402
except Exception as err:  # noqa: BLE001 - any import failure is a skip, not a crash
    Harness = JarvisClient = None  # type: ignore[assignment,misc]
    HARNESS_ERROR = f"{type(err).__name__}: {err}"

KEEP = os.environ.get("JARVIS_HARNESS_KEEP", "").lower() in {"1", "true", "yes"}
VERBOSE = os.environ.get("JARVIS_HARNESS_VERBOSE", "").lower() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "e2e: drives the real desktop agent against a real jarvis-core"
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach both process logs to any failure.

    A red end-to-end test is nearly useless without them, and a CI job that
    fails to upload its artifacts should still leave enough in the console
    output to work out what happened.
    """
    report = yield
    if report.when == "call" and report.failed:
        args = getattr(item, "funcargs", {}) or {}
        agent = args.get("live") or args.get("agent")
        if agent is not None:
            with contextlib.suppress(Exception):
                report.sections.append(("desktop agent", agent.log_tail(60)))
        instance = args.get("harness")
        if instance is not None:
            with contextlib.suppress(Exception):
                report.sections.append(("jarvis-core", instance.logs(60)))
    return report


def _work_root() -> tuple[Path, bool]:
    """Where logs, state and workspaces go. ``(path, is_temporary)``.

    CI points ``JARVIS_HARNESS_WORK_DIR`` at an artifacts directory so a failed
    job uploads something a human can read. A sub-directory is used rather than
    the root itself, so this suite and ``testing/e2e`` can share the variable
    without overwriting each other's logs.
    """
    configured = os.environ.get("JARVIS_DESKTOP_E2E_WORK_DIR") or os.environ.get(
        "JARVIS_HARNESS_WORK_DIR"
    )
    if configured:
        root = Path(configured)
        if not os.environ.get("JARVIS_DESKTOP_E2E_WORK_DIR"):
            root = root / "desktop-agent"
        root.mkdir(parents=True, exist_ok=True)
        return root, False
    return Path(tempfile.mkdtemp(prefix="jarvis-desktop-e2e-")), True


#: The loopback spellings the harness and the proxy actually bind and dial.
#: ``[::1]`` is deliberately absent: httpx turns every ``no_proxy`` entry into
#: a URL pattern and rejects the bracketed form with ``InvalidURL: Invalid
#: port: ':1]'``, which would break every REST call instead of protecting it.
#: A bare ``::1`` parses, but nothing here binds v6, so neither is needed.
_LOOPBACK = ("localhost", "127.0.0.1")


@pytest.fixture(scope="session", autouse=True)
def loopback_never_goes_through_a_proxy():
    """Make sure nothing in this process tries to proxy 127.0.0.1.

    Everything here talks to a server on this machine, but three separate
    libraries decide that for themselves from the environment: ``httpx``
    (``trust_env`` is on by default) for the REST calls, ``websockets`` — which
    resolves a proxy for a ``ws://`` URI from the ``https_proxy`` entry and
    consults only ``urllib.request.proxy_bypass`` to opt out — and ``urllib``
    itself. On a runner behind a proxy whose ``no_proxy`` does not name
    loopback, every one of them would hand a connection meant for 127.0.0.1 to
    a proxy that cannot route it, and the symptom would be "the agent never
    registered" ninety seconds later rather than the cause.

    Only the loopback names are added, and only for this process; an existing
    ``no_proxy`` is preserved. The agent subprocess sets its own — see
    :meth:`DesktopAgent.start`.
    """
    previous = {name: os.environ.get(name) for name in ("no_proxy", "NO_PROXY")}
    existing = [
        part.strip()
        for part in (previous["no_proxy"] or previous["NO_PROXY"] or "").split(",")
        if part.strip()
    ]
    merged = ",".join(existing + [h for h in _LOOPBACK if h not in existing])
    os.environ["no_proxy"] = merged
    os.environ["NO_PROXY"] = merged
    try:
        yield merged
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="session")
def work_root():
    root, temporary = _work_root()
    yield root
    if temporary and not KEEP:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def harness(work_root, loopback_never_goes_through_a_proxy):
    """The real jarvis-core, against the fake model and voice backends."""
    if Harness is None:
        pytest.skip(
            "the shared end-to-end harness (testing/harness) is not importable yet "
            f"({HARNESS_ERROR}). This suite boots the real jarvis-core through it; "
            "install its requirements with `pip install -r jarvis-core/requirements.txt "
            "-r testing/requirements.txt` and re-run."
        )
    instance = Harness(work_dir=str(work_root / "harness"), keep=True, verbose=VERBOSE)
    try:
        instance.start()
    except Exception as err:  # pragma: no cover - a boot failure must be readable
        pytest.fail(f"could not start the harness: {err}", pytrace=False)
    try:
        yield instance
    finally:
        instance.stop(cleanup=False)


@pytest.fixture(scope="session")
def proxy(harness):
    """The agent's socket, in the test's hands.

    The agent dials this instead of the harness so a test can cut a live
    connection and watch the real reconnect path run. It is a byte-for-byte
    relay: nothing about the protocol is interpreted here.
    """
    relay = TcpProxy(harness.client_host, harness.port).start()
    try:
        yield relay
    finally:
        relay.stop()


@pytest.fixture(scope="session")
def agent(harness, proxy, work_root):
    """The real desktop agent, registered and reporting presence."""
    instance = DesktopAgent(
        server_url=f"ws://127.0.0.1:{proxy.port}",
        token=harness.token,
        work_dir=work_root / "agent",
        device_id=DEVICE_ID,
        device_name=DEVICE_NAME,
    )
    instance.start()
    try:
        instance.wait_registered(harness.base_url)
        instance.wait_present(harness.base_url)
        yield instance
    finally:
        instance.stop()


@pytest.fixture(autouse=True)
def fail_closed(request):
    """Every test starts with the answer set to "no", and nothing remembered.

    The control files and the policy store outlive a test, so without this the
    verdict one test set decides what the next one measures — and a test that
    fails half way through, having granted approval, arms the rest of the file.
    Autouse rather than opt-in precisely because the tests that most need it
    are the ones that never mention consent at all (the path-escape cases, the
    reconnect case): those must be refused by a guard, and there must be no way
    for an inherited approval to be the reason they pass.

    It runs only when a test actually uses the agent, so a session with no
    harness still skips instead of erroring. It deliberately does NOT touch the
    policy store: "nothing was persisted all session" is an assertion the
    closing sweep makes about the whole run, and a fixture that quietly deleted
    the file each time would make it unfalsifiable. The one test that writes a
    policy on purpose puts it back itself.
    """
    if "live" not in request.fixturenames and "agent" not in request.fixturenames:
        yield
        return
    instance = request.getfixturevalue("agent")
    instance.control.fail_closed()
    yield
    instance.control.fail_closed()


@pytest.fixture
def live(agent, harness):
    """Fail fast, with the log, if the agent or the server died earlier."""
    dead = agent.dead_reason()
    if dead:
        pytest.fail(f"the desktop agent is not running: {dead}", pytrace=False)
    try:
        harness.check_alive()
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"the harness is no longer healthy: {err}", pytrace=False)
    return agent


@pytest.fixture
async def client(harness):
    """A websocket+REST client on the server, already authenticated.

    Function-scoped: a test that leaves a subscription behind, or one whose
    socket the server closes, must not be able to break the next one.
    """
    connection = JarvisClient(harness.base_url, harness.token)
    try:
        await connection.connect()
        yield connection
    finally:
        await connection.aclose()
