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

# NOT imported at module scope.
#
# A conftest runs for every test collected under it, including the ones that
# want nothing from the harness. `test_ci_workflow_contract.py` is the case
# that matters: it parses the workflow files and shells out to bash, needs only
# pyyaml, runs in under a second, and is the first thing CI should be able to
# run — and importing the harness here dragged in `httpx` and `websockets`
# before collection could even start, so it could not run in a job that had not
# installed the whole end-to-end stack. It did not, and CI failed with
# `ModuleNotFoundError: No module named 'httpx'` on a test that does not use it.
#
# Imported inside the fixtures instead, so the cost falls on the tests that
# actually ask for a harness.
def _harness_types():
    from testing.harness import Harness, JarvisClient

    return Harness, JarvisClient


#: Set JARVIS_HARNESS_KEEP=1 to leave the work directory (logs, received
#: audio, the generated config) behind for inspection.
KEEP = os.environ.get("JARVIS_HARNESS_KEEP", "").lower() in {"1", "true", "yes"}
VERBOSE = os.environ.get("JARVIS_HARNESS_VERBOSE", "").lower() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: drives a real jarvis-core through the harness")


@pytest.fixture(scope="session")
def harness():
    """The real jarvis-core, against the fakes, for the whole session."""
    Harness, _ = _harness_types()
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
    _, JarvisClient = _harness_types()
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
    _, JarvisClient = _harness_types()
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
