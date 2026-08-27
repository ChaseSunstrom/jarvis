"""The loopback socket the desktop shell answers consent prompts over.

What is pinned here is the direction every failure has to fall. A socket that
can approve an action must refuse when the token is wrong, when the shell
disappears mid-question, when nobody answers, and when a second answer arrives
for a question that has already been answered — and it must say *unattended*
rather than *denied* when there was no shell at all, because "the user refused"
is a lie the server should not be told.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis_desktop.consent import (
    ApprovalRequest,
    ApprovalVerdict,
    ShellConsentGateway,
)
from jarvis_desktop.ipc import IpcServer, read_token, write_token
from jarvis_desktop.policy import ActionTier

pytestmark = pytest.mark.asyncio


class Shell:
    """The Electron side, as a test can drive it."""

    def __init__(self, reader, writer) -> None:
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, server: IpcServer, token: str | None = None) -> "Shell":
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        shell = cls(reader, writer)
        await shell.send({"token": token if token is not None else server.token})
        return shell

    async def send(self, frame: dict) -> None:
        self.writer.write((json.dumps(frame) + "\n").encode())
        await self.writer.drain()

    async def next(self, timeout: float = 2.0) -> dict:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        return json.loads(line) if line else {}

    async def close(self) -> None:
        self.writer.close()


def a_request() -> ApprovalRequest:
    return ApprovalRequest(
        action_id="open_url",
        description="Open a link",
        params={"url": "https://example.invalid/thing"},
        tier=ActionTier.CONFIRM,
        reason="the model asked",
    )


@pytest.fixture
async def server():
    server = IpcServer()
    await server.start()
    yield server
    await server.stop()


async def test_a_shell_that_authenticates_gets_the_current_status(server):
    await server.publish({"state": "listening", "detail": "wake word armed"})
    shell = await Shell.connect(server)
    frame = await shell.next()
    assert frame["type"] == "status"
    assert frame["state"] == "listening"
    await shell.close()


async def test_the_wrong_token_is_refused(server):
    shell = await Shell.connect(server, token="not-the-token")
    # The server closes rather than answering: `readline` returns b"" at a
    # clean end of stream, which `Shell.next` reports as an empty frame.
    assert await shell.next(timeout=1.0) == {}
    # And the connection is not counted, so a question reports `unattended`
    # rather than waiting on somebody who is not there.
    assert server.connected is False
    assert await server.ask({"action_id": "x"}, timeout=0.2) == "unattended"


async def test_a_question_reaches_the_shell_and_its_answer_comes_back(server):
    shell = await Shell.connect(server)
    await shell.next()  # the status frame

    asked = asyncio.ensure_future(server.ask({"action_id": "open_url"}, timeout=2))
    question = await shell.next()
    assert question["type"] == "ask"
    assert question["action_id"] == "open_url"

    await shell.send({"type": "answer", "id": question["id"], "verdict": "approved"})
    assert await asked == "approved"
    await shell.close()


async def test_nobody_answering_is_a_timeout_not_an_approval(server):
    shell = await Shell.connect(server)
    await shell.next()
    assert await server.ask({"action_id": "x"}, timeout=0.2) == "timeout"
    await shell.close()


async def test_a_shell_that_disappears_mid_question_denies_it(server):
    shell = await Shell.connect(server)
    await shell.next()
    asked = asyncio.ensure_future(server.ask({"action_id": "x"}, timeout=5))
    await shell.next()
    await shell.close()
    assert await asyncio.wait_for(asked, 2) == "unattended"


async def test_an_answer_to_an_unknown_question_is_ignored(server):
    """Single use. A second answer would be approving something twice."""
    shell = await Shell.connect(server)
    await shell.next()
    asked = asyncio.ensure_future(server.ask({"action_id": "x"}, timeout=2))
    question = await shell.next()
    await shell.send({"type": "answer", "id": question["id"], "verdict": "approved"})
    assert await asked == "approved"
    # And again, for the same id.
    await shell.send({"type": "answer", "id": question["id"], "verdict": "approved"})
    await asyncio.sleep(0.05)   # nothing to assert but that it did not explode
    await shell.close()


async def test_with_no_shell_connected_a_question_is_unattended(server):
    assert await server.ask({"action_id": "x"}, timeout=0.2) == "unattended"


# --- the gateway on top of it -------------------------------------------------


async def test_the_gateway_is_unusable_while_no_shell_is_connected(server):
    gateway = ShellConsentGateway(server)
    assert gateway.usable() is False
    assert gateway.unattended is True
    # And it refuses rather than hanging.
    assert await gateway.request(a_request()) is ApprovalVerdict.DENIED


async def test_the_gateway_carries_the_parameters_verbatim(server):
    shell = await Shell.connect(server)
    await shell.next()
    gateway = ShellConsentGateway(server, timeout=2)
    asked = asyncio.ensure_future(gateway.request(a_request()))

    question = await shell.next()
    assert question["params"] == {"url": "https://example.invalid/thing"}
    assert question["description"] == "Open a link"
    await shell.send({"type": "answer", "id": question["id"], "verdict": "approved"})
    assert await asked is ApprovalVerdict.APPROVED
    await shell.close()


async def test_an_unrecognised_answer_is_a_denial(server):
    shell = await Shell.connect(server)
    await shell.next()
    gateway = ShellConsentGateway(server, timeout=2)
    asked = asyncio.ensure_future(gateway.request(a_request()))
    question = await shell.next()
    await shell.send({"type": "answer", "id": question["id"], "verdict": "sure why not"})
    assert await asked is ApprovalVerdict.DENIED
    await shell.close()


async def test_answering_counts_as_being_at_the_machine(server):
    """The presence hook: a person who answers a prompt is not idle."""
    shell = await Shell.connect(server)
    await shell.next()
    seen: list[bool] = []
    gateway = ShellConsentGateway(server, timeout=2)
    gateway.on_interaction = lambda: seen.append(True)
    asked = asyncio.ensure_future(gateway.request(a_request()))
    question = await shell.next()
    await shell.send({"type": "answer", "id": question["id"], "verdict": "approved"})
    await asked
    assert seen == [True]
    await shell.close()


# --- the token ---------------------------------------------------------------


def test_the_token_is_written_once_and_reused(tmp_path):
    first = write_token(tmp_path)
    assert first
    assert write_token(tmp_path) == first
    assert read_token(tmp_path) == first


def test_a_missing_token_reads_as_empty(tmp_path):
    assert read_token(tmp_path / "nowhere") == ""
