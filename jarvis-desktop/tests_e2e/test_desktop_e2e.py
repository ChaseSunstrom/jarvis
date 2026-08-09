"""The desktop agent, end to end, against a real jarvis-core.

Everything in ``tests/`` mocks the socket. This suite does not: a real
``python -m jarvis_desktop`` process holds a real websocket to a real
``python -m jarvis`` process, and every assertion here is about what came back
over that wire or what changed on this machine's disk.

What is real
    The agent (its channel, handshake, action registry, policy engine, tier
    arithmetic, path scope, SSRF guard, audit log, presence reporter and
    companion handler), and the server (its websocket framing, device hub,
    presence registry, ``device_control`` service and ``companion`` manager).

What is not
    Two things, both because CI has no human and no screen: the Tier-2/Tier-3
    confirmation dialog and the ``companion.ask`` question dialog. Each is
    replaced by a backend that reads its verdict from a JSON file and records
    what it was asked — see ``agent_runner.py``. That recording is the point:
    it makes "it asked the user again" an assertion rather than an assumption.
    The model and voice backends are faked by the shared harness, at the wire
    protocol; nothing about the server is mocked.

The security invariants this suite is here to prove, in the order they appear
below: an AUTO action runs without asking; a CONFIRM action that is refused
does not run *at all* (checked against the filesystem, not against a status
string); an approved CONFIRM action asks again the very next time; the server
cannot talk the device into a lower tier; and neither a path escape nor an
SSRF target survives a dispatch, even with a human's approval behind it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
from typing import Any

import pytest

from support import DEVICE_ID, DEVICE_NAME, async_wait_until

pytestmark = pytest.mark.e2e

#: Every dispatch is bounded well inside the REST client's own 30s timeout, so
#: a device that never answers fails as a timeout here rather than as a
#: confusing HTTP error.
DISPATCH_TIMEOUT = 20


async def dispatch(
    client: Any,
    action: str,
    params: dict[str, Any] | None = None,
    *,
    tier: int | None = None,
    reason: str = "the end-to-end suite asked for this",
) -> dict[str, Any]:
    """``device_control.run`` against the agent, and the service's report."""
    data: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "action": action,
        "reason": reason,
        "timeout": DISPATCH_TIMEOUT,
    }
    if params is not None:
        data["params"] = params
    if tier is not None:
        data["tier"] = tier
    response = await client.call_service_rest(
        "device_control", "run", data, return_response=True
    )
    return response["service_response"]


async def service(client: Any, domain: str, name: str, data: dict[str, Any] | None = None):
    response = await client.call_service_rest(domain, name, data or {}, return_response=True)
    return response["service_response"]


# ===========================================================================
# 1. it connects, authenticates, registers, and the server knows about it
# ===========================================================================
async def test_the_agent_registers_and_the_server_can_see_it(client, live):
    """A real handshake: auth_required -> auth -> auth_ok -> device/register.

    The agent was already waited for by the fixture; what this asserts is that
    what arrived is *right* — the identity, the platform, and a manifest whose
    tiers are the device's own numbers rather than anything the server chose.
    """
    devices = (await service(client, "device_control", "list_devices"))["devices"]
    mine = [d for d in devices if d["device_id"] == DEVICE_ID]
    assert mine, f"the agent is not in the server's device list: {devices}\n{live.log_tail()}"
    entry = mine[0]

    assert entry["name"] == DEVICE_NAME
    assert entry["platform"] == "desktop"
    assert entry["connected"] is True
    assert entry["app_version"], "the agent registered without a version"

    actions = {a["id"]: a for a in entry["actions"]}
    for required in ("get_system_state", "read_file", "delete_file", "http_request"):
        assert required in actions, f"{required} missing from the manifest: {sorted(actions)}"

    # The tiers the server holds are the ones the local table declares. If the
    # manifest ever arrived with different numbers, every tier assertion below
    # would be measuring the wrong thing.
    assert actions["get_system_state"]["tier"] == 1
    assert actions["read_file"]["tier"] == 1
    assert actions["http_request"]["tier"] == 2
    assert actions["delete_file"]["tier"] == 3
    assert actions["run_command"]["tier"] == 3

    # Capabilities are derived from the config switches, not from the table.
    assert "files" in entry["capabilities"]
    assert "system" in entry["capabilities"]

    # And the presence registry has it too — the other half of "registered".
    report = await service(client, "companion", "presence")
    presence = [d for d in report["devices"] if d["device_id"] == DEVICE_ID]
    assert presence, f"the agent is not in the presence registry: {report}"
    assert presence[0]["connected"] is True
    assert presence[0]["platform"] == "desktop"


