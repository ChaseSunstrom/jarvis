"""What a workflow is, before it is allowed anywhere near the instance.

Everything here is a pure function over JSON. No network, no client, no
config — so the rules that decide what Jarvis may build are provable on a
machine with no n8n on it, which is the only way they get tested at all.

## The three things this file exists to do

**Refuse a workflow that is structurally broken.** n8n keys `connections` by
node NAME, so two nodes called the same thing silently merge a graph into
something nobody drew; a connection naming a node that is not there is an edge
into nowhere. Both save happily and fail later, at run time, in a way that
looks like the model wrote nonsense rather than like this module let it
through.

**Strip credentials.** A node the model wrote may carry
`"credentials": {"gmailOAuth2": {"id": "5", "name": "..."}}` — because every
n8n example on the internet has one, and the model has read them all. That id
is a guess. Attaching a guessed credential id means one of three things: it
does not exist (a confusing failure), it exists and is the wrong account (a
quiet one), or it exists and is an account the request had no business
reaching. So the block is removed, always, and what it asked for is REPORTED
instead — which is the whole of "ask for connections". A human attaches
credentials in n8n, where the secrets already live and the model is not.

**Build the payload rather than forwarding one.** `clean_workflow` returns a
new dict with exactly the four keys n8n's create endpoint takes. Passing the
model's object through would carry `active`, `id`, `pinData` and anything else
it invented; a workflow that arrives active is a workflow that ran before
anybody read it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MAX_NODES",
    "MAX_WORKFLOW_BYTES",
    "WorkflowError",
    "CleanWorkflow",
    "clean_workflow",
    "describe_graph",
    "needed_connections",
    "summarise",
]

#: A workflow larger than this is not something a person is going to review,
#: and it is the shape a runaway generation takes.
MAX_NODES = 120
MAX_WORKFLOW_BYTES = 400_000
MAX_NAME_CHARS = 128

#: Node types shipped with n8n. Anything else is a community node, which has to
#: be installed on the instance before the workflow can run — worth saying,
#: because the failure otherwise is "Unrecognized node type" at execution time.
BUILTIN_PREFIXES = ("n8n-nodes-base.", "@n8n/")


class WorkflowError(ValueError):
    """A workflow that must not be sent, and the sentence saying why."""


@dataclass
class CleanWorkflow:
    """A workflow ready to POST, and everything a human needs to know first."""

    #: Exactly what n8n's create endpoint takes. Nothing else.
    payload: dict[str, Any]
    #: `(node name, credential type)` for every credential the model asked for
    #: and did not get. This is the "connect these" list.
    connections_needed: list[tuple[str, str]] = field(default_factory=list)
    #: Node types that are not shipped with n8n.
    community_nodes: list[str] = field(default_factory=list)
    #: Things worth saying that are not refusals.
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.payload.get("name", ""),
            "nodes": len(self.payload.get("nodes") or []),
            "connections_needed": [
                {"node": node, "credential_type": kind}
                for node, kind in self.connections_needed
            ],
            "community_nodes": list(self.community_nodes),
            "notes": list(self.notes),
        }


def _node_name(raw: Any, index: int) -> str:
    name = str((raw or {}).get("name") or "").strip()
    if not name:
        raise WorkflowError(
            f"node {index + 1} has no name. Every node needs one — n8n keys the "
            "connections by name, so an unnamed node cannot be wired to."
        )
    return name


def clean_workflow(raw: Any, *, tag_note: str = "") -> CleanWorkflow:
    """Validate and normalise one workflow. Raises `WorkflowError`.

    The refusals are exactly the ones that would produce a workflow which
    SAVES and then misbehaves: duplicate node names, edges to nodes that do not
    exist, and sizes nobody will review. Everything else is normalised or
    reported, because a rule that rejects a legitimate workflow is worse than
    the mistake it was guarding against.
    """
    if not isinstance(raw, dict):
        raise WorkflowError("a workflow has to be a JSON object with name and nodes.")

    name = str(raw.get("name") or "").strip()
    if not name:
        raise WorkflowError("the workflow needs a name.")
    if len(name) > MAX_NAME_CHARS:
        raise WorkflowError(f"that name is {len(name)} characters; the limit is {MAX_NAME_CHARS}.")

    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise WorkflowError("the workflow needs a `nodes` list with at least one node.")
    if len(nodes_raw) > MAX_NODES:
        raise WorkflowError(
            f"that is {len(nodes_raw)} nodes; the limit is {MAX_NODES}. Split it up."
        )

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    connections_needed: list[tuple[str, str]] = []
    community: list[str] = []

    for index, entry in enumerate(nodes_raw):
        if not isinstance(entry, dict):
            raise WorkflowError(f"node {index + 1} is not an object.")
        node_name = _node_name(entry, index)
        if node_name in seen:
            # The one that looks harmless and is not: n8n stores connections
            # under the node's name, so the second `HTTP Request` inherits the
            # first one's edges and the graph is not the one anybody drew.
            raise WorkflowError(
                f"two nodes are called {node_name!r}. Node names have to be "
                "unique — n8n wires the graph by name, so a duplicate silently "
                "takes over the other one's connections."
            )
        seen.add(node_name)

        node_type = str(entry.get("type") or "").strip()
        if not node_type:
            raise WorkflowError(f"{node_name!r} has no `type`.")
        if not node_type.startswith(BUILTIN_PREFIXES):
            community.append(node_type)

        # Reported, never forwarded. See the module docstring.
        credentials = entry.get("credentials")
        if isinstance(credentials, dict):
            for kind in credentials:
                connections_needed.append((node_name, str(kind)))

        nodes.append(
            {
                "name": node_name,
                "type": node_type,
                "typeVersion": _number(entry.get("typeVersion"), 1),
                "position": _position(entry.get("position")),
                "parameters": entry.get("parameters")
                if isinstance(entry.get("parameters"), dict)
                else {},
            }
        )

    connections = _connections(raw.get("connections"), seen)

    payload: dict[str, Any] = {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        # `executionOrder: v1` is n8n's current ordering; a workflow saved
        # without it runs nodes in the legacy order, which is a different
        # program from the one that was reviewed.
        "settings": _settings(raw.get("settings")),
    }

    size = len(json.dumps(payload))
    if size > MAX_WORKFLOW_BYTES:
        raise WorkflowError(
            f"that workflow is {size} bytes; the limit is {MAX_WORKFLOW_BYTES}."
        )

    notes: list[str] = []
    if not _has_trigger(nodes):
        # Not a refusal: a workflow with no trigger is legal, saveable, and
        # exactly what a sub-workflow called by another one looks like.
        notes.append(
            "No trigger node, so this cannot be activated on its own — it can "
            "only be run by hand or called from another workflow."
        )
    if tag_note:
        notes.append(tag_note)

    return CleanWorkflow(
        payload=payload,
        connections_needed=connections_needed,
        community_nodes=sorted(set(community)),
        notes=notes,
    )


def _number(value: Any, default: float) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _position(value: Any) -> list[Any]:
    """n8n needs one, and a missing position stacks every node on the origin."""
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    ):
        return [value[0], value[1]]
    return [0, 0]


def _settings(value: Any) -> dict[str, Any]:
    settings = dict(value) if isinstance(value, dict) else {}
    settings.setdefault("executionOrder", "v1")
    return settings


def _connections(value: Any, names: set[str]) -> dict[str, Any]:
    """The graph, checked against the nodes that exist.

    An edge naming a node that is not there is the other silent breakage: n8n
    saves it, draws nothing, and the branch simply never runs.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowError("`connections` has to be an object keyed by node name.")

    out: dict[str, Any] = {}
    for source, outputs in value.items():
        if source not in names:
            raise WorkflowError(
                f"`connections` starts an edge at {source!r}, which is not one "
                "of the nodes."
            )
        if not isinstance(outputs, dict):
            raise WorkflowError(f"the connections for {source!r} are not an object.")
        clean_outputs: dict[str, Any] = {}
        for kind, branches in outputs.items():
            if not isinstance(branches, list):
                raise WorkflowError(
                    f"the {kind!r} connections for {source!r} are not a list."
                )
            clean_branches = []
            for branch in branches:
                if branch is None:
                    clean_branches.append([])
                    continue
                if not isinstance(branch, list):
                    raise WorkflowError(
                        f"a {kind!r} branch of {source!r} is not a list of targets."
                    )
                targets = []
                for target in branch:
                    if not isinstance(target, dict):
                        raise WorkflowError(
                            f"a target of {source!r} is not an object."
                        )
                    wanted = str(target.get("node") or "")
                    if wanted not in names:
                        raise WorkflowError(
                            f"{source!r} connects to {wanted!r}, which is not one "
                            "of the nodes."
                        )
                    targets.append(
                        {
                            "node": wanted,
                            "type": str(target.get("type") or kind),
                            "index": int(_number(target.get("index"), 0)),
                        }
                    )
                clean_branches.append(targets)
            clean_outputs[str(kind)] = clean_branches
        out[source] = clean_outputs
    return out


