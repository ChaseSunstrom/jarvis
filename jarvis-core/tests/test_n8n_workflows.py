"""What a model may write into somebody's n8n, and what is taken back off it.

## Why this file is mostly about refusals

A workflow is a program that runs against somebody's email, somebody's
spreadsheet and somebody's card, with credentials Jarvis is not allowed to
see. The interesting failures are therefore not "it crashed" but "it saved and
then did something nobody drew":

* two nodes with one name — n8n wires the graph by NAME, so the second one
  inherits the first one's edges;
* an edge to a node that is not there — saves, draws nothing, never runs;
* a credential id the model guessed — points at the wrong account, or an
  account this request had no business touching;
* `active: true` in the object the model wrote — a workflow that ran before
  anybody read it.

Each of those is a one-line mistake with no symptom until it matters.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.n8n.workflows import (  # noqa: E402
    MAX_NODES,
    CleanWorkflow,
    WorkflowError,
    clean_workflow,
    describe_graph,
    needed_connections,
    summarise,
)


def wf(**over):
    """A minimal valid workflow: a webhook into an HTTP request."""
    base = {
        "name": "File the receipt",
        "nodes": [
            {
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [0, 0],
                "parameters": {"path": "receipt"},
            },
            {
                "name": "Notion",
                "type": "n8n-nodes-base.notion",
                "typeVersion": 2,
                "position": [220, 0],
                "parameters": {},
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Notion", "type": "main", "index": 0}]]}
        },
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------
def test_a_reasonable_workflow_becomes_a_payload():
    clean = clean_workflow(wf())
    assert isinstance(clean, CleanWorkflow)
    assert clean.payload["name"] == "File the receipt"
    assert [n["name"] for n in clean.payload["nodes"]] == ["Webhook", "Notion"]
    assert clean.payload["connections"]["Webhook"]["main"][0][0]["node"] == "Notion"


def test_the_payload_has_only_the_four_keys_n8n_takes():
    """Built here, not forwarded — see the next two tests for why."""
    clean = clean_workflow(wf())
    assert set(clean.payload) == {"name", "nodes", "connections", "settings"}


def test_a_workflow_cannot_arrive_active():
    """The model setting `active: true` must not be setting anything.

    A workflow that arrives switched on has run before anybody read it, which
    is the one outcome approval was supposed to prevent.
    """
    clean = clean_workflow(wf(active=True, id="hijacked", pinData={"x": 1}))
    assert "active" not in clean.payload
    assert "id" not in clean.payload
    assert "pinData" not in clean.payload


def test_the_execution_order_is_pinned():
    """A workflow saved without it runs nodes in the legacy order, which is a
    different program from the one that was reviewed."""
    assert clean_workflow(wf()).payload["settings"]["executionOrder"] == "v1"


def test_an_operators_own_settings_survive():
    clean = clean_workflow(wf(settings={"timezone": "Europe/London"}))
    assert clean.payload["settings"]["timezone"] == "Europe/London"
    assert clean.payload["settings"]["executionOrder"] == "v1"


# ---------------------------------------------------------------------------
# credentials — the "ask for connections" half
# ---------------------------------------------------------------------------
def test_a_guessed_credential_is_stripped_and_reported():
    """The single most important line in this module.

    Every n8n example on the internet carries a `credentials` block, so the
    model writes one, and the id in it is invented. Attaching it means the
    workflow points at no account, the wrong account, or an account this
    request had no business reaching.
    """
    workflow = wf()
    workflow["nodes"][1]["credentials"] = {
        "notionApi": {"id": "5", "name": "Somebody else's Notion"}
    }
    clean = clean_workflow(workflow)

    assert "credentials" not in clean.payload["nodes"][1], "a guessed id was sent to n8n"
    assert clean.connections_needed == [("Notion", "notionApi")]


def test_every_credential_a_node_asks_for_is_named():
    workflow = wf()
    workflow["nodes"][1]["credentials"] = {"notionApi": {}, "notionOAuth2Api": {}}
    clean = clean_workflow(workflow)
    assert sorted(k for _n, k in clean.connections_needed) == [
        "notionApi",
        "notionOAuth2Api",
    ]


def test_a_workflow_with_no_credentials_asks_for_nothing():
    assert clean_workflow(wf()).connections_needed == []


def test_the_report_says_which_node_as_well_as_which_credential():
    """"Connect a Notion credential" is not actionable in a nine-node
    workflow; "connect notionApi for 'Notion'" is."""
    workflow = wf()
    workflow["nodes"][1]["credentials"] = {"notionApi": {"id": "5"}}
    node, kind = clean_workflow(workflow).connections_needed[0]
    assert node == "Notion"
    assert kind == "notionApi"


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------
def test_two_nodes_with_one_name_are_refused():
    """n8n keys connections by name, so the duplicate takes over the other
    one's edges and the graph is not the one anybody drew."""
    workflow = wf()
    workflow["nodes"][1]["name"] = "Webhook"
    workflow["connections"] = {}
    with pytest.raises(WorkflowError) as caught:
        clean_workflow(workflow)
    assert "unique" in str(caught.value)