# ===========================================================================
# 2. presence: it reports, and the server routes to it
# ===========================================================================
async def test_presence_reaches_the_server_and_routing_picks_this_device(client, live):
    """The agent's ``device_event``/``presence`` frames, as the server sees them.

    A freshly registered ``DevicePresence`` defaults to screen off and locked.
    Both being the other way round can only be the result of a presence frame
    arriving from the agent and being folded in, so this is an assertion about
    the wire, not about a default.
    """
    report = await service(client, "companion", "presence", {"need": "ask"})
    mine = next(d for d in report["devices"] if d["device_id"] == DEVICE_ID)

    assert mine["screen_on"] is True, f"no presence report was applied: {mine}"
    assert mine["locked"] is False
    assert mine["driving"] is False
    assert mine["reach"] >= 2, f"the agent ranked too low to be asked anything: {mine}"
    assert mine["reach_name"] in ("IDLE", "PRESENT", "ACTIVE")

    # It is the only device connected, so it is where a question goes.
    route = report["route"]
    assert route["device_id"] == DEVICE_ID, f"routing did not pick the agent: {route}"
    assert route["mode"] == "ask"
    assert route["reason"]


# ===========================================================================
# 3. a Tier-1 action runs, without asking anybody
# ===========================================================================
async def test_a_tier_one_action_runs_and_returns_a_real_result(client, live):
    prompts_before = len(live.control.prompts())

    outcome = await dispatch(client, "get_system_state", reason="Checking the machine, Sir.")

    assert outcome["status"] == "ok", outcome
    assert outcome["device_id"] == DEVICE_ID
    assert outcome["tier"] == 1
    assert outcome["tier_name"] == "AUTO"

    result = outcome["result"]
    # Plausible, real values measured on this machine — not a fixture. Compared
    # against this process's own view rather than hard-coded to the CI runner,
    # so the suite is runnable on a developer's machine too.
    assert result["os"] == platform.system()
    assert result["hostname"], "no hostname in the system state"
    assert result["device_name"] == DEVICE_NAME
    assert result["python"].startswith("3."), result["python"]
    assert isinstance(result["cpu"], dict) and result["cpu"]["count"] >= 1
    assert isinstance(result["memory"], dict) and result["memory"]
    assert isinstance(result["disk"], dict) and result["disk"]
    assert result["disk"].get("total_bytes", 0) > 0, result["disk"]

    # AUTO means AUTO: nobody was asked anything.
    assert len(live.control.prompts()) == prompts_before, (
        "a Tier-1 action raised a confirmation prompt"
    )
    recorded = live.audit_for("get_system_state")
    assert recorded, "the action was not written to the audit log"
    assert recorded[-1]["decision"] == "ALLOW"
    assert recorded[-1]["status"] == "ok"


# ===========================================================================
# 4. a Tier-3 action the user refuses does not run
# ===========================================================================
async def test_a_refused_tier_three_action_never_reaches_the_handler(client, live):
    """The assertion that matters is the file, not the status string.

    ``denied`` coming back proves the agent *said* no. The file still being
    there proves ``DeleteFile.run`` was never called — which is the property
    the tier system exists to provide.
    """
    victim = live.workspace_file("refuse-me.txt", "still here\n")
    assert victim.exists()
    live.control.set_consent("denied")
    prompts_before = len(live.control.prompts())

    outcome = await dispatch(
        client,
        "delete_file",
        {"path": "refuse-me.txt"},
        reason="Tidying up, Sir. (this text is written by the server)",
    )

    assert outcome["status"] == "denied", outcome
    assert outcome["tier"] == 3
    assert outcome["tier_name"] == "CONFIRM"
    assert "denied by the user" in outcome.get("error", ""), outcome
    # The model is told to stop rather than to retry.
    assert "do NOT send it again" in outcome.get("message", "")

    assert victim.exists(), "the file was deleted despite the refusal — the handler ran"
    assert victim.read_text(encoding="utf-8") == "still here\n"

    prompts = live.control.prompts()
    assert len(prompts) == prompts_before + 1, "the refusal did not come from a prompt"
    prompt = prompts[-1]
    assert prompt["action_id"] == "delete_file"
    assert prompt["tier"] == 3
    assert prompt["tier_name"] == "CONFIRM"
    assert prompt["rememberable"] is False, "a Tier-3 prompt offered to remember the answer"
    # Verbatim params and verbatim server text: the human sees what will run and
    # who asked for it.
    assert prompt["params"] == {"path": "refuse-me.txt"}
    assert prompt["reason"] == "Tidying up, Sir. (this text is written by the server)"
    assert "delete_file" in prompt["rendered"]
    assert "refuse-me.txt" in prompt["rendered"]

    entry = live.audit_for("delete_file")[-1]
    assert entry["status"] == "denied"
    assert entry["decision"] == "DENY"
    assert entry["ok"] is False


