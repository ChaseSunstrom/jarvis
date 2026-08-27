"""The two actions that reach past this machine.

Every other action does something to the desktop. These two talk to the hub —
because a note belongs to the house rather than to the laptop, and a snippet
saved here should be readable from the phone.

Nothing here opens a socket: `urllib.request.urlopen` is replaced, so what is
tested is the request the action builds (its URL, its method, its bearer token)
and how it reports the hub's answers, including its refusals.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
import urllib.error
from typing import Any

import pytest

from jarvis_desktop.actions.base import ActionContext
from jarvis_desktop.actions.notes import FindNote, SaveNote, hub_base_url
from jarvis_desktop.config import Config
from jarvis_desktop.policy import ActionTier


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def ctx(tmp_path):
    from jarvis_desktop.actions.paths import PathScope

    config = Config(
        server_url="ws://hub.local:8080/api/websocket",
        token="secret-token",
        device_id="desk",
        device_name="Desk",
    )
    return ActionContext(config=config, scope=PathScope([tmp_path]))


class Sent(list):
    """What the action asked for, and what the hub will answer.

    A list subclass rather than a tuple of two: the assertions read
    `sent[0]["url"]`, which is the thing being tested, and the answers are
    stage-setting.
    """

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[Any] = []


@pytest.fixture
def sent(monkeypatch):
    """Every request the action made, without one leaving the machine."""
    captured = Sent()
    answers = captured.answers

    def fake_urlopen(request, timeout=None):
        captured.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": json.loads(request.data.decode()) if request.data else None,
            }
        )
        answer = answers.pop(0) if answers else {}
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_the_http_base_is_derived_from_the_one_address_it_was_given(ctx):
    """A second setting for "the same server, over http" is a second thing to
    get wrong."""
    assert hub_base_url(ctx) == "http://hub.local:8080"
    # The config is frozen, as it should be — a second context rather than an
    # assignment.
    secure = ActionContext(
        config=replace(ctx.config, server_url="wss://jarvis.example:443/api/websocket"),
        scope=ctx.scope,
    )
    assert hub_base_url(secure) == "https://jarvis.example:443"


def test_saving_a_note_posts_it_with_the_agent_token(ctx, sent):
    sent.answers.append({"created": True, "note": {"id": "gate-code", "title": "Gate code"}})

    result = SaveNote().run(ctx, {"title": "Gate code", "body": "1234#", "tags": ["house"]})

    assert result.ok is True
    assert result.data == {"id": "gate-code", "title": "Gate code"}
    assert sent[0]["url"] == "http://hub.local:8080/api/notes"
    assert sent[0]["method"] == "POST"
    assert sent[0]["headers"]["authorization"] == "Bearer secret-token"
    assert sent[0]["body"] == {"title": "Gate code", "body": "1234#", "tags": ["house"]}


def test_searching_asks_the_hub_and_returns_titles(ctx, sent):
    sent.answers.append(
        {"notes": [{"id": "boiler-serviced", "title": "Boiler serviced", "tags": ["house"]}]}
    )

    result = FindNote().run(ctx, {"query": "boiler"})

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["notes"][0]["title"] == "Boiler serviced"
    assert sent[0]["url"] == "http://hub.local:8080/api/notes?q=boiler"
    assert sent[0]["method"] == "GET"


def test_reading_one_note_asks_for_it_by_id(ctx, sent):
    sent.answers.append({"note": {"id": "gate-code", "title": "Gate code", "body": "1234#"}})
    result = FindNote().run(ctx, {"id": "gate-code"})
    assert result.data["note"]["body"] == "1234#"
    assert sent[0]["url"] == "http://hub.local:8080/api/notes/gate-code"


def test_the_hub_s_own_refusal_is_what_the_user_is_told(ctx, sent):
    """"no note 'x'" is the answer. Hiding it behind "request failed" would
    make the agent the least useful thing in the chain."""
    sent.answers.append(
        urllib.error.HTTPError(
            "http://hub.local:8080/api/notes/nope",
            404,
            "Not Found",
            {},
            io.BytesIO(json.dumps({"message": "no note 'nope'"}).encode()),
        )
    )
    result = FindNote().run(ctx, {"id": "nope"})
    assert result.ok is False
    assert "no note 'nope'" in result.error


def test_an_agent_with_no_token_says_so_rather_than_trying(ctx):
    tokenless = ActionContext(config=replace(ctx.config, token=""), scope=ctx.scope)
    action = SaveNote()
    assert action.available(tokenless) is False
    assert "token" in action.unavailable_reason(tokenless)


def test_neither_action_is_allowed_to_run_silently(ctx):
    """Tier 2: they are announced. Writing to the user's own store is not worth
    stopping the house for, and it is worth telling them about."""
    assert SaveNote().tier == ActionTier.NOTIFY
    assert FindNote().tier == ActionTier.NOTIFY
