"""Every credential Jarvis needs exists after the first start, or it is unusable.

The admin token was already minted on first run. The pairing secret was not: it
was read from ``JARVIS_PAIRING_SECRET`` and nowhere else, so a fresh install
could not add a phone until the operator invented a secret and set a variable —
and nothing on any surface told them that was the missing step.

So it is generated too, into the same file, and the rules are the ones the
token already follows: the environment wins, the value is persisted, the log
says where it went and never what it is.
"""

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.api import pairing  # noqa: E402
from jarvis.auth import (  # noqa: E402
    DATA_AUTH,
    ENV_PAIRING_SECRET,
    ENV_TOKEN,
    PAIRING_SECRET_KEY,
    AuthManager,
    async_setup_auth,
)
from jarvis.core import Jarvis  # noqa: E402
from jarvis.store import Store  # noqa: E402


@pytest.fixture(autouse=True)
def no_credentials_in_the_environment(monkeypatch):
    """First run means first run: nothing inherited from the developer's shell."""
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_PAIRING_SECRET, raising=False)


@pytest.fixture
def box(tmp_path):
    return Jarvis(tmp_path)


def _stored(config_dir: Path) -> dict:
    return json.loads((config_dir / ".storage" / "auth.json").read_text())["data"]


async def test_a_pairing_secret_exists_after_the_first_start(box, tmp_path):
    """The bug: unset meant pairing was unusable, with no way to make it work."""
    auth = await async_setup_auth(box)

    assert len(auth.pairing_secret) >= pairing.MIN_SECRET_CHARS
    # And it is the secret pairing actually checks, so a phone can be added.
    pairing.check_secret(auth.pairing_secret, box)


async def test_the_generated_secret_is_persisted_and_survives_a_restart(tmp_path):
    """Regenerating on every boot would invalidate whatever the operator wrote down."""
    first = await async_setup_auth(Jarvis(tmp_path))
    secret = first.pairing_secret
    assert _stored(tmp_path)[PAIRING_SECRET_KEY] == secret

    second = await async_setup_auth(Jarvis(tmp_path))
    assert second.pairing_secret == secret


async def test_minting_a_token_does_not_lose_the_pairing_secret(tmp_path):
    """Both credentials share one document, so either save must keep the other."""
    auth = await async_setup_auth(Jarvis(tmp_path))
    secret = auth.pairing_secret

    await auth.create_token("phone")

    assert _stored(tmp_path)[PAIRING_SECRET_KEY] == secret
    assert (await async_setup_auth(Jarvis(tmp_path))).pairing_secret == secret


async def test_the_environment_wins_exactly_as_it_does_for_the_token(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(ENV_PAIRING_SECRET, "operator-chose-this")
    auth = await async_setup_auth(Jarvis(tmp_path))

    assert auth.pairing_secret == "operator-chose-this"
    # Nothing was generated behind it: a set variable means the operator is in
    # charge, and a second live secret would be a second way in.
    assert _stored(tmp_path)[PAIRING_SECRET_KEY] == ""
    pairing.check_secret("operator-chose-this", None)


async def test_the_environment_overrides_a_secret_already_on_disk(tmp_path, monkeypatch):
    """Setting the variable on an install that already generated one must take.

    And the generated one must stop working when it does, or an operator who
    set the variable to rotate the secret would be leaving the old one live.
    """
    store = Store(tmp_path, "auth")
    await store.save({"tokens": [], PAIRING_SECRET_KEY: "generated-earlier"})
    monkeypatch.setenv(ENV_PAIRING_SECRET, "operator-chose-this")

    box = Jarvis(tmp_path)
    box.data[DATA_AUTH] = await AuthManager(store).async_load()

    assert pairing.configured_secret(box) == "operator-chose-this"
    with pytest.raises(pairing.PairingError, match="not correct"):
        pairing.check_secret("generated-earlier", box)


async def test_a_stored_secret_is_not_overridden_by_a_later_generation(tmp_path):
    """Second start with the variable gone must not mint a rival secret."""
    store = Store(tmp_path, "auth")
    await store.save({"tokens": [], PAIRING_SECRET_KEY: "already-chosen"})

    auth = await AuthManager(store).async_load()
    assert await auth.async_ensure_pairing_secret() is None
    assert auth.pairing_secret == "already-chosen"


async def test_the_secret_is_never_logged(tmp_path, caplog):
    """It is readable back from disk, unlike a token, so printing it is a leak."""
    with caplog.at_level("DEBUG"):
        auth = await async_setup_auth(Jarvis(tmp_path))

    assert auth.pairing_secret
    assert auth.pairing_secret not in caplog.text
    # ...but the operator is told where it is and how to read it, or a secret
    # nobody can find is the same as no secret at all.
    assert str(tmp_path / ".storage" / "auth.json") in caplog.text
    assert PAIRING_SECRET_KEY in caplog.text
    assert ENV_PAIRING_SECRET in caplog.text


async def test_the_banner_is_printed_once(tmp_path, caplog):
    await async_setup_auth(Jarvis(tmp_path))
    caplog.clear()  # caplog collects for the whole test, not just the block.
    with caplog.at_level("DEBUG"):
        await async_setup_auth(Jarvis(tmp_path))

    assert "GENERATED A PAIRING SECRET" not in caplog.text


async def test_the_store_is_not_readable_by_other_users(tmp_path):
    """It now holds a secret in the clear; 0644 under a default umask would do."""
    await async_setup_auth(Jarvis(tmp_path))
    path = tmp_path / ".storage" / "auth.json"

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_a_manager_with_nowhere_to_write_stays_switched_off(box):
    """An unreadable secret is worse than none: pairing would look on to nobody."""
    auth = AuthManager()
    box.data[DATA_AUTH] = auth

    assert await auth.async_ensure_pairing_secret() is None
    assert auth.pairing_secret == ""
    with pytest.raises(pairing.PairingError, match="switched off"):
        pairing.check_secret("anything", box)


def test_the_accessor_reads_the_stored_secret(tmp_path):
    """The hook an HTTP layer needs — and it authenticates nobody by itself."""
    box = Jarvis(tmp_path)
    auth = asyncio.run(async_setup_auth(box))

    assert pairing.configured_secret(box) == auth.pairing_secret
    # Without a box there is nothing to read but the environment, and reporting
    # a secret that is not in force would be worse than reporting none.
    assert pairing.configured_secret() == ""