def _has_trigger(nodes: list[dict[str, Any]]) -> bool:
    """Heuristic, and deliberately only used for a NOTE.

    n8n marks trigger nodes in the node description, which the public API does
    not hand out, so the type name is all there is. Getting this wrong must
    therefore never refuse anything.
    """
    for node in nodes:
        kind = str(node.get("type") or "").lower()
        tail = kind.rsplit(".", 1)[-1]
        if tail.endswith("trigger") or tail in {
            "webhook",
            "cron",
            "interval",
            "start",
            "emailreadimap",
            "rssfeedread",
        }:
            return True
    return False


def describe_graph(workflow: Any) -> dict[str, Any]:
    """A workflow as STRUCTURE: what the model is allowed to read back.

    Never the parameters. A node's parameters are where somebody types an API
    key into a header field, a bearer token into an HTTP node, a password into
    a database DSN — n8n stores plenty of that inline rather than in a
    credential. Handing the whole object to the model would make every read of
    a workflow a possible leak of somebody else's secret, so what comes back is
    the shape: names, types, which nodes carry a credential, and the edges.
    """
    data = workflow if isinstance(workflow, dict) else {}
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    graph = []
    for entry in nodes:
        if not isinstance(entry, dict):
            continue
        credentials = entry.get("credentials")
        graph.append(
            {
                "name": str(entry.get("name") or ""),
                "type": str(entry.get("type") or ""),
                "has_credential": bool(isinstance(credentials, dict) and credentials),
                "credential_types": sorted(credentials) if isinstance(credentials, dict) else [],
            }
        )
    edges = []
    connections = data.get("connections")
    if isinstance(connections, dict):
        for source, outputs in connections.items():
            if not isinstance(outputs, dict):
                continue
            for branches in outputs.values():
                if not isinstance(branches, list):
                    continue
                for branch in branches:
                    if not isinstance(branch, list):
                        continue
                    for target in branch:
                        if isinstance(target, dict) and target.get("node"):
                            edges.append([str(source), str(target["node"])])
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "active": bool(data.get("active")),
        "nodes": graph,
        "edges": edges,
    }


