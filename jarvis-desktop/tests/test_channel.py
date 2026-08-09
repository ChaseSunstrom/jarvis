"""The device protocol, over a fake websocket. No network, no sockets, no sleep.

What is being checked here is not "does JSON round trip" but the four rules that
make the channel safe to point at a server that may have been prompt-injected:

* the ``tier`` field can only RAISE,
* a denied command never reaches the action,
* a flood is refused and *answered*, not silently dropped,
* an unknown action is CONFIRM, not "unknown".
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from jarvis_desktop.channel import DeviceChannel, TransportClosed
from jarvis_desktop.consent import ApprovalVerdict
from jarvis_desktop.policy import ActionTier, UserPolicy

from .conftest import FakeTransport, RecordingAction, ScriptedConsent

pytestmark = pytest.mark.asyncio


def auth_ok() -> str:
    return json.dumps({"type": "auth_ok", "ha_version": "jarvis-0.1.0"})


def auth_required() -> str:
    return json.dumps({"type": "auth_required", "ha_version": "jarvis-0.1.0"})


def register_ok(request_id: int = 1) -> str:
    return json.dumps(
        {"id": request_id, "type": "result", "success": True, "result": {"ok": True}}
    )


def command(action: str, tier=None, command_id="c-1", **params) -> dict:
    frame = {
        "type": "device_command",
        "command_id": command_id,
        "action": action,
        "params": params,
        "reason": "because the model said so",
    }
    if tier is not None:
        frame["tier"] = tier
    return frame


async def run_session(
    channel: DeviceChannel, transport: FakeTransport, timeout: float = 10.0
) -> None:
    """Drive one session until everything queued has been handled.

    The fake socket does not close on its own, so this waits for the inbound
    queue to drain *and* every command task to finish, then closes it — which is
    what a real session does when the server has nothing more to say.
    """
    session = asyncio.create_task(channel.run_session(transport))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(0.005)
        if session.done():
            break
        if not transport.inbound.empty():
            continue
        if any(not task.done() for task in channel._tasks):
            continue
        break
    transport.finish()
    try:
        await asyncio.wait_for(session, timeout=timeout)
    except TransportClosed:
        pass
    except asyncio.TimeoutError:
        session.cancel()
        raise


@contextlib.asynccontextmanager
async def live_session(channel: DeviceChannel, transport: FakeTransport, timeout: float = 10.0):
    """Register, hand control to the test, then close the socket.

    Commands sent inside the block go one at a time via :func:`send`, so the
    concurrency gate never fires and the test is measuring the thing it says it
    is measuring.
    """
    session = asyncio.create_task(channel.run_session(transport))
    try:
        await asyncio.wait_for(channel.ready.wait(), timeout=timeout)
        yield
    finally:
        transport.finish()
        try:
            await asyncio.wait_for(session, timeout=timeout)
        except (TransportClosed, asyncio.TimeoutError, asyncio.CancelledError):
            session.cancel()


async def send(
    channel: DeviceChannel, transport: FakeTransport, frame: dict, timeout: float = 10.0
) -> dict:
    """Push one command and wait for its answer. Every command gets one."""
    wanted = len(transport.results()) + 1
    transport.push(frame)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while len(transport.results()) < wanted and loop.time() < deadline:
        await asyncio.sleep(0.002)
    assert len(transport.results()) >= wanted, f"no reply to {frame.get('command_id')}"
    return transport.results()[-1]


# --- registration -----------------------------------------------------------


async def test_registration_sends_the_expected_frames(config, make_registry):
    action = RecordingAction("get_system_state", ActionTier.AUTO, capability="system")
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_required(), auth_ok(), register_ok()])

    await run_session(channel, transport)

    auth = transport.of_type("auth")
    assert len(auth) == 1
    assert auth[0]["access_token"] == "test-token"

    register = transport.of_type("jarvis/device/register")
    assert len(register) == 1
    device = register[0]["device"]
    assert device["platform"] == "desktop"
    assert device["id"] == "desktop-test"
    assert device["name"] == "test-machine"
    assert device["capabilities"] == ["system"]
    assert device["app_version"]
    assert register[0]["id"] == 1
    # The action manifest rides inside `device` as an additive field.
    assert device["actions"][0]["id"] == "get_system_state"
    assert device["actions"][0]["tier"] == 1


async def test_the_token_is_only_sent_in_reply_to_auth_required(config, make_registry):
    """Volunteering a credential to whatever answered the socket is how tokens
    leak. A server that did not ask does not get one."""
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport([auth_ok(), register_ok()])
    await run_session(channel, transport)
    assert transport.of_type("auth") == []
    assert "test-token" not in "".join(transport.raw_sent)
    assert channel.registered is False or transport.of_type("jarvis/device/register")


async def test_a_rejected_token_ends_the_session_and_penalises_the_backoff(
    config, make_registry
):
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport([auth_required(), json.dumps({"type": "auth_invalid"})])
    with pytest.raises(TransportClosed):
        await channel.run_session(transport)
    assert channel.registered is False
    # Retrying a rejected token quickly just hammers the auth path.
    assert channel._backoff.attempt >= channel._backoff.PENALTY_ATTEMPT


async def test_a_refused_registration_ends_the_session(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport(
        [auth_ok(), json.dumps({"id": 1, "type": "result", "success": False, "error": {"code": "x"}})]
    )
    with pytest.raises(TransportClosed):
        await channel.run_session(transport)
    assert channel.registered is False


async def test_nothing_is_emitted_before_registration(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    assert await channel.emit_event("test", {}) is False


# --- command -> result ------------------------------------------------------


async def test_an_allowed_command_runs_and_answers(config, make_registry):
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("get_system_state", tier=1, value="hello"))

    await run_session(channel, transport)

    assert action.calls == [{"value": "hello"}]
    reply = transport.result_for("c-1")
    assert reply is not None
    assert reply["type"] == "device_result"
    assert reply["status"] == "ok"
    assert reply["result"]["echoed"] == "hello"


async def test_an_unknown_action_is_unsupported_not_executed(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("definitely_not_an_action", tier=1))

    await run_session(channel, transport)

    reply = transport.result_for("c-1")
    assert reply["status"] == "unsupported"
    assert "unknown action" in reply["error"]


async def test_an_unknown_action_is_treated_as_confirm_by_the_channel(config, make_registry):
    """Not "unknown", not "ask the server" — the most dangerous tier there is,
    so a typo or an injected action name cannot land in the auto-run bucket."""
    channel = DeviceChannel(config, make_registry([]))
    assert channel.effective_tier("who_knows", 1) == ActionTier.CONFIRM


async def test_a_command_with_no_command_id_is_dropped(config, make_registry):
    action = RecordingAction("noop", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push({"type": "device_command", "action": "noop", "params": {}})

    await run_session(channel, transport)

    assert action.calls == []
    assert transport.results() == []


async def test_an_unparseable_frame_is_ignored_not_fatal(config, make_registry):
    action = RecordingAction("noop", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push("{not json")
    transport.push("[]")
    transport.push(command("noop", tier=1))

    await run_session(channel, transport)

    assert len(action.calls) == 1


async def test_unknown_fields_on_a_command_change_nothing(config, make_registry):
    """A server that invents `skip_confirmation` is describing a field this
    parser does not have and will not grow."""
    action = RecordingAction("dangerous", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    frame = command("dangerous", tier=3)
    frame.update({"skip_confirmation": True, "policy": "allow", "auto_approve": True})
    transport.push(frame)

    await run_session(channel, transport)

    assert action.calls == []
    assert transport.result_for("c-1")["status"] == "denied"


# --- the tier field can only raise ------------------------------------------


async def test_the_server_cannot_lower_a_tier_three_action(config, make_registry):
    """The headline case. `run_command` is Tier 3 locally; the server claims
    Tier 1; a human is still asked, and this one says no."""
    action = RecordingAction("run_command", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("run_command", tier=1, value="rm -rf /"))

    await run_session(channel, transport)

    assert action.calls == [], "a Tier-3 action ran because the server said Tier 1"
    assert len(consent.seen) == 1
    assert consent.seen[0].tier == ActionTier.CONFIRM
    assert transport.result_for("c-1")["status"] == "denied"


async def test_the_server_can_raise_a_tier_one_action_into_a_prompt(config, make_registry):
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("get_system_state", tier=3))

    await run_session(channel, transport)

    assert action.calls == []
    assert consent.seen[0].tier == ActionTier.CONFIRM
    assert consent.seen[0].rememberable is False


@pytest.mark.parametrize("bad_tier", [0, 4, 99, -1, "one", None, True, [], {}])
async def test_a_garbage_tier_field_does_not_lower_anything(config, make_registry, bad_tier):
    action = RecordingAction("run_command", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("run_command", tier=bad_tier))

    await run_session(channel, transport)

    assert action.calls == []
    assert consent.seen[0].tier == ActionTier.CONFIRM


async def test_effective_tier_is_max_of_local_and_incoming(config, make_registry):
    registry = make_registry(
        [
            RecordingAction("auto_thing", ActionTier.AUTO),
            RecordingAction("notify_thing", ActionTier.NOTIFY),
            RecordingAction("confirm_thing", ActionTier.CONFIRM),
        ]
    )
    channel = DeviceChannel(config, registry)
    for local_id, local in (
        ("auto_thing", ActionTier.AUTO),
        ("notify_thing", ActionTier.NOTIFY),
        ("confirm_thing", ActionTier.CONFIRM),
    ):
        for incoming in (None, 1, 2, 3, 0, 99, "x"):
            effective = channel.effective_tier(local_id, incoming)
            assert effective >= local, f"{local_id} lowered by tier={incoming}"


# --- a denied command never reaches the action ------------------------------


async def test_a_never_policy_denies_without_prompting_or_running(
    config, make_registry, policy
):
    action = RecordingAction("write_file", ActionTier.NOTIFY)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED)
    policy.set_policy("write_file", UserPolicy.NEVER)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("write_file", tier=2))

    await run_session(channel, transport)

    assert action.calls == []
    assert consent.seen == [], "a `never` action put a prompt on screen"
    assert transport.result_for("c-1")["status"] == "denied"


async def test_panic_denies_everything_including_tier_one(config, make_registry, policy):
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    policy.panic = True
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("get_system_state", tier=1))

    await run_session(channel, transport)

    assert action.calls == []
    assert transport.result_for("c-1")["status"] == "denied"
    assert "panic" in transport.result_for("c-1")["error"].lower()


async def test_a_denied_prompt_means_nothing_ran(config, make_registry):
    action = RecordingAction("delete_file", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("delete_file", tier=3, path="notes.txt"))

    await run_session(channel, transport)

    assert action.calls == []
    reply = transport.result_for("c-1")
    assert reply["status"] == "denied"
    assert "denied by the user" in reply["error"]


async def test_a_timed_out_prompt_means_nothing_ran(config, make_registry):
    action = RecordingAction("delete_file", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.TIMEOUT)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("delete_file", tier=3))

    await run_session(channel, transport)

    assert action.calls == []
    assert transport.result_for("c-1")["status"] == "denied"


async def test_the_prompt_shows_the_verbatim_params_and_reason(config, make_registry):
    action = RecordingAction("run_command", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    channel = DeviceChannel(config, make_registry([action], consent=consent))
    transport = FakeTransport([auth_ok(), register_ok()])
    frame = command("run_command", tier=3, value="rm important.txt", token="sk-live-secret")
    frame["reason"] = "tidying up your downloads folder"
    transport.push(frame)

    await run_session(channel, transport)

    shown = consent.seen[0]
    # Verbatim: the prompt is not allowed to lie about what will run, so the
    # params are NOT the redacted copy that goes to the audit log.
    assert shown.params == {"value": "rm important.txt", "token": "sk-live-secret"}
    assert shown.reason == "tidying up your downloads folder"
    assert shown.action_id == "run_command"


# --- rate limiting ----------------------------------------------------------


async def test_a_flood_is_refused_after_the_burst(config, make_registry):
    """Ten back to back, then refusals until the bucket refills."""
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    clock = [0.0]  # frozen: no refill happens during the flood
    channel = DeviceChannel(config, make_registry([action]), clock=lambda: clock[0])
    transport = FakeTransport([auth_ok(), register_ok()])

    async with live_session(channel, transport):
        replies = [
            await send(channel, transport, command("get_system_state", tier=1, command_id=f"c-{i}"))
            for i in range(20)
        ]

    assert len(action.calls) == 10, f"{len(action.calls)} commands ran past the burst"
    assert all(r["status"] == "ok" for r in replies[:10])
    refused = replies[10:]
    assert all(r["status"] == "error" for r in refused)
    assert all("rate limit" in r["error"] for r in refused)


async def test_every_refused_command_is_still_answered(config, make_registry):
    """A silent drop leaves the server waiting forever."""
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    clock = [0.0]
    channel = DeviceChannel(config, make_registry([action]), clock=lambda: clock[0])
    transport = FakeTransport([auth_ok(), register_ok()])

    async with live_session(channel, transport):
        for index in range(15):
            await send(
                channel, transport, command("get_system_state", tier=1, command_id=f"c-{index}")
            )

    answered = {r["command_id"] for r in transport.results()}
    assert answered == {f"c-{i}" for i in range(15)}


async def test_the_bucket_refills_over_time(config, make_registry):
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    clock = [0.0]
    channel = DeviceChannel(config, make_registry([action]), clock=lambda: clock[0])
    transport = FakeTransport([auth_ok(), register_ok()])

    async with live_session(channel, transport):
        for index in range(11):
            await send(
                channel, transport, command("get_system_state", tier=1, command_id=f"a-{index}")
            )
        assert len(action.calls) == 10
        # Ten seconds later the bucket has refilled by ten tokens.
        clock[0] = 10.0
        reply = await send(
            channel, transport, command("get_system_state", tier=1, command_id="b-0")
        )

    assert reply["status"] == "ok"
    assert len(action.calls) == 11


# --- dedupe and concurrency -------------------------------------------------


async def test_a_redelivered_command_replays_instead_of_running_twice(config, make_registry):
    action = RecordingAction("get_system_state", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])

    async with live_session(channel, transport):
        first = await send(
            channel, transport, command("get_system_state", tier=1, command_id="c-9")
        )
        second = await send(
            channel, transport, command("get_system_state", tier=1, command_id="c-9")
        )

    assert len(action.calls) == 1, "a redelivered command ran twice"
    assert first == second, "the replay differed from the original answer"


async def test_the_replay_history_survives_a_reconnect(config, make_registry):
    """A redelivery after the socket came back must not run the action again."""
    action = RecordingAction("delete_file", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))

    first_socket = FakeTransport([auth_ok(), register_ok()])
    async with live_session(channel, first_socket):
        await send(channel, first_socket, command("delete_file", tier=1, command_id="c-42"))
    assert len(action.calls) == 1

    second_socket = FakeTransport([auth_ok(), register_ok(2)])
    async with live_session(channel, second_socket):
        replay = await send(
            channel, second_socket, command("delete_file", tier=1, command_id="c-42")
        )
    assert len(action.calls) == 1, "the action ran again after a reconnect"
    assert replay["command_id"] == "c-42"


async def test_two_commands_for_the_same_action_do_not_race(config, make_registry):
    from .conftest import SlowAction

    action = SlowAction("type_text", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("type_text", tier=1, command_id="c-a", sleep_s=0.2))
    transport.push(command("type_text", tier=1, command_id="c-b", sleep_s=0.2))

    await run_session(channel, transport)

    assert len(action.calls) == 1
    busy = transport.result_for("c-b")
    assert busy["status"] == "error"
    assert "already running" in busy["error"]


# --- outbound events --------------------------------------------------------


async def test_events_are_emitted_after_registration(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport([auth_required(), auth_ok(), register_ok()])

    async def emit_then_close():
        await channel.ready.wait()
        assert await channel.emit_event("schedule", {"trigger": "nightly"}) is True
        assert await channel.emit_event("file_changed", {"path": "x"}, untrusted=True) is True
        transport.finish()

    emitter = asyncio.create_task(emit_then_close())
    try:
        await asyncio.wait_for(channel.run_session(transport), timeout=10)
    except TransportClosed:
        pass
    await emitter

    events = transport.of_type("device_event")
    assert [e["event"] for e in events] == ["schedule", "file_changed"]
    assert events[0]["data"]["trigger"] == "nightly"
    assert "trust" not in events[0]
    assert events[1]["trust"] == "untrusted"


async def test_event_flooding_is_capped(config, make_registry):
    clock = [0.0]
    channel = DeviceChannel(config, make_registry([]), clock=lambda: clock[0])
    transport = FakeTransport([auth_ok(), register_ok()])

    async def spam():
        await channel.ready.wait()
        results = [await channel.emit_event("noise", {"n": n}) for n in range(40)]
        transport.finish()
        return results

    spammer = asyncio.create_task(spam())
    try:
        await asyncio.wait_for(channel.run_session(transport), timeout=10)
    except TransportClosed:
        pass
    results = await spammer

    assert sum(results) == 20  # the event bucket's capacity
    assert len(transport.of_type("device_event")) == 20


# --- host pinning -----------------------------------------------------------


async def test_host_pinning_refuses_another_server(config, make_registry):
    import dataclasses

    pinned = dataclasses.replace(config, pinned_host="jarvis.lan")
    channel = DeviceChannel(pinned, make_registry([]))
    assert channel.check_host("ws://jarvis.lan:8080/api/websocket") is None
    assert channel.check_host("ws://JARVIS.LAN:8080/api/websocket") is None
    for bad in (
        "ws://evil.example.com/api/websocket",
        "ws://jarvis.lan.evil.com/api/websocket",
        "ws://192.168.1.9/api/websocket",
    ):
        assert channel.check_host(bad) is not None, bad


async def test_no_pin_means_no_restriction(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    assert channel.check_host("ws://anything.example.com/") is None


async def test_run_forever_does_not_connect_to_an_unpinned_host(config, make_registry):
    import dataclasses

    pinned = dataclasses.replace(
        config, pinned_host="jarvis.lan", server_url="ws://evil.example.com/api/websocket"
    )
    channel = DeviceChannel(pinned, make_registry([]), rng=lambda: 0.0)
    attempts = []

    async def connector(url):  # pragma: no cover - must never be called
        attempts.append(url)
        raise AssertionError("connected to a host that does not match the pin")

    # One session's worth of loop, with the sleep patched out.
    channel._backoff.base_s = 0.001
    await asyncio.wait_for(channel.run_forever(connect=connector, max_sessions=1), timeout=5)
    assert attempts == []


# --- reply sanitising -------------------------------------------------------


async def test_a_result_with_a_nonsense_status_becomes_an_error(config, make_registry):
    from jarvis_desktop.actions.base import ActionResult, Status

    weird = ActionResult(True, {"x": 1}, None, Status.OK)
    weird.status = "totally-fine"  # type: ignore[assignment]
    action = RecordingAction("odd", ActionTier.AUTO, result=weird)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("odd", tier=1))

    await run_session(channel, transport)

    reply = transport.result_for("c-1")
    assert reply["status"] == "error"
    assert "no recognised status" in reply["error"]


async def test_an_action_that_raises_is_reported_not_swallowed(config, make_registry):
    class Exploding(RecordingAction):
        def run(self, ctx, params):
            raise RuntimeError("kaboom")

    action = Exploding("boom", ActionTier.AUTO)
    channel = DeviceChannel(config, make_registry([action]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push(command("boom", tier=1))

    await run_session(channel, transport)

    reply = transport.result_for("c-1")
    assert reply["status"] == "error"
    assert "kaboom" in reply["error"]


async def test_an_oversized_frame_closes_the_session(config, make_registry):
    channel = DeviceChannel(config, make_registry([]))
    transport = FakeTransport([auth_ok(), register_ok()])
    transport.push("x" * (600 * 1024))
    with pytest.raises(TransportClosed):
        await channel.run_session(transport)


# --- host pinning survives a redirect ---------------------------------------
#
# Found by adversarial review. `check_host` runs *before* the connection, but
# the websocket client follows HTTP 3xx during the handshake, cross-origin
# included. "We connected" was therefore not the same as "we connected to the
# host we asked for" — and the very next thing the agent does is hand over its
# token.


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("jarvis.lan", ("jarvis.lan", 80)),
        ("jarvis.lan:8080", ("jarvis.lan", 8080)),
        ("JARVIS.LAN:8080", ("jarvis.lan", 8080)),
        ("[::1]:8080", ("::1", 8080)),
        ("[fd00::1]", ("fd00::1", 80)),
        ("192.168.1.5:80", ("192.168.1.5", 80)),
    ],
)
async def test_split_authority(authority, expected):
    from jarvis_desktop.channel import split_authority

    assert split_authority(authority, 80) == expected


class _FakeConnection:
    """Just enough of a websockets client connection to read the Host header."""

    def __init__(self, host: str | None):
        import types

        self.closed = False
        self.request = types.SimpleNamespace(headers={"Host": host} if host else {})

    async def close(self):
        self.closed = True


async def test_handshake_authority_reads_where_the_handshake_landed():
    from jarvis_desktop.channel import handshake_authority

    assert handshake_authority(_FakeConnection("evil.example:8080"), 80) == ("evil.example", 8080)
    assert handshake_authority(_FakeConnection(None), 80) is None
    assert handshake_authority(object(), 80) is None


async def test_the_transport_refuses_a_handshake_that_was_redirected(monkeypatch):
    """The token has not been sent yet at this point, so a redirect caught here
    costs nothing. Caught later it costs the token."""
    import websockets

    from jarvis_desktop.channel import WebsocketTransport

    connection = _FakeConnection("evil.example:8080")

    async def fake_connect(url, **kwargs):
        return connection

    monkeypatch.setattr(websockets, "connect", fake_connect)

    with pytest.raises(TransportClosed) as excinfo:
        await WebsocketTransport.connect("ws://jarvis.lan:8080/api/websocket")

    assert "evil.example" in str(excinfo.value)
    assert connection.closed, "the redirected socket was left open"


async def test_a_redirect_to_another_port_on_the_same_host_is_refused_too(monkeypatch):
    """Same host, different port is still not where we aimed."""
    import websockets

    from jarvis_desktop.channel import WebsocketTransport

    connection = _FakeConnection("jarvis.lan:9999")

    async def fake_connect(url, **kwargs):
        return connection

    monkeypatch.setattr(websockets, "connect", fake_connect)

    with pytest.raises(TransportClosed):
        await WebsocketTransport.connect("ws://jarvis.lan:8080/api/websocket")


async def test_the_transport_accepts_a_handshake_with_the_host_we_asked_for(monkeypatch):
    import websockets

    from jarvis_desktop.channel import WebsocketTransport

    connection = _FakeConnection("jarvis.lan:8080")

    async def fake_connect(url, **kwargs):
        return connection

    monkeypatch.setattr(websockets, "connect", fake_connect)

    transport = await WebsocketTransport.connect("ws://jarvis.lan:8080/api/websocket")

    assert transport is not None
    assert connection.closed is False


async def test_allow_plaintext_ws_is_actually_enforced(config):
    """It was parsed from the config file and then never read — a switch that
    silently does nothing is worse than no switch, because someone trusts it."""
    import dataclasses

    from jarvis_desktop.actions.builtins import build_registry
    from jarvis_desktop.audit import AuditLog
    from jarvis_desktop.policy import PolicyStore

    def channel_for(cfg):
        registry = build_registry(cfg, PolicyStore(cfg.policy_path), AuditLog(cfg.audit_path))
        return DeviceChannel(cfg, registry)

    permissive = channel_for(config)
    assert permissive.check_host("ws://jarvis.lan:8080/api/websocket") is None

    strict = channel_for(dataclasses.replace(config, allow_plaintext_ws=False))
    refusal = strict.check_host("ws://jarvis.lan:8080/api/websocket")
    assert refusal and "plaintext" in refusal
    assert strict.check_host("wss://jarvis.lan:8443/api/websocket") is None


async def test_a_connection_refused_by_the_host_check_never_reaches_the_server(config):
    """`run_forever` must not dial a URL `check_host` refused."""
    import dataclasses

    from jarvis_desktop.actions.builtins import build_registry
    from jarvis_desktop.audit import AuditLog
    from jarvis_desktop.policy import PolicyStore

    cfg = dataclasses.replace(config, pinned_host="jarvis.lan", server_url="ws://evil.example/api/websocket")
    registry = build_registry(cfg, PolicyStore(cfg.policy_path), AuditLog(cfg.audit_path))
    channel = DeviceChannel(cfg, registry, clock=lambda: 0.0, rng=lambda: 0.0)
    dialled: list[str] = []

    async def connector(url):  # pragma: no cover - must never be called
        dialled.append(url)
        raise AssertionError("dialled a refused host")

    async def stop_soon():
        await asyncio.sleep(0)
        await channel.stop()

    asyncio.ensure_future(stop_soon())
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(channel.run_forever(connect=connector, max_sessions=1), timeout=5)

    assert dialled == []
