"""Grounding: what nodes this n8n actually has, and checking JSON against it.

## The failure this is about

A model writing n8n JSON writes against a catalogue it has never seen. It
knows there is a Slack node, so it writes `n8n-nodes-base.slack` at
`typeVersion: 2` — and this box has `2.2`, or has it under a different
package, or does not have it because nobody installed the community node.
n8n saves the workflow, draws a red box, and the failure surfaces days later
as "the thing you set up does nothing".

## And the bug that is live today

`_settings()` used to forward whatever the model wrote. n8n's own
`workflowSettings.yml` is `additionalProperties: false` with a closed list, so
one invented key turns an approved workflow into an opaque 400 — after the
human has said yes. That is the first test in the settings section, and it is
a fix rather than a feature.
"""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.n8n.client import N8nClient  # noqa: E402
from jarvis.integrations.n8n.nodes import (  # noqa: E402
    ERROR,
    WARNING,
    NodeCatalogue,
    NodeType,
    harvest,
    load,
    read_catalogue,
    validate,
)
from jarvis.integrations.n8n.session import COOKIE_NAME, N8nSession  # noqa: E402
from jarvis.integrations.n8n.workflows import (  # noqa: E402
    SETTINGS_KEYS,
    _settings,
    clean_workflow,
    strip_for_update,
)

URL = "http://n8n.lan:5678"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.session.signature"


def workflow(**over):
    base = {
        "name": "File the receipt",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2},
            {"name": "Gmail", "type": "n8n-nodes-base.gmail", "typeVersion": 2.1},
        ],
        "connections": {"Webhook": {"main": [[{"node": "Gmail", "type": "main", "index": 0}]]}},
    }
    base.update(over)
    return base


def catalogue(*types: NodeType) -> NodeCatalogue:
    box = NodeCatalogue(source="harvest")
    box.merge(list(types))
    return box


BASIC = (
    NodeType("n8n-nodes-base.webhook", 2.0),
    NodeType("n8n-nodes-base.gmail", 2.1),
)


# ---------------------------------------------------------------------------
# the settings whitelist — a live bug, not a feature
# ---------------------------------------------------------------------------
def test_an_invented_settings_key_is_dropped_rather_than_sent():
    """n8n's settings schema is `additionalProperties: false`. One key it does
    not know and the POST comes back 400, on a workflow a human has already
    approved, with a message nobody can act on."""
    got = _settings({"executionOrder": "v1", "retryOnFail": True, "nonsense": 1})
    assert got == {"executionOrder": "v1"}


def test_the_legal_keys_all_survive():
    every = {key: "x" for key in SETTINGS_KEYS}
    got = _settings(every)
    assert set(got) == set(SETTINGS_KEYS)


def test_execution_order_is_still_defaulted():
    assert _settings(None)["executionOrder"] == "v1"
    assert _settings({"timezone": "Europe/London"})["executionOrder"] == "v1"


def test_a_workflow_with_junk_settings_is_still_accepted():
    """Dropping, not refusing. A model that put a node setting in the workflow
    settings wrote something reasonable in the wrong place, and a refusal
    costs an approval round trip to say so."""
    cleaned = clean_workflow(workflow(settings={"alwaysOutputData": True}))
    assert cleaned.payload["settings"] == {"executionOrder": "v1"}


# ---------------------------------------------------------------------------
# strip_for_update — `update_workflow` was defined and unusable
# ---------------------------------------------------------------------------
def test_a_fetched_workflow_is_reduced_to_what_a_put_will_take():
    """The obvious round trip — GET, change one thing, PUT — is a guaranteed
    400 today, because a GET carries a dozen server-owned fields and the
    update schema is closed too."""
    fetched = {
        **workflow(),
        "id": "wf-1",
        "active": True,
        "tags": [{"id": "1", "name": "jarvis"}],
        "versionId": "abc",
        "activeVersionId": "def",
        "versionCounter": 7,
        "sourceWorkflowId": "wf-0",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-02-01T00:00:00Z",
        "isArchived": False,
        "triggerCount": 1,
        "meta": {"instanceId": "x"},
        "pinData": {},
        "shared": [],
        "scopes": ["workflow:read"],
        "staticData": None,
    }
    got = strip_for_update(fetched)
    assert set(got) == {"name", "nodes", "connections", "settings"}
    assert got["name"] == "File the receipt"


def test_stripping_also_cleans_the_settings():
    got = strip_for_update({**workflow(), "settings": {"executionOrder": "v0", "junk": 1}})
    assert got["settings"] == {"executionOrder": "v0"}


# ---------------------------------------------------------------------------
# harvesting: the vocabulary this box actually runs
# ---------------------------------------------------------------------------
def test_harvest_takes_the_newest_version_of_each_type():
    """Two workflows using the same node at different versions: the newest is
    the one to ground a model on, because the older one is what it would have
    guessed anyway."""
    found = {t.name: t.version for t in harvest([
        {"nodes": [{"type": "n8n-nodes-base.slack", "typeVersion": 2}]},
        {"nodes": [{"type": "n8n-nodes-base.slack", "typeVersion": 2.2}]},
        {"nodes": [{"type": "n8n-nodes-base.gmail", "typeVersion": 2.1}]},
    ])}
    assert found == {"n8n-nodes-base.slack": 2.2, "n8n-nodes-base.gmail": 2.1}