# ===========================================================================
# 5. an approved Tier-3 action runs once, and asks again next time
# ===========================================================================
async def test_an_approved_tier_three_action_runs_once_and_asks_again(client, live):
    live.control.set_consent("approved")
    target = live.workspace_file("approve-me.txt", "delete me\n")
    prompts_before = len(live.control.prompts())

    first = await dispatch(client, "delete_file", {"path": "approve-me.txt"})
    assert first["status"] == "ok", first
    assert first["result"]["removed_entries"] == 1
    assert not target.exists(), "the approval came back ok but the file is still there"
    assert len(live.control.prompts()) == prompts_before + 1

    # The same action, the same params, a second time. Approval is consent to
    # run once; it is never a licence.
    target = live.workspace_file("approve-me.txt", "delete me again\n")
    second = await dispatch(client, "delete_file", {"path": "approve-me.txt"})
    assert second["status"] == "ok", second
    assert not target.exists()

    prompts = live.control.prompts()
    assert len(prompts) == prompts_before + 2, (
        "the second identical command did not ask again — the approval was remembered"
    )
    assert [p["action_id"] for p in prompts[-2:]] == ["delete_file", "delete_file"]
    assert all(p["rememberable"] is False for p in prompts[-2:])
    # Two different commands, so two different prompts: this is not one prompt
    # counted twice.
    assert prompts[-1]["command_id"] != prompts[-2]["command_id"]
    assert prompts[-1]["command_id"], "the prompt did not carry the server's command id"

    # Nothing was written to the policy store, so there is nothing that could
    # auto-approve the next one.
    remembered = live.policy().get("policies", {})
    assert "delete_file" not in remembered, (
        f"a Tier-3 answer was persisted: {remembered}"
    )

    # And the adversarial version: a prompt that answers "always" anyway. The
    # Tier-3 prompt never offers it (`rememberable` is false above), so this is
    # a UI that has been tampered with or gone wrong. The approval is honoured
    # for this one command; the "always" is dropped on the floor.
    live.control.set_consent("approved_always")
    target = live.workspace_file("approve-me.txt", "and again\n")
    assert (await dispatch(client, "delete_file", {"path": "approve-me.txt"}))["status"] == "ok"
    assert not target.exists()
    assert live.policy().get("policies", {}) == {}, (
        f"an 'always' answer to a Tier-3 prompt was stored: {live.policy()}"
    )

    prompts_before = len(live.control.prompts())
    target = live.workspace_file("approve-me.txt", "one more time\n")
    assert (await dispatch(client, "delete_file", {"path": "approve-me.txt"}))["status"] == "ok"
    assert len(live.control.prompts()) == prompts_before + 1, (
        "'always' on a Tier-3 prompt stopped the next command asking"
    )
    live.control.set_consent("denied")