def test_an_edge_to_a_node_that_is_not_there_is_refused():
    """It saves, draws nothing, and the branch simply never runs."""
    workflow = wf()
    workflow["connections"]["Webhook"]["main"][0][0]["node"] = "Ghost"
    with pytest.raises(WorkflowError) as caught:
        clean_workflow(workflow)
    assert "Ghost" in str(caught.value)


def test_an_edge_starting_nowhere_is_refused():
    workflow = wf()
    workflow["connections"]["Nobody"] = {"main": [[]]}
    with pytest.raises(WorkflowError) as caught:
        clean_workflow(workflow)
    assert "Nobody" in str(caught.value)


@pytest.mark.parametrize(
    "broken,expected",
    [
        ({}, "name"),
        ({"name": "x"}, "nodes"),
        ({"name": "x", "nodes": []}, "nodes"),
        ({"name": "", "nodes": [{"name": "a", "type": "b"}]}, "name"),
    ],
)
def test_a_workflow_that_is_not_one_is_refused(broken, expected):
    with pytest.raises(WorkflowError) as caught:
        clean_workflow(broken)
    assert expected in str(caught.value)


def test_a_node_with_no_name_or_type_is_refused():
    with pytest.raises(WorkflowError):
        clean_workflow({"name": "x", "nodes": [{"type": "n8n-nodes-base.noOp"}]})
    with pytest.raises(WorkflowError):
        clean_workflow({"name": "x", "nodes": [{"name": "a"}]})


def test_a_runaway_generation_is_refused():
    nodes = [
        {"name": f"n{i}", "type": "n8n-nodes-base.noOp", "position": [i, 0]}
        for i in range(MAX_NODES + 1)
    ]
    with pytest.raises(WorkflowError) as caught:
        clean_workflow({"name": "big", "nodes": nodes})
    assert str(MAX_NODES) in str(caught.value)


def test_a_workflow_that_is_not_an_object_is_refused():
    for junk in ["a string", 7, None, ["a", "list"]]:
        with pytest.raises(WorkflowError):
            clean_workflow(junk)


# ---------------------------------------------------------------------------
# the normalisations
# ---------------------------------------------------------------------------
def test_a_missing_position_does_not_stack_every_node_on_the_origin():
    """n8n needs one; a node without it is invisible under the first."""
    clean = clean_workflow(
        {"name": "x", "nodes": [{"name": "a", "type": "n8n-nodes-base.noOp"}]}
    )
    assert clean.payload["nodes"][0]["position"] == [0, 0]


def test_a_nonsense_position_is_replaced_rather_than_refused():
    clean = clean_workflow(
        {
            "name": "x",
            "nodes": [{"name": "a", "type": "n8n-nodes-base.noOp", "position": "over there"}],
        }
    )
    assert clean.payload["nodes"][0]["position"] == [0, 0]


def test_a_community_node_is_named_because_it_has_to_be_installed():
    """Otherwise the failure is "Unrecognized node type" at execution time."""
    clean = clean_workflow(
        {
            "name": "x",
            "nodes": [{"name": "a", "type": "n8n-nodes-weird.thing", "position": [0, 0]}],
        }
    )
    assert clean.community_nodes == ["n8n-nodes-weird.thing"]


def test_a_workflow_with_no_trigger_is_a_note_and_not_a_refusal():
    """It is legal, saveable, and exactly what a sub-workflow looks like."""
    clean = clean_workflow(
        {
            "name": "x",
            "nodes": [{"name": "a", "type": "n8n-nodes-base.noOp", "position": [0, 0]}],
        }
    )
    assert any("trigger" in note.lower() for note in clean.notes)


def test_a_trigger_is_recognised_and_says_nothing():
    clean = clean_workflow(wf())
    assert not any("trigger" in note.lower() for note in clean.notes)


# ---------------------------------------------------------------------------
# reading back
# ---------------------------------------------------------------------------
def test_reading_a_workflow_never_returns_node_parameters():
    """People type API keys into an HTTP node's header fields, and n8n stores
    them inline. A read of a workflow must not be a read of that."""
    workflow = {
        "id": "42",
        "name": "Leaky",
        "active": True,
        "nodes": [
            {
                "name": "HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {
                    "url": "https://api.example.com",
                    "headerParameters": {
                        "parameters": [
                            {"name": "Authorization", "value": "Bearer sk-live-SECRET"}
                        ]
                    },
                },
            }
        ],
        "connections": {},
    }
    graph = describe_graph(workflow)
    assert "sk-live-SECRET" not in str(graph)
    assert "parameters" not in str(graph)
    assert graph["nodes"][0]["name"] == "HTTP Request"
    assert graph["active"] is True