def test_harvest_survives_a_malformed_workflow():
    """Somebody's real workflow being odd must not stop Jarvis learning from
    the other forty-nine."""
    found = harvest([
        {"nodes": "not a list"},
        None,
        {"nodes": [{"type": "", "typeVersion": 1}, {"no": "type"}]},
        {"nodes": [{"type": "n8n-nodes-base.cron", "typeVersion": "not a number"}]},
    ])
    assert [t.name for t in found] == ["n8n-nodes-base.cron"]
    assert found[0].version == 1.0


def test_a_harvested_type_is_not_marked_as_catalogued():
    """They are different assurances: harvested means it WORKS here,
    catalogued means it EXISTS here."""
    assert harvest([{"nodes": [{"type": "x", "typeVersion": 1}]}])[0].catalogued is False


# ---------------------------------------------------------------------------
# the full catalogue
# ---------------------------------------------------------------------------
def test_a_version_list_resolves_to_the_newest():
    """n8n's `version` is a number OR a list. A client that took the first
    element would ground the model on the oldest version there is."""
    rows = read_catalogue([
        {"name": "n8n-nodes-base.slack", "displayName": "Slack", "version": [1, 1.1, 2.2]}
    ])
    assert rows[0].version == 2.2
    assert rows[0].catalogued is True


@pytest.mark.parametrize(
    "row",
    [
        {"name": "a.cronTrigger", "version": 1},
        {"name": "a.thing", "version": 1, "group": ["trigger"]},
        {"name": "a.thing", "version": 1, "polling": True},
        {"name": "a.thing", "version": 1, "eventTriggerDescription": ""},
    ],
)
def test_every_marker_n8n_uses_for_a_trigger_is_read(row):
    assert read_catalogue([row])[0].trigger is True


def test_merging_keeps_the_best_of_both_sources():
    box = catalogue(NodeType("a.b", 2.0))
    box.merge([NodeType("a.b", 1.0, catalogued=True, display_name="A B", trigger=True)])
    got = box.get("a.b")
    assert got.version == 2.0, "the newer version wins"
    assert got.catalogued and got.trigger and got.display_name == "A B"


def test_the_listing_is_bounded_and_searchable():
    box = catalogue(*[NodeType(f"n8n-nodes-base.thing{i}", 1) for i in range(50)])
    box.merge([NodeType("n8n-nodes-base.gmail", 2.1)])
    assert len(box.listing(limit=10)) == 10
    assert [r["type"] for r in box.listing(search="gmail")] == ["n8n-nodes-base.gmail"]


def test_a_whole_version_is_shown_as_an_integer():
    """`2.0` next to `2.1` reads as a mistake, and a model copying it writes
    a float where n8n's own JSON has an int."""
    assert catalogue(NodeType("a.b", 2.0)).listing()[0]["typeVersion"] == 2
    assert catalogue(NodeType("a.b", 2.1)).listing()[0]["typeVersion"] == 2.1


# ---------------------------------------------------------------------------
# loading, over the wire
# ---------------------------------------------------------------------------
async def test_the_harvest_needs_no_login():
    """The whole point of harvesting: it works with the API key Jarvis already
    has, so grounding is not gated behind a password."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/rest/" not in request.url.path
        return httpx.Response(
            200,
            json={"data": [{"nodes": [{"type": "n8n-nodes-base.gmail", "typeVersion": 2.1}]}]},
        )

    box = await load(N8nClient(URL, "key", transport=httpx.MockTransport(handler)), None)
    assert box.source == "harvest"
    assert box.newest_version("n8n-nodes-base.gmail") == 2.1


async def test_a_login_adds_the_full_catalogue():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/"):
            return httpx.Response(
                200,
                json={"data": [{"nodes": [{"type": "n8n-nodes-base.gmail", "typeVersion": 2.1}]}]},
            )
        if request.url.path.endswith("/login"):
            return httpx.Response(
                200, json={"data": {}}, headers={"Set-Cookie": f"{COOKIE_NAME}={TOKEN}"}
            )
        return httpx.Response(
            200, json=[{"name": "n8n-nodes-base.slack", "version": [1, 2.2]}]
        )

    transport = httpx.MockTransport(handler)
    box = await load(
        N8nClient(URL, "key", transport=transport),
        N8nSession(URL, "a@b.c", "hunter2hunter2", transport=transport),
    )
    assert box.source == "harvest+catalogue"
    assert box.newest_version("n8n-nodes-base.slack") == 2.2


async def test_a_failure_keeps_what_was_known_rather_than_blanking_it():
    """A transient error must not make Jarvis dumber than it was a minute
    ago — and it must never raise, because a grounding improvement that can
    fail a tool call is a downgrade."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    before = catalogue(*BASIC)
    after = await load(
        N8nClient(URL, "key", transport=httpx.MockTransport(handler)),
        None,
        existing=before,
        force=True,
    )
    assert after.newest_version("n8n-nodes-base.gmail") == 2.1