# ===========================================================================
# 6. the server may raise a tier and can never lower one
# ===========================================================================
async def test_the_server_cannot_lower_a_tier(client, live, harness):
    """Two halves of the same rule, from opposite directions.

    First: a CONFIRM action tagged ``tier: 1`` on the way in. The server's own
    ``effective_tier`` is ``max(manifest, requested)``, so what leaves the
    server is already 3 — and the device prompts.

    Second, and the stronger case: ``http_request`` is NOTIFY in the manifest,
    so the *server* has no way to know that this particular call is CONFIRM —
    ``HttpRequest.tier_for`` raises it to CONFIRM for a POST, and that lives
    only on the device. The command therefore arrives tagged tier 2 and is
    enforced at 3. A device that took the server's number would have prompted
    at NOTIFY (rememberable) instead of CONFIRM (never rememberable).
    """
    live.control.set_consent("denied")
    survivor = live.workspace_file("tier-one-please.txt", "not today\n")
    prompts_before = len(live.control.prompts())

    lowered = await dispatch(client, "delete_file", {"path": "tier-one-please.txt"}, tier=1)

    assert lowered["status"] == "denied", lowered
    assert lowered["tier"] == 3, "the server sent a lowered tier"
    assert survivor.exists(), "a CONFIRM action ran because the caller asked for tier 1"
    prompts = live.control.prompts()
    assert len(prompts) == prompts_before + 1, "asking for tier 1 skipped the prompt"
    assert prompts[-1]["tier"] == 3
    assert prompts[-1]["rememberable"] is False

    # --- the half the device has to enforce on its own --------------------
    prompts_before = len(prompts)
    posted = await dispatch(
        client,
        "http_request",
        {"url": f"{harness.base_url}/healthz", "method": "POST", "body": "{}"},
        tier=1,
    )

    assert posted["status"] == "denied", posted
    # What the server believed: the manifest floor for http_request, which is
    # NOTIFY. It asked for less than the truth.
    assert posted["tier"] == 2, posted
    prompts = live.control.prompts()
    assert len(prompts) == prompts_before + 1, "a POST went out without a prompt"
    prompt = prompts[-1]
    assert prompt["action_id"] == "http_request"
    assert prompt["tier"] == 3, (
        "the device enforced the server's NOTIFY instead of its own CONFIRM for a POST"
    )
    assert prompt["rememberable"] is False, (
        "a CONFIRM-by-params action offered to remember the answer"
    )
    assert prompt["params"]["method"] == "POST"


# ===========================================================================
# 6b. the policy store, from outside the agent
# ===========================================================================
async def test_the_policy_store_is_real_and_only_tier_two_can_be_remembered(client, live):
    """The positive control for every "nothing was remembered" assertion.

    Those assertions read ``state/policy.json``, and on a green run that file
    does not exist — which means an empty result proves nothing on its own. It
    would look identical if this suite were reading the wrong path, or if the
    agent's state directory were somewhere else entirely. So this test makes
    the store exist, on purpose, by answering *always* to the one tier that is
    allowed to remember it:

    * a Tier-2 ``approved_always`` writes the file this suite reads, at the
      path this suite reads it from, and the next identical command does not
      ask — so remembering demonstrably works, and Tier 3 refusing to do it is
      a property of Tier 3 rather than of a feature that never worked;
    * then the file is edited from *outside* the running process, which is the
      other half: ``never`` and ``panic`` are the user's local kill switches,
      and they are worthless if a long-lived agent only reads them at startup.
    """
    assert not live.policy_path.exists(), (
        f"something was already remembered before this test ran: {live.policy()}"
    )

    try:
        # --- a NOTIFY answer that IS allowed to be remembered -------------
        live.control.set_consent("approved_always")
        prompts_before = len(live.control.prompts())

        first = await dispatch(
            client, "write_file", {"path": "remembered.txt", "content": "once\n"}
        )
        assert first["status"] == "ok", first
        assert first["tier"] == 2
        assert first["tier_name"] == "NOTIFY"
        prompts = live.control.prompts()
        assert len(prompts) == prompts_before + 1, "a NOTIFY action ran without asking"
        assert prompts[-1]["rememberable"] is True, (
            "a Tier-2 prompt did not offer to remember the answer"
        )
        assert (live.workspace / "remembered.txt").read_text(encoding="utf-8") == "once\n"

        # The file the suite reads is the file the agent writes. Everything
        # else in this suite that asserts "the policy store is empty" depends
        # on this line being true.
        assert live.policy_path.exists(), (
            f"'always' was answered and accepted, but {live.policy_path} was never "
            "written — the emptiness assertions elsewhere are reading nothing"
        )
        assert live.remembered() == {"write_file": "allow_always"}, live.policy()

        # And it takes effect: the same command again, with nobody asked.
        live.control.set_consent("denied")  # would refuse, if it were consulted
        second = await dispatch(
            client, "write_file", {"path": "remembered.txt", "content": "twice\n"}
        )
        assert second["status"] == "ok", second
        assert len(live.control.prompts()) == prompts_before + 1, (
            "the remembered answer was not used — it asked again"
        )
        assert (live.workspace / "remembered.txt").read_text(encoding="utf-8") == "twice\n"

        # --- the user's kill switches, set while the agent is running ------
        live.write_policy({"write_file": "never"})
        prompts_before = len(live.control.prompts())
        live.control.set_consent("approved")  # cannot help: NEVER outranks it

        blocked = await dispatch(
            client, "write_file", {"path": "remembered.txt", "content": "three\n"}
        )
        assert blocked["status"] == "denied", blocked
        assert "blocked write_file" in blocked.get("error", ""), blocked
        assert len(live.control.prompts()) == prompts_before, (
            "a NEVER action prompted; the user should not be asked to reconsider"
        )
        assert (live.workspace / "remembered.txt").read_text(encoding="utf-8") == "twice\n"

        # Panic outranks everything, including a Tier-1 action that never asks.
        live.write_policy({}, panic=True)
        panicked = await dispatch(client, "get_system_state")
        assert panicked["status"] == "denied", panicked
        assert "panic" in panicked.get("error", ""), panicked
        assert len(live.control.prompts()) == prompts_before
    finally:
        # Back to the shipped defaults, so the closing sweep is measuring this
        # session and not this test.
        live.forget_policy()
        live.control.fail_closed()

    assert not live.policy_path.exists()
    recovered = await dispatch(client, "get_system_state", reason="after the panic")
    assert recovered["status"] == "ok", (
        f"clearing the panic flag was not picked up by the running agent: {recovered}"
    )


