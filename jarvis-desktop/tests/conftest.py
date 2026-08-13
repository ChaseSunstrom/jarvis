"""Shared fixtures. Nothing here touches the network, the display or a real
machine — every test builds its own workspace under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Callable

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


# --- a tkinter that is not tkinter ------------------------------------------
#
# Both dialogs — the consent prompt and the companion question — used to have no
# tests at all, because `_show` needs a display and CI has none, and because the
# module is not even importable on a box without `python3-tk` (this one). So
# every property of the most consequential window this agent draws was resting
# on somebody reading it: that closing it denies, that the "always" control is
# not drawn for Tier 3, that it is painted in the palette rather than in system
# grey.
#
# This is a stand-in with the handful of methods those two functions call. It is
# not a tkinter emulator and cannot catch a layout mistake; what it catches is
# which widgets are built, with which colours, wired to which answers.


class FakeTkWidget:
    """One widget, remembering how it was made and how it was configured."""

    def __init__(self, owner: "FakeTk", kind: str, parent: Any = None, **kwargs: Any) -> None:
        self.owner = owner
        self.kind = kind
        self.parent = parent
        self.kwargs: dict[str, Any] = dict(kwargs)
        self.window_title: str | None = None
        self.attrs: list[tuple] = []
        self.content = ""
        self.value = ""
        self.bindings: dict[str, Callable] = {}
        self.protocols: dict[str, Callable] = {}
        self.timers: list[tuple[int, Callable]] = []
        self.packed: dict[str, Any] | None = None
        self.destroyed = False

    # -- the bits of the Tk API the two dialogs use --

    def title(self, text: str) -> None:
        self.window_title = text

    def attributes(self, *args: Any) -> None:
        self.attrs.append(args)

    def configure(self, **kwargs: Any) -> None:
        self.kwargs.update(kwargs)

    def insert(self, _index: Any, text: str) -> None:
        self.content += text

    def get(self) -> str:
        return self.value

    def pack(self, **kwargs: Any) -> None:
        self.packed = kwargs

    def bind(self, sequence: str, fn: Callable) -> None:
        self.bindings[sequence] = fn

    def protocol(self, name: str, fn: Callable) -> None:
        self.protocols[name] = fn

    def after(self, delay_ms: int, fn: Callable) -> None:
        self.timers.append((delay_ms, fn))

    def mainloop(self) -> None:
        # Where the "user" acts. The script runs once; a script that answers
        # nothing is a window that closed without an answer, which is a real
        # case and must still produce a verdict.
        self.owner.script(self.owner)

    def quit(self) -> None:
        self.owner.quit_calls += 1

    def destroy(self) -> None:
        self.destroyed = True

    def lift(self) -> None:
        pass

    def focus_force(self) -> None:
        pass

    def focus_set(self) -> None:
        pass


class FakeTk:
    """A ``tkinter`` stand-in plus the helpers a test needs to drive it."""

    KINDS = ("Tk", "Label", "Text", "Frame", "Button", "Entry", "Message")

    def __init__(self) -> None:
        self.widgets: list[FakeTkWidget] = []
        self.root: FakeTkWidget | None = None
        self.quit_calls = 0
        #: What the "user" does once the dialog is up. Replaced per test.
        self.script: Callable[["FakeTk"], None] = lambda tk: None
        self.module = types.SimpleNamespace(
            **{kind: self._factory(kind) for kind in self.KINDS}
        )

    def _factory(self, kind: str) -> Callable[..., FakeTkWidget]:
        def make(*args: Any, **kwargs: Any) -> FakeTkWidget:
            widget = FakeTkWidget(self, kind, args[0] if args else None, **kwargs)
            self.widgets.append(widget)
            if kind == "Tk":
                self.root = widget
            return widget

        return make

    # -- reading what was drawn --

    def of_kind(self, kind: str) -> list[FakeTkWidget]:
        return [w for w in self.widgets if w.kind == kind]

    def button(self, text: str) -> FakeTkWidget | None:
        for widget in self.of_kind("Button"):
            if widget.kwargs.get("text") == text:
                return widget
        return None

    def button_labels(self) -> list[str]:
        return [str(w.kwargs.get("text")) for w in self.of_kind("Button")]

    def texts(self) -> list[str]:
        """Every string drawn anywhere, including the read-only slab."""
        out = [str(w.kwargs.get("text", "")) for w in self.widgets]
        out += [w.content for w in self.widgets if w.content]
        return [t for t in out if t]

    def colours(self) -> set[str]:
        """Every colour any widget was given, whatever the option was called."""
        found: set[str] = set()
        for widget in self.widgets:
            for value in widget.kwargs.values():
                if isinstance(value, str) and value.startswith("#"):
                    found.add(value.lower())
        return found

    # -- acting as the user --

    def click(self, text: str) -> None:
        widget = self.button(text)
        assert widget is not None, f"no button labelled {text!r}: {self.button_labels()}"
        widget.kwargs["command"]()

    def close_window(self) -> None:
        assert self.root is not None
        self.root.protocols["WM_DELETE_WINDOW"]()

    def fire_timers(self, rounds: int = 1000) -> None:
        """Let the clock run out: run every pending ``after`` callback, and
        anything those schedule in turn.

        Draining rather than firing once, because the two dialogs use ``after``
        differently — the consent prompt arms a single deadline, the companion
        question reschedules itself every second — and "the countdown expired"
        has to mean the same thing for both.
        """
        assert self.root is not None
        for _ in range(rounds):
            pending, self.root.timers = self.root.timers, []
            if not pending:
                return
            for _delay, fn in pending:
                fn()


@pytest.fixture()
def fake_tk(monkeypatch):
    """Put :class:`FakeTk` in ``sys.modules`` so ``import tkinter`` finds it."""
    fake = FakeTk()
    monkeypatch.setitem(sys.modules, "tkinter", fake.module)
    return fake


@pytest.fixture()
def make_registry(ctx, policy, audit):
    """Build a registry over a set of test actions and a scripted consent."""

    def _make(actions, consent=None):
        registry = ActionRegistry(ctx, policy=policy, audit=audit, consent=consent)
        registry.register_all(actions)
        return registry

    return _make