async def test_a_fresh_catalogue_is_not_refetched():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"data": []})

    client = N8nClient(URL, "key", transport=httpx.MockTransport(handler))
    box = catalogue(*BASIC)
    import time

    box.checked_at = time.time()
    await load(client, None, existing=box)
    assert calls == []


# ---------------------------------------------------------------------------
# validating — a report, never a gate
# ---------------------------------------------------------------------------
def test_a_good_workflow_passes_with_only_the_credential_note():
    report = validate(
        workflow(
            nodes=[
                {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2},
                {
                    "name": "Gmail",
                    "type": "n8n-nodes-base.gmail",
                    "typeVersion": 2.1,
                    "credentials": {"gmailOAuth2": {"id": "5"}},
                },
            ]
        ),
        catalogue(*BASIC),
    )
    assert report["ok"] is True
    assert any("gmailOAuth2" in f["message"] for f in report["findings"])


def test_a_node_type_this_instance_does_not_have_is_a_warning_not_an_error():
    """A warning because the catalogue may be a harvest, which only sees types
    already in use — refusing a node nobody has used yet would be absurd."""
    report = validate(
        workflow(
            nodes=[{"name": "S", "type": "n8n-nodes-base.slaack", "typeVersion": 1}],
            connections={},
        ),
        catalogue(*BASIC),
    )
    assert report["ok"] is True
    bad = [f for f in report["findings"] if "slaack" in f["message"]]
    assert bad and bad[0]["level"] == WARNING


def test_a_version_newer_than_this_instance_has_is_named_with_both_numbers():
    report = validate(
        workflow(
            nodes=[{"name": "G", "type": "n8n-nodes-base.gmail", "typeVersion": 9}],
            connections={},
        ),
        catalogue(*BASIC),
    )
    said = [f["message"] for f in report["findings"] if "version" in f["message"]]
    assert said and "9" in said[0] and "2.1" in said[0]


def test_an_older_version_is_not_complained_about():
    """Writing an older version is a legitimate choice, and n8n loads it."""
    report = validate(
        workflow(
            nodes=[{"name": "G", "type": "n8n-nodes-base.gmail", "typeVersion": 1}],
            connections={},
        ),
        catalogue(*BASIC),
    )
    assert not [f for f in report["findings"] if "newest here" in f["message"]]


def test_a_duplicate_node_name_is_an_error_because_it_silently_breaks_the_graph():
    report = validate(
        workflow(
            nodes=[
                {"name": "A", "type": "n8n-nodes-base.webhook", "typeVersion": 2},
                {"name": "A", "type": "n8n-nodes-base.gmail", "typeVersion": 2.1},
            ],
            connections={},
        ),
        catalogue(*BASIC),
    )
    assert report["ok"] is False
    assert report["findings"][0]["level"] == ERROR


def test_no_trigger_is_a_warning_with_the_legitimate_case_named():
    report = validate(
        workflow(
            nodes=[{"name": "G", "type": "n8n-nodes-base.gmail", "typeVersion": 2.1}],
            connections={},
        ),
        catalogue(*BASIC),
    )
    said = [f for f in report["findings"] if f["where"] == "trigger"]
    assert said and "called by another workflow" in said[0]["message"]


def test_the_catalogue_answers_the_trigger_question_the_heuristic_gets_wrong():
    """`emailReadImap` is a trigger and its name does not say so. The
    heuristic special-cases it; a real catalogue does not have to."""
    odd = NodeType("n8n-nodes-base.somePollingThing", 1, catalogued=True, trigger=True)
    report = validate(
        workflow(
            nodes=[{"name": "P", "type": "n8n-nodes-base.somePollingThing", "typeVersion": 1}],
            connections={},
        ),
        catalogue(odd),
    )
    assert not [f for f in report["findings"] if f["where"] == "trigger"]


def test_with_no_catalogue_it_says_it_checked_nothing():
    """A report with a missing section is honest. A report that silently
    checks nothing is worse than no report."""
    report = validate(workflow(), None)
    said = [f for f in report["findings"] if f["where"] == "catalogue"]
    assert said and "not checked" in said[0]["message"]


def test_it_never_refuses_something_create_would_accept():
    """A validator stricter than the writer is one people learn to skip."""
    good = workflow()
    assert clean_workflow(good)
    assert validate(good, catalogue(*BASIC))["ok"] is True
    assert validate(good, None)["ok"] is True


def test_a_workflow_that_is_not_one_fails_with_the_writers_own_sentence():
    report = validate({"name": "", "nodes": []}, catalogue(*BASIC))
    assert report["ok"] is False
    assert "name" in report["findings"][0]["message"]
