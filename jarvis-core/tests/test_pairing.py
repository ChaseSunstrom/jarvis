"""Pairing a phone without typing a token, and without putting one in a QR.

The shortcut everybody reaches for — encode the token in the QR — is worse than
typing it. A QR on a screen can be photographed from across a room, ends up in
whatever screenshot captured it, and stays valid as long as the token does. A
credential in a picture is a credential in every copy of that picture.

So the QR carries a short-lived, single-use code, and the phone exchanges it
for a token over HTTP. The claim endpoint is the only unauthenticated write in
the API, which makes everything below load-bearing rather than defensive.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from jarvis.api import pairing  # noqa: E402
from jarvis.api.server import create_app  # noqa: E402
from jarvis.auth import DATA_AUTH, ENV_TOKEN, AuthManager  # noqa: E402
from jarvis.core import Jarvis  # noqa: E402


SECRET = "pair-me-please"


@pytest.fixture(autouse=True)
def pairing_secret(monkeypatch):
    """Pairing is off until an operator turns it on, so tests turn it on."""
    monkeypatch.setenv(pairing.ENV_PAIRING_SECRET, SECRET)


@pytest.fixture
async def jarvis(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path)
    await box.async_setup({})
    box.data[DATA_AUTH] = AuthManager()
    yield box
    await box.async_stop()


def _auth(box: Jarvis) -> AuthManager:
    return box.data[DATA_AUTH]


# The HTTP half needs a synchronous app, so it builds its own box rather than
# borrowing the async fixture above.
@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    box = Jarvis(tmp_path)
    box.data[DATA_AUTH] = AuthManager()
    secret = asyncio.run(box.data[DATA_AUTH].create_token("console"))[1]
    with TestClient(create_app(box, static_dir=tmp_path / "no-www")) as client:
        yield client, secret


async def test_a_code_is_exchanged_for_a_token(jarvis):
    issued = await pairing.async_issue(jarvis, {"secret": SECRET})
    assert issued["code"] and len(issued["code"]) >= 24

    claimed = await pairing.async_claim(jarvis, {"code": issued["code"], "name": "Pixel"})

    assert claimed["token"]
    assert claimed["name"] == "Pixel"
    # And it is a real token: the thing the phone will authenticate with.
    assert _auth(jarvis).verify(claimed["token"]) is not None


async def test_the_code_is_not_the_token(jarvis):
    """The whole point. What goes on screen must not be a credential."""
    issued = await pairing.async_issue(jarvis, {"secret": SECRET})
    assert _auth(jarvis).verify(issued["code"]) is None
    claimed = await pairing.async_claim(jarvis, {"code": issued["code"]})
    assert claimed["token"] != issued["code"]


async def test_a_code_is_single_use(jarvis):
    issued = await pairing.async_issue(jarvis, {"secret": SECRET})
    await pairing.async_claim(jarvis, {"code": issued["code"]})

    with pytest.raises(pairing.PairingError):
        await pairing.async_claim(jarvis, {"code": issued["code"]})


async def test_two_devices_racing_one_code_get_one_token(jarvis):
    """The pop happens before the token is minted, so the race is decided."""
    issued = await pairing.async_issue(jarvis, {"secret": SECRET})

    results = await asyncio.gather(
        pairing.async_claim(jarvis, {"code": issued["code"]}),
        pairing.async_claim(jarvis, {"code": issued["code"]}),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, pairing.PairingError)]
    assert len(ok) == 1 and len(refused) == 1


async def test_an_expired_code_is_refused(jarvis):
    codes = pairing.get_codes(jarvis)
    entry = codes.issue(now=1000.0)

    with pytest.raises(pairing.PairingError):
        codes.claim(entry.code, now=1000.0 + pairing.CODE_TTL + 1)


async def test_a_code_still_works_just_inside_its_life(jarvis):
    codes = pairing.get_codes(jarvis)
    entry = codes.issue(now=1000.0)
    assert codes.claim(entry.code, now=1000.0 + pairing.CODE_TTL - 1).code == entry.code


async def test_a_wrong_code_is_refused_and_counted(jarvis):
    codes = pairing.get_codes(jarvis)
    codes.issue()

    for _ in range(pairing.MAX_ATTEMPTS):
        with pytest.raises(pairing.PairingError):
            codes.claim("not-a-real-code")

    # Past the limit the endpoint stops being useful at all — including for a
    # code that IS valid, which is the point: a sweep gets nowhere.
    entry = codes.issue()
    with pytest.raises(pairing.PairingError, match="Too many failed"):
        codes.claim(entry.code)


async def test_the_attempt_counter_forgets(jarvis):
    """A lockout that never lifts is a support call, not a defence."""
    codes = pairing.get_codes(jarvis)
    for i in range(pairing.MAX_ATTEMPTS):
        with pytest.raises(pairing.PairingError):
            codes.claim("nope", now=1000.0 + i)

    entry = codes.issue(now=1000.0 + pairing.ATTEMPT_WINDOW + 1)
    assert codes.claim(entry.code, now=1000.0 + pairing.ATTEMPT_WINDOW + 2)


async def test_outstanding_codes_are_bounded(jarvis):
    """An authenticated client asking in a loop cannot grow the store."""
    codes = pairing.get_codes(jarvis)
    for _ in range(pairing.MAX_OUTSTANDING * 3):
        codes.issue()
    assert len(codes.codes) <= pairing.MAX_OUTSTANDING


async def test_the_newest_code_survives_the_cap(jarvis):
    """Because the newest is the one on somebody's screen right now."""
    codes = pairing.get_codes(jarvis)
    for _ in range(pairing.MAX_OUTSTANDING):
        codes.issue()
    latest = codes.issue()
    assert codes.claim(latest.code).code == latest.code