# ===========================================================================
# 7. companion.ask, all the way to the desk and back
# ===========================================================================
async def test_companion_ask_round_trips_through_the_agent(client, live):
    """``companion.ask`` -> ``jarvis_message`` -> the desk -> the waiting service.

    The service call blocks until the answer comes back, so this passing at all
    means the whole loop closed: routing chose the agent, the agent rendered
    the question, the answer went out as ``jarvis_message_result`` on the same
    socket, and the server matched it to the message it was waiting on.
    """
    live.control.set_answer("answered", "lock it")
    asks_before = len(live.control.asks())

    outcome = await service(
        client,
        "companion",
        "ask",
        {
            "question": "Shall I lock the workshop machine, Sir?",
            "options": ["lock it", "leave it"],
            "timeout": DISPATCH_TIMEOUT,
        },
    )

    assert outcome["status"] == "answered", outcome
    assert outcome["answer"] == "lock it"
    assert outcome["device_id"] == DEVICE_ID
    assert outcome["mode"] == "ask"

    asks = live.control.asks()
    assert len(asks) == asks_before + 1, "the question never reached the agent"
    asked = asks[-1]
    assert asked["message_id"] == outcome["message_id"]
    assert asked["text"] == "Shall I lock the workshop machine, Sir?"
    assert asked["options"] == ["lock it", "leave it"]
    assert asked["kind"] == "ask"
    assert asked["mode"] == "ask"

    # A question is not a command: answering one must not have run anything.
    assert not live.audit_for("lock_screen")


# ===========================================================================
# 8. a path escape and an SSRF attempt are refused
# ===========================================================================
@pytest.mark.parametrize(
    ("path", "because"),
    [
        # The expected reason is pinned per case rather than matched against a
        # list of phrases. A path that is refused for the wrong reason — "no
        # such file", say, which is what a broken scope would produce for the
        # ~ case — is a scope that is not doing its job, and a loose match
        # would call that a pass.
        ("../../../../etc/passwd", "path escapes the sandbox"),
        ("/etc/passwd", "path is outside the allowed roots"),
        ("subdir/../../../../etc/shadow", "path escapes the sandbox"),
        ("~/.ssh/id_rsa", "home-relative paths are not allowed"),
    ],
)
async def test_a_path_escape_is_refused(client, live, path, because):
    """``read_file`` is Tier 1 — it runs with nobody in the loop, so the path
    scope is the only thing standing between the model and the whole disk."""
    prompts_before = len(live.control.prompts())

    outcome = await dispatch(client, "read_file", {"path": path})

    assert outcome["status"] != "ok", f"{path} was read: {outcome}"
    assert "content" not in (outcome.get("result") or {})
    error = outcome.get("error", "")
    assert because in error, f"{path} was refused, but not by the path scope: {error!r}"
    assert "root:x:" not in str(outcome), "file contents leaked into the reply"
    # Refused by the scope, not by a prompt somebody might one day approve.
    assert len(live.control.prompts()) == prompts_before


