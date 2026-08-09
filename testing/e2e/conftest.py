"""Fixtures for the end-to-end suites.

One harness is booted for the whole session — starting a real jarvis-core per
test would triple the runtime for no extra coverage — and each test gets a
fresh websocket client on it. Anything a test changes about the house it puts
back, or asserts on an entity nobody else touches.

The harness fixture is deliberately *synchronous*: it supervises subprocesses,
not coroutines, so it does not have to share an event loop with the async
tests that use it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from testing.harness import Harness, JarvisClient  # noqa: E402

#: Set JARVIS_HARNESS_KEEP=1 to leave the work directory (logs, received
#: audio, the generated config) behind for inspection.
KEEP = os.environ.get("JARVIS_HARNESS_KEEP", "").lower() in {"1", "true", "yes"}
VERBOSE = os.environ.get("JARVIS_HARNESS_VERBOSE", "").lower() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: drives a real jarvis-core through the harness")


@pytest.fixture(scope="session")
def harness():
    """The real jarvis-core, against the fakes, for the whole session."""
    work_dir = os.environ.get("JARVIS_HARNESS_WORK_DIR")
    instance = Harness(work_dir=work_dir, keep=KEEP, verbose=VERBOSE)
    try:
        instance.start()
    except Exception as err:  # pragma: no cover - a boot failure must be readable
        pytest.fail(f"could not start the harness: {err}", pytrace=False)
    yield instance
    instance.stop()


@pytest.fixture
async def client(harness):
    """A websocket+REST client, already authenticated."""
    harness.check_alive()
    connection = JarvisClient(harness.base_url, harness.token)
    try:
        await connection.connect()
        yield connection
    finally:
        await connection.aclose()


@pytest.fixture
def anonymous(harness):
    """A client holding a token the server has never heard of."""
    return JarvisClient(harness.base_url, "not-a-real-token")


@pytest.fixture
def spare_work_dir(tmp_path, request):
    """Where a test that boots its *own* harness should put it.

    CI uploads whatever is under `JARVIS_HARNESS_WORK_DIR`'s parent, so a
    second harness left in pytest's `tmp_path` would write its config, its
    logs and its audio somewhere nobody collects — and its failure would be the
    one thing in the suite you could not read afterwards.
    """
    configured = os.environ.get("JARVIS_HARNESS_WORK_DIR")
    if not configured:
        return tmp_path
    name = "".join(char if char.isalnum() or char in "-_" else "-"
                   for char in request.node.name)[:80]
    path = Path(configured).parent / "extra" / name
    path.mkdir(parents=True, exist_ok=True)
    return path