def test_reading_says_which_nodes_have_a_credential():
    workflow = {
        "name": "x",
        "nodes": [
            {"name": "A", "type": "t", "credentials": {"gmailOAuth2": {"id": "3"}}},
            {"name": "B", "type": "t"},
        ],
        "connections": {},
    }
    graph = describe_graph(workflow)
    assert graph["nodes"][0]["has_credential"] is True
    assert graph["nodes"][0]["credential_types"] == ["gmailOAuth2"]
    assert graph["nodes"][1]["has_credential"] is False


def test_the_edges_come_back_as_pairs():
    graph = describe_graph(wf())
    assert graph["edges"] == [["Webhook", "Notion"]]


def test_needed_connections_finds_the_node_with_nothing_attached():
    workflow = {
        "name": "x",
        "nodes": [
            {"name": "Attached", "type": "t", "credentials": {"a": {"id": "1"}}},
            {"name": "Empty", "type": "t", "credentials": {"b": {}}},
        ],
    }
    assert needed_connections(workflow) == [
        {"node": "Empty", "credential_type": "b"}
    ]


def test_needed_connections_does_not_guess_about_nodes_that_asked_for_nothing():
    """n8n does not publish which node types require auth, and guessing would
    either nag about nodes needing nothing or stay quiet about one that does."""
    assert needed_connections({"name": "x", "nodes": [{"name": "A", "type": "t"}]}) == []


def test_a_summary_is_enough_to_pick_one_and_no_more():
    row = summarise(
        {
            "id": "7",
            "name": "Nightly",
            "active": True,
            "nodes": [{}, {}],
            "tags": [{"name": "jarvis"}],
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    )
    assert row == {
        "id": "7",
        "name": "Nightly",
        "active": True,
        "nodes": 2,
        "tags": ["jarvis"],
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_every_reader_survives_junk():
    """These read what an n8n of unknown version returned."""
    for junk in [None, "text", 7, [], {"nodes": "not a list"}]:
        describe_graph(junk)
        needed_connections(junk)
        summarise(junk)


def test_a_real_n8n_export_survives_the_round_trip():
    """The most realistic input there is.

    A model asked for an n8n workflow writes what n8n's own "copy" button
    produces, because that is what it has read: uuids, `pinData`, `versionId`,
    `meta`, fractional `typeVersion`s, real credential ids and `active: true`.
    Everything in that list is either dropped or preserved on purpose, and
    getting one of them wrong is a workflow that is rejected by n8n or one
    that runs before anybody reads it.
    """
    export = {
        "name": "Receipts to Notion",
        "nodes": [
            {
                "parameters": {"pollTimes": {"item": [{"mode": "everyMinute"}]}},
                "id": "8f0c-uuid",
                "name": "Gmail Trigger",
                "type": "n8n-nodes-base.gmailTrigger",
                "typeVersion": 1.2,
                "position": [-40, 300],
                "credentials": {"gmailOAuth2": {"id": "aQ1", "name": "Gmail account"}},
            },
            {
                "parameters": {"resource": "databasePage"},
                "id": "1a2b-uuid",
                "name": "Create page",
                "type": "n8n-nodes-base.notion",
                "typeVersion": 2.2,
                "position": [220, 300],
                "credentials": {"notionApi": {"id": "bR2", "name": "Notion"}},
            },
        ],
        "connections": {
            "Gmail Trigger": {"main": [[{"node": "Create page", "type": "main", "index": 0}]]}
        },
        "pinData": {},
        "active": True,
        "settings": {"executionOrder": "v1"},
        "versionId": "abc",
        "meta": {"instanceId": "deadbeef"},
        "id": "WlY9k",
        "tags": [{"name": "prod", "id": "1"}],
    }

    clean = clean_workflow(export)

    # Dropped: everything n8n's create endpoint does not take, and the two
    # that would be dangerous if it did.
    assert sorted(clean.payload) == ["connections", "name", "nodes", "settings"]
    assert not any("credentials" in node for node in clean.payload["nodes"])

    # Kept: a fractional typeVersion is a real n8n version, and rounding it
    # would silently run a different revision of the node.
    assert [n["typeVersion"] for n in clean.payload["nodes"]] == [1.2, 2.2]
    # Kept: negative coordinates are ordinary in a real canvas.
    assert clean.payload["nodes"][0]["position"] == [-40, 300]
    # Kept: parameters are what the workflow DOES.
    assert clean.payload["nodes"][1]["parameters"] == {"resource": "databasePage"}

    # Reported: both real-looking ids, which are still guesses.
    assert clean.connections_needed == [
        ("Gmail Trigger", "gmailOAuth2"),
        ("Create page", "notionApi"),
    ]
    # `gmailTrigger` is recognised, so no misleading "cannot be activated".
    assert clean.notes == []
