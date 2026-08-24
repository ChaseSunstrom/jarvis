"""The promise the whole project rests on, checked rather than trusted.

"100 % local" is the reason this software exists, and nothing in the code
stopped `llm: url:` naming a cloud endpoint. A promise nothing verifies is a
hope, so the model server's address is resolved at startup and refused if it is
somebody else's computer.

What this deliberately does NOT do: refuse a name that does not resolve. On a
first boot a compose service may not have started yet, and failing to start
because DNS was not ready would be a worse bug than the one being prevented.
"""

from __future__ import annotations

import pytest

from jarvis.integrations.llm import is_local_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:8000/v1",
        "http://192.168.1.20:8080/v1",
        "http://10.4.0.9/v1",
        "http://172.16.5.5:4000/v1",
        # Tailscale and friends: CGNAT space, which is an overlay of machines
        # the operator owns.
        "http://100.106.15.29:8080/v1",
        "http://[::1]:8000/v1",
        "http://nas.local:8086",
        "http://host.docker.internal:11434",
    ],
)
def test_a_machine_the_operator_plausibly_owns_is_allowed(url):
    ok, why = is_local_url(url)
    assert ok, why


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "https://1.1.1.1/v1", "http://8.8.8.8:8000/v1"],
)
def test_somebody_else_s_computer_is_refused_with_the_address_it_resolved_to(url):
    ok, why = is_local_url(url)
    assert not ok
    assert "public address" in why
    # The message has to say what to do, not only that it said no.
    assert "local_only" in why


def test_an_unresolvable_name_is_allowed_because_a_container_may_be_starting():
    ok, why = is_local_url("http://a-service-that-has-not-started-yet.invalid:8000/v1")
    assert ok, why


def test_something_that_is_not_a_url_is_refused_rather_than_assumed_local():
    ok, why = is_local_url("not a url at all")
    assert not ok


async def test_setup_refuses_to_start_against_a_public_model_server(monkeypatch, caplog):
    """The guard is not advisory: the integration does not come up."""
    from jarvis.integrations import llm as module

    class FakeJarvis:
        config_dir = "/tmp"
        config: dict = {}
        data: dict = {}

        def __getattr__(self, name):  # pragma: no cover - nothing else is reached
            raise AssertionError(f"setup went further than it should have ({name})")

    ok = await module.async_setup(FakeJarvis(), {"url": "https://api.openai.com/v1", "model": "gpt"})
    assert ok is False


async def test_the_guard_can_be_turned_off_deliberately():
    """An operator running a model through a relay they own may say so."""
    from jarvis.integrations import llm as module

    seen: list[str] = []

    class FakeJarvis:
        config_dir = "/tmp"
        config: dict = {}
        data: dict = {}

        def __getattr__(self, name):
            seen.append(name)
            raise RuntimeError("stop here: the guard let it through")

    with pytest.raises(RuntimeError):
        await module.async_setup(
            FakeJarvis(),
            {"url": "https://api.openai.com/v1", "model": "gpt", "local_only": False},
        )
    assert seen, "setup should have got past the guard"