async def test_a_symlink_out_of_the_workspace_is_refused(client, live):
    """The interesting escape: a path that is inside the workspace until it is
    resolved. Only a real filesystem can prove this one."""
    link = live.workspace / "looks-innocent.txt"
    with contextlib.suppress(FileNotFoundError):
        link.unlink()
    os.symlink("/etc/passwd", link)
    assert link.is_symlink()

    outcome = await dispatch(client, "read_file", {"path": "looks-innocent.txt"})

    assert outcome["status"] != "ok", outcome
    assert "root:x:" not in str(outcome), "a symlink out of the workspace was followed"
    assert "escapes" in outcome.get("error", "") or "outside" in outcome.get("error", "")


@pytest.mark.parametrize(
    ("url", "because"),
    [
        (
            "http://169.254.169.254/latest/meta-data/",
            "address 169.254.169.254 is blocked",
        ),
        (
            "http://metadata.google.internal/computeMetadata/v1/",
            "host metadata.google.internal is blocked",
        ),
        ("http://[::1]:8080/api/", "address ::1 is blocked"),
        ("http://192.168.0.1/admin", "address 192.168.0.1 is blocked"),
        ("file:///etc/passwd", "only http and https are allowed"),
    ],
)
async def test_an_ssrf_target_is_refused_even_with_approval(client, live, url, because):
    """Approval is granted here on purpose.

    The guard must be what refuses this, not the policy engine — otherwise a
    user who clicks Approve (or a NOTIFY action they once allowed) would be one
    click away from the cloud metadata service.

    The status is asserted, not just the text. A URL that is simply unreachable
    comes back as ``error`` ("request failed: ..."), and a guard refusal comes
    back as ``denied`` ("refused: ..."), because they are different
    constructors. Matching loosely on the word "refused" would let a *removed*
    guard pass on any machine where the connection is merely declined —
    ``[Errno 111] Connection refused`` contains it. That is not a hypothetical:
    it is what ``http://[::1]:8080/`` produces on a runner with IPv6 loopback.
    """
    live.control.set_consent("approved")

    outcome = await dispatch(client, "http_request", {"url": url})

    assert outcome["status"] == "denied", (
        f"{url} was not refused by the SSRF guard (a guard refusal is `denied`; "
        f"`error` means it was attempted and merely failed): {outcome}"
    )
    error = outcome.get("error", "")
    assert error.startswith("refused: "), error
    assert because in error, f"{url} was refused for the wrong reason: {error!r}"
    assert not (outcome.get("result") or {}).get("body")
    assert "root:x:" not in str(outcome), "the target's content came back"


async def test_the_server_itself_is_still_reachable(client, live, harness):
    """The negative control for the test above.

    The SSRF guard exempts exactly one host — the jarvis-core the agent is
    already authenticated to — and if that exemption were broken, every refusal
    above would pass for the wrong reason.
    """
    live.control.set_consent("approved")

    outcome = await dispatch(client, "http_request", {"url": f"{harness.base_url}/healthz"})

    assert outcome["status"] == "ok", outcome
    assert outcome["result"]["status"] == 200
    assert "ok" in outcome["result"]["body"]
    # Fetched content is somebody else's writing, and says so.
    assert outcome.get("trust") == "untrusted", outcome