def needed_connections(workflow: Any) -> list[dict[str, str]]:
    """Which nodes in an EXISTING workflow have no credential attached.

    Only nodes that already declare one are considered here — n8n does not
    publish which node types require authentication, and guessing would either
    nag about nodes that need nothing or, worse, stay quiet about one that
    does. So this answers a narrow question honestly: of the nodes that say
    they take a credential, which are not connected to one.
    """
    data = workflow if isinstance(workflow, dict) else {}
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    missing: list[dict[str, str]] = []
    for entry in nodes:
        if not isinstance(entry, dict):
            continue
        credentials = entry.get("credentials")
        if not isinstance(credentials, dict):
            continue
        for kind, value in credentials.items():
            attached = isinstance(value, dict) and str(value.get("id") or "").strip()
            if not attached:
                missing.append(
                    {"node": str(entry.get("name") or ""), "credential_type": str(kind)}
                )
    return missing


def summarise(workflow: Any) -> dict[str, Any]:
    """One row for a list: enough to pick one, nothing that could be a secret."""
    data = workflow if isinstance(workflow, dict) else {}
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    return {
        "id": str(data.get("id") or ""),
        "name": str(data.get("name") or ""),
        "active": bool(data.get("active")),
        "nodes": len(nodes),
        "tags": [
            str(t.get("name") or "") if isinstance(t, dict) else str(t) for t in tags
        ],
        "updated_at": str(data.get("updatedAt") or ""),
    }
