"""What nodes this n8n actually has, so the model stops inventing them.

## The failure this fixes

A model writing an n8n workflow is writing against a catalogue it has never
seen. It knows from training that there is a Slack node, so it writes
`n8n-nodes-base.slack` with `typeVersion: 2` — and this instance has `2.2`,
or has the node under `@n8n/n8n-nodes-langchain`, or does not have it at all
because nobody installed the community package. n8n accepts the workflow and
draws a red box, and the failure surfaces days later as "the automation you
set up does nothing".

n8n's own AI builder sidesteps this by handing its model a filtered list of
real node types before it writes anything. So does this.

## Two sources, and the free one is the default

**Harvest** (no extra auth): read the workflows already on the instance
through the public API and collect every `(type, typeVersion)` pair. This
needs only the API key Jarvis already has, and it produces something a static
list cannot — *the vocabulary this box actually runs, at the versions it runs
them*. If everyone here uses `n8n-nodes-base.gmail` at `2.1`, that is a better
grounding signal than any documentation.

**The catalogue** (needs a login): `GET /rest/types/nodes.json` is the full
list n8n's own editor loads. Strictly better when it is available, and
entirely optional.

Neither is required. With no catalogue at all, validation degrades to exactly
what it does today and says so — a report with a missing section is honest; a
report that silently checks nothing is not.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .client import N8nClient, N8nError
from .session import N8nSession, SessionError

_LOGGER = logging.getLogger(__name__)

__all__ = ["NodeCatalogue", "NodeType", "harvest", "CATALOGUE_PATH"]

CATALOGUE_PATH = "types/nodes.json"

#: How long a catalogue is believed. Node types change when somebody installs
#: a community package, which is rare and never urgent.
CACHE_SECONDS = 3600.0

#: How many workflows to read when harvesting. Enough to see the vocabulary,
#: few enough that a tool call is not a crawl of somebody's whole instance.
HARVEST_WORKFLOWS = 50

#: What a listing hands the model. A model given nine hundred node types reads
#: none of them, and the ones that matter are the ones already in use here.
MAX_LISTED = 200


@dataclass(frozen=True)
class NodeType:
    """One node type, at the newest version this instance has."""

    name: str
    version: float
    #: True when it came from the full catalogue rather than from a workflow —
    #: a harvested type is known to WORK here, a catalogued one is known to
    #: EXIST here, and those are different assurances.
    catalogued: bool = False
    display_name: str = ""
    #: n8n only marks triggers in the node description, which is exactly what
    #: the public API withholds. With a catalogue this is a fact; without one
    #: it stays False and `_has_trigger`'s heuristic keeps its job.
    trigger: bool = False

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {"type": self.name, "typeVersion": _tidy(self.version)}
        if self.display_name:
            row["name"] = self.display_name
        if self.trigger:
            row["trigger"] = True
        return row


@dataclass
class NodeCatalogue:
    """Everything known about this instance's node types, from both sources."""

    types: dict[str, NodeType] = field(default_factory=dict)
    #: "", "harvest", "catalogue", or "harvest+catalogue" — said out loud in
    #: every report, because how much to trust a finding depends on it.
    source: str = ""
    checked_at: float = 0.0

    @property
    def known(self) -> bool:
        return bool(self.types)

    @property
    def fresh(self) -> bool:
        return bool(self.checked_at) and (time.time() - self.checked_at) < CACHE_SECONDS

    def get(self, node_type: Any) -> NodeType | None:
        return self.types.get(str(node_type or "").strip())

    def newest_version(self, node_type: Any) -> float | None:
        found = self.get(node_type)
        return found.version if found else None

    def triggers(self) -> set[str]:
        return {t.name for t in self.types.values() if t.trigger}

    def listing(self, *, search: str = "", limit: int = MAX_LISTED) -> list[dict[str, Any]]:
        """What the model gets. Filtered, sorted, and bounded."""
        wanted = str(search or "").strip().lower()
        rows = [
            t
            for t in self.types.values()
            if not wanted or wanted in t.name.lower() or wanted in t.display_name.lower()
        ]
        # Catalogued-and-in-use first: a type this instance is already running
        # is the safest thing a model can reach for.
        rows.sort(key=lambda t: (t.catalogued, t.name))
        return [t.as_dict() for t in rows[: max(1, int(limit))]]

    def merge(self, others: list[NodeType]) -> None:
        """Add types, newest version wins, and `trigger` is never un-set."""
        for entry in others:
            existing = self.types.get(entry.name)
            if existing is None:
                self.types[entry.name] = entry
                continue
            self.types[entry.name] = NodeType(
                name=entry.name,
                version=max(existing.version, entry.version),
                catalogued=existing.catalogued or entry.catalogued,
                display_name=existing.display_name or entry.display_name,
                trigger=existing.trigger or entry.trigger,
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "count": len(self.types),
            "checked_at": self.checked_at,
        }