async def test_an_empty_or_missing_code_is_refused(jarvis):
    for payload in ({}, {"code": ""}, {"code": None}, {"code": "   "}):
        with pytest.raises(pairing.PairingError):
            await pairing.async_claim(jarvis, payload)


async def test_the_device_name_is_bounded(jarvis):
    issued = await pairing.async_issue(jarvis, {"secret": SECRET})
    claimed = await pairing.async_claim(jarvis, {"code": issued["code"], "name": "x" * 500})
    assert len(claimed["name"]) <= pairing.MAX_NAME_CHARS


async def test_two_codes_are_never_the_same(jarvis):
    codes = pairing.get_codes(jarvis)
    seen = {codes.issue().code for _ in range(pairing.MAX_OUTSTANDING)}
    assert len(seen) == pairing.MAX_OUTSTANDING


# --- over HTTP, which is how the phone actually reaches it ------------------


def test_claim_is_unauthenticated_and_new_is_not(http):
    """The asymmetry is the design: only somebody already in may invite."""
    client, token = http

    # Minting needs a token, because inviting a device onto the house is an
    # authenticated act.
    refused = client.post("/api/pair/new")
    assert refused.status_code == 401

    issued = client.post(
        "/api/pair/new",
        headers={"Authorization": f"Bearer {token}"},
        json={"secret": SECRET},
    )
    assert issued.status_code == 200
    code = issued.json()["code"]

    # Claiming must not, because the phone has no credential yet — that is the
    # entire problem being solved.
    claimed = client.post("/api/pair/claim", json={"code": code, "name": "Pixel 8"})
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["token"] and body["name"] == "Pixel 8"

    # And the token works.
    check = client.get("/api/states", headers={"Authorization": f"Bearer {body['token']}"})
    assert check.status_code == 200


def test_a_bad_claim_says_so_without_saying_more(http):
    client, _token = http
    refused = client.post("/api/pair/claim", json={"code": "definitely-not-valid"})
    assert refused.status_code == 403
    # Nothing about how many codes exist, or how close the guess was.
    assert "not valid" in refused.json()["detail"]


# --- the escalation this endpoint would otherwise be -------------------------


def test_minting_needs_the_pairing_secret(http):
    """Possession of the API token is deliberately not enough.

    jarvis-web's relay attaches the server-held admin token to whatever
    connects, and its origin guard admits a request with no `Origin` because
    that is what a non-browser client looks like. So a script with transient
    reach to the console's port is already an authenticated API client. Without
    a second secret it could mint a code, claim it immediately, and walk away
    with a permanent token — reach for as long as the script runs turned into
    access forever.
    """
    client, token = http
    auth = {"Authorization": f"Bearer {token}"}

    for body in ({}, {"secret": ""}, {"secret": "wrong"}, {"secret": SECRET + "x"}):
        refused = client.post("/api/pair/new", headers=auth, json=body)
        assert refused.status_code == 403, body

    allowed = client.post("/api/pair/new", headers=auth, json={"secret": SECRET})
    assert allowed.status_code == 200


def test_pairing_is_off_until_an_operator_turns_it_on(http, monkeypatch):
    """Fail closed: an unset secret refuses everything rather than allowing it."""
    client, token = http
    monkeypatch.delenv(pairing.ENV_PAIRING_SECRET, raising=False)

    refused = client.post(
        "/api/pair/new",
        headers={"Authorization": f"Bearer {token}"},
        json={"secret": ""},
    )
    assert refused.status_code == 403
    assert "switched off" in refused.json()["detail"]


def test_a_placeholder_secret_is_refused(http, monkeypatch):
    client, token = http
    monkeypatch.setenv(pairing.ENV_PAIRING_SECRET, "x")
    refused = client.post(
        "/api/pair/new",
        headers={"Authorization": f"Bearer {token}"},
        json={"secret": "x"},
    )
    assert refused.status_code == 403
    assert "too short" in refused.json()["detail"]


def test_a_browser_may_not_claim(http):
    """Browsers always send Origin on a cross-origin POST; phones never do."""
    client, token = http
    issued = client.post(
        "/api/pair/new",
        headers={"Authorization": f"Bearer {token}"},
        json={"secret": SECRET},
    )
    code = issued.json()["code"]

    refused = client.post(
        "/api/pair/claim",
        json={"code": code},
        headers={"Origin": "https://evil.example"},
    )
    assert refused.status_code == 403

    # ...and the code was not spent by the refusal, so the real phone can
    # still use it. A refusal that burned the code would be a denial of
    # service anybody on the network could trigger.
    accepted = client.post("/api/pair/claim", json={"code": code})
    assert accepted.status_code == 200


def test_the_secret_is_never_echoed_back(http):
    client, token = http
    issued = client.post(
        "/api/pair/new",
        headers={"Authorization": f"Bearer {token}"},
        json={"secret": SECRET},
    )
    assert SECRET not in issued.text
