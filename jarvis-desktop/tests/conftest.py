"""Shared fixtures. Nothing here touches the network, the display or a real
machine — every test builds its own workspace under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis_desktop.actions.base import Action, ActionContext, ActionResult
from jarvis_desktop.actions.paths import PathScope
from jarvis_desktop.actions.registry import ActionRegistry
from jarvis_desktop.audit import AuditLog
from jarvis_desktop.channel import Transport, TransportClosed
from jarvis_desktop.config import Config, InputConfig, ShellConfig
from jarvis_desktop.consent import ApprovalVerdict, ConsentGateway
from jarvis_desktop.policy import ActionTier, PolicyStore


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture()
def config(tmp_path, workspace):
    return Config(
        server_url="ws://jarvis.lan:8080/api/websocket",
        token="test-token",
        device_id="desktop-test",
        device_name="test-machine",
        state_dir=tmp_path / "state",
        file_roots=(workspace,),
        shell=ShellConfig(enabled=True, use_shell=False, timeout_s=5.0),
        input_automation=InputConfig(enabled=False),
        consent_timeout_s=1.0,
    ).ensure_dirs()


@pytest.fixture()
def ctx(config, workspace):
    return ActionContext(
        config=config, scope=PathScope([workspace]), allowed_hosts=(config.server_host,)
    )


@pytest.fixture()
def policy(config):
    return PolicyStore(config.policy_path)


@pytest.fixture()
def audit(config):
    return AuditLog(config.audit_path)


class RecordingAction(Action):
    """A test action that remembers every time it actually ran.

    The whole point of several tests is that this list stays empty.
    """

    def __init__(
        self,
        action_id: str,
        tier: ActionTier,
        result: ActionResult | None = None,
        capability: str = "test",
    ) -> None:
        self.id = action_id
        self.tier = tier
        self.description = f"test action {action_id}"
        self.params_schema = {"value": "string: anything"}
        self.capability = capability
        self.timeout_s = 5.0
        self.calls: list[dict[str, Any]] = []
        self._result = result

    def run(self, ctx: ActionContext, params: dict) -> ActionResult:
        self.calls.append(dict(params))
        return self._result or ActionResult.success(echoed=params.get("value"))


class SlowAction(RecordingAction):
    def run(self, ctx: ActionContext, params: dict) -> ActionResult:
        import time

        self.calls.append(dict(params))
        time.sleep(float(params.get("sleep_s", 0.05)))
        return ActionResult.success(slept=True)


@dataclass
class ScriptedConsent(ConsentGateway):
    """Answers from a queue and records what it was shown."""

    answers: list[ApprovalVerdict] = field(default_factory=list)
    default: ApprovalVerdict = ApprovalVerdict.DENIED
    seen: list[Any] = field(default_factory=list)
    name: str = "scripted"

    async def request(self, request):
        self.seen.append(request)
        return self.answers.pop(0) if self.answers else self.default


class FakeTransport(Transport):
    """A websocket that is a list of strings.

    ``inbound`` is what the "server" says, in order; ``sent`` is everything the
    agent wrote. When the script runs out, :meth:`recv` raises
    :class:`TransportClosed`, which is exactly what a closed socket does.
    """

    def __init__(self, inbound: list[str] | None = None) -> None:
        self.inbound: asyncio.Queue[str | None] = asyncio.Queue()
        for frame in inbound or []:
            self.inbound.put_nowait(frame)
        self.sent: list[dict] = []
        self.raw_sent: list[str] = []
        self.closed = False

    # -- the Transport contract --

    async def send(self, message: str) -> None:
        if self.closed:
            raise TransportClosed("sent on a closed transport")
        import json

        self.raw_sent.append(message)
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        frame = await self.inbound.get()
        if frame is None:
            raise TransportClosed("server closed the connection")
        return frame

    async def close(self) -> None:
        self.closed = True

    # -- test helpers --

    def push(self, frame: dict | str) -> None:
        import json

        self.inbound.put_nowait(frame if isinstance(frame, str) else json.dumps(frame))

    def finish(self) -> None:
        """Tell the agent the socket closed, ending its read loop."""
        self.inbound.put_nowait(None)

    def of_type(self, kind: str) -> list[dict]:
        return [f for f in self.sent if f.get("type") == kind]

    def results(self) -> list[dict]:
        return self.of_type("device_result")

    def result_for(self, command_id: str) -> dict | None:
        for frame in self.results():
            if frame.get("command_id") == command_id:
                return frame
        return None


@pytest.fixture()
def make_registry(ctx, policy, audit):
    """Build a registry over a set of test actions and a scripted consent."""

    def _make(actions, consent=None):
        registry = ActionRegistry(ctx, policy=policy, audit=audit, consent=consent)
        registry.register_all(actions)
        return registry

    return _make