def harvest(workflows: list[dict[str, Any]]) -> list[NodeType]:
    """Every `(type, typeVersion)` pair in a pile of workflows.

    Deliberately tolerant: a workflow with a malformed node is somebody's real
    workflow, and refusing to learn from the other forty-nine because one is
    odd would be the wrong trade.
    """
    found: dict[str, float] = {}
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        for node in workflow.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            name = str(node.get("type") or "").strip()
            if not name:
                continue
            try:
                version = float(node.get("typeVersion") or 1)
            except (TypeError, ValueError):
                version = 1.0
            found[name] = max(found.get(name, 0.0), version)
    return [NodeType(name=n, version=v) for n, v in sorted(found.items())]


def read_catalogue(payload: Any) -> list[NodeType]:
    """`GET /rest/types/nodes.json` into node types.

    n8n hands back a list of `INodeTypeDescription`, where `version` is either
    a number or a list of them. The list form is the one that matters: a node
    supporting `[1, 1.1, 2]` should be written at 2, and a client that took
    the first element would ground the model on the oldest version there is.
    """
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out: list[NodeType] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            NodeType(
                name=name,
                version=_newest(row.get("version")),
                catalogued=True,
                display_name=str(row.get("displayName") or "").strip(),
                trigger=_is_trigger(row),
            )
        )
    return out


def _newest(value: Any) -> float:
    if isinstance(value, (list, tuple)) and value:
        versions = []
        for item in value:
            try:
                versions.append(float(item))
            except (TypeError, ValueError):
                continue
        return max(versions) if versions else 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _is_trigger(row: dict[str, Any]) -> bool:
    """n8n's own three markers, any of which makes a node a trigger."""
    if row.get("group") and "trigger" in [str(g).lower() for g in row.get("group") or []]:
        return True
    if row.get("polling") or row.get("trigger") or row.get("eventTriggerDescription") is not None:
        return True
    return str(row.get("name") or "").lower().endswith("trigger")


def _tidy(version: float) -> Any:
    """`2.0` reads as a mistake next to `2.1`. `2` does not."""
    return int(version) if float(version).is_integer() else round(float(version), 3)


async def load(
    client: N8nClient | None,
    session: N8nSession | None = None,
    *,
    existing: NodeCatalogue | None = None,
    force: bool = False,
) -> NodeCatalogue:
    """Build the catalogue from whichever sources are open.

    Never raises. A catalogue is an improvement to grounding, and an
    improvement that can fail a tool call is a downgrade.
    """
    catalogue = existing or NodeCatalogue()
    if catalogue.fresh and not force:
        return catalogue

    fresh = NodeCatalogue()
    sources: list[str] = []

    if client is not None:
        try:
            workflows, _cursor = await client.list_workflows(limit=HARVEST_WORKFLOWS)
            # The list endpoint returns whole workflows, nodes included, so
            # this is one request rather than fifty.
            fresh.merge(harvest(workflows))
            if fresh.types:
                sources.append("harvest")
        except N8nError as err:
            _LOGGER.debug("n8n: could not harvest node types: %s", err)

    if session is not None and session.configured:
        try:
            response = await session.request("GET", CATALOGUE_PATH)
            if response.status_code < 400 and response.content:
                fresh.merge(read_catalogue(response.json()))
                sources.append("catalogue")
        except (SessionError, ValueError) as err:
            _LOGGER.debug("n8n: could not read the node catalogue: %s", err)

    if not fresh.types:
        # Keep whatever was known before rather than blanking it: a transient
        # failure should not make Jarvis dumber than it was a minute ago.
        return catalogue
    fresh.source = "+".join(sources)
    fresh.checked_at = time.time()
    return fresh