# ===========================================================================
# 9. the socket dies: reconnect, re-register, and the server sees the gap
# ===========================================================================
async def test_the_agent_reconnects_and_re_registers_after_the_socket_dies(
    client, live, proxy, harness
):
    """Kill the connection underneath a live session and watch it come back.

    The agent dials through a TCP relay the test owns, so this is a real socket
    dying mid-session — not a stop and start, and not a mocked transport.

    The relay is told to refuse new connections *before* the cut. Without that
    the agent is back within a second and whether the test ever sees the gap
    depends on how the box happened to schedule it; with it, the gap is held
    open until the assertions about it have been made.
    """
    assert live.registration(harness.base_url) is not None
    established_before = proxy.established

    # All three subscriptions are made before anything is cut, and they share
    # one socket, so the frames arrive in the order the server fired them. That
    # ordering is what lets the presence stream be drained at a known point.
    gone = await client.subscribe_events("jarvis_device_disconnected")
    back = await client.subscribe_events("jarvis_device_registered")
    events = await client.subscribe_events("jarvis_device_event")
    try:
        proxy.block()
        dropped = proxy.drop_all()
        assert dropped >= 1, f"there was no live connection to cut. {proxy.stats()}"

        # The gap, as the server reports it rather than as a poll happens to
        # catch it.
        notice = await gone.wait_for(
            lambda e: (e.get("data") or {}).get("device_id") == DEVICE_ID, timeout=30
        )
        assert notice["data"]["name"] == DEVICE_NAME

        # Nothing can run on it while it is away, and presence says so. These
        # are polls rather than single reads only because the server tidies up
        # in two steps; the device cannot return underneath them, because the
        # relay is still refusing connections.
        await async_wait_until(
            lambda: live.registration(harness.base_url) is None,
            timeout=30,
            what="the server to drop the agent from its live device list",
        )

        async def absent_entry():
            report = await service(client, "companion", "presence")
            entry = next(
                (d for d in report["devices"] if d["device_id"] == DEVICE_ID), None
            )
            return entry if entry and entry["connected"] is False else None

        absent = await async_wait_until(
            absent_entry, timeout=30, what="presence to mark the agent absent"
        )
        assert absent["reach"] == 0, absent

        refused = await dispatch(client, "get_system_state", reason="while it is away")
        assert refused["status"] != "ok", refused

        # Let it back in. It is the agent that reconnects — nothing here
        # restarts it, and nothing tells it to try again.
        proxy.unblock()
        # The agent's reconnect delay is exponential from one second and is not
        # configurable, so the wait has to cover several failed dials on a
        # loaded runner having pushed the next one out. 150s is roughly twice
        # the worst case after six refusals; anything past that is a hang, not
        # slowness, and the proxy counters say which.
        registered = await back.wait_for(
            lambda e: (e.get("data") or {}).get("device_id") == DEVICE_ID, timeout=150
        )
        assert registered["data"]["connected"] is True

        entry = live.registration(harness.base_url)
        assert entry is not None and entry["action_count"] > 0, (
            f"it came back without its manifest: {entry}"
        )
        assert proxy.established == established_before + 1, (
            f"expected exactly one new relayed session, saw "
            f"{proxy.established - established_before}. {proxy.stats()}"
        )

        # Everything the old session sent is already queued behind the
        # registration frame, so draining here leaves only what the new one
        # sends. A fresh session means the server rebuilt its presence view,
        # and the agent re-reports the lot rather than waiting for a change.
        while True:
            try:
                events.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        event = await events.wait_for(
            lambda e: (e.get("data") or {}).get("device_id") == DEVICE_ID
            and (e.get("data") or {}).get("event") == "presence",
            timeout=45,
        )
        signals = event["data"]["data"]
        assert signals["screen_on"] is True, signals
        assert event["data"]["trust"] == "trusted"
    finally:
        for stream in (gone, back, events):
            with contextlib.suppress(Exception):
                await stream.unsubscribe()

    # It is not merely connected: it works.
    outcome = await dispatch(client, "get_system_state", reason="after the reconnect")
    assert outcome["status"] == "ok", outcome
    assert outcome["result"]["device_name"] == DEVICE_NAME


async def test_nothing_ran_that_was_not_asked_for(live):
    """A last sweep of the agent's own audit log.

    Every entry it holds should be one of the actions this suite dispatched. An
    action nobody asked for turning up here would mean something in the agent
    executed on its own — which is exactly the failure the tier system is
    supposed to make impossible.
    """
    expected = {"get_system_state", "read_file", "delete_file", "http_request"}
    seen = {entry["action"] for entry in live.audit()}
    assert seen <= expected, f"the agent ran something nobody dispatched: {seen - expected}"

    # And nothing at all was persisted as an always-allow.
    assert live.policy().get("policies", {}) == {}, live.policy()