# ---------------------------------------------------------------------------
# validating a workflow against what this instance actually has
# ---------------------------------------------------------------------------
ERROR = "error"
WARNING = "warning"


def validate(
    workflow: Any, catalogue: NodeCatalogue | None = None
) -> dict[str, Any]:
    """A dry run of `clean_workflow`, plus what the catalogue knows.

    A REPORT, not a gate — the same shape and the same rule as
    `automation/check.py`: `ok` is False only for an `error`, and a warning is
    information. The point is that a model can fix its own JSON in the next
    round rather than burning an approval to find out it invented a node.

    It never refuses something `clean_workflow` would accept. A validator
    stricter than the writer is a validator people learn to skip.
    """
    from .workflows import WorkflowError, clean_workflow

    findings: list[dict[str, str]] = []
    try:
        cleaned = clean_workflow(workflow)
    except WorkflowError as err:
        return {
            "ok": False,
            "findings": [{"level": ERROR, "where": "workflow", "message": str(err)}],
            "catalogue": (catalogue or NodeCatalogue()).as_dict(),
        }

    nodes = cleaned.payload.get("nodes") or []
    if catalogue is None or not catalogue.known:
        findings.append(
            {
                "level": WARNING,
                "where": "catalogue",
                "message": (
                    "Jarvis has no list of this instance's node types, so node "
                    "names and versions were not checked. They are checked "
                    "once the instance has been read at least once."
                ),
            }
        )
    else:
        findings.extend(_check_nodes(nodes, catalogue))

    if not _any_trigger(nodes, catalogue):
        findings.append(
            {
                "level": WARNING,
                "where": "trigger",
                "message": (
                    "No trigger node, so nothing will ever start this on its "
                    "own. That is fine for something meant to be called by "
                    "another workflow."
                ),
            }
        )

    for node_name, kind in cleaned.connections_needed:
        findings.append(
            {
                "level": WARNING,
                "where": node_name,
                "message": (
                    f"needs a {kind} credential attached in n8n before it can "
                    "run. Jarvis never attaches one."
                ),
            }
        )

    return {
        "ok": not any(f["level"] == ERROR for f in findings),
        "findings": findings,
        "catalogue": (catalogue or NodeCatalogue()).as_dict(),
        "nodes": len(nodes),
    }


def _check_nodes(nodes: list[Any], catalogue: NodeCatalogue) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "?")
        node_type = str(node.get("type") or "")
        known = catalogue.get(node_type)
        if known is None:
            findings.append(
                {
                    "level": WARNING,
                    "where": name,
                    # A warning and not an error: the catalogue may be a
                    # harvest, which only sees types already in use, and
                    # refusing a node nobody has used yet would be absurd.
                    "message": (
                        f"{node_type!r} is not a node type Jarvis has seen on "
                        "this n8n. Check the spelling, or install the "
                        "community package it comes from."
                    ),
                }
            )
            continue
        try:
            wanted = float(node.get("typeVersion") or 1)
        except (TypeError, ValueError):
            findings.append(
                {
                    "level": ERROR,
                    "where": name,
                    "message": "`typeVersion` has to be a number.",
                }
            )
            continue
        if wanted > known.version:
            findings.append(
                {
                    "level": WARNING,
                    "where": name,
                    "message": (
                        f"asks for {node_type} version {_tidy(wanted)}, and the "
                        f"newest here is {_tidy(known.version)}. n8n will load "
                        "it at the older version, which may not have the "
                        "fields this node sets."
                    ),
                }
            )
    return findings


def _any_trigger(nodes: list[Any], catalogue: NodeCatalogue | None) -> bool:
    """The catalogue answers this properly; without one, fall back.

    `workflows._has_trigger` guesses from the type name because the public API
    withholds the node description. With a real catalogue the guess is
    unnecessary, and the difference shows up on exactly the nodes a guess gets
    wrong — `n8n-nodes-base.emailReadImap` is a trigger and does not say so.
    """
    from .workflows import _has_trigger

    typed = [n for n in nodes if isinstance(n, dict)]
    if catalogue is not None and catalogue.known:
        known_triggers = catalogue.triggers()
        if known_triggers and any(
            str(n.get("type") or "") in known_triggers for n in typed
        ):
            return True
    return _has_trigger(typed)
