"""The fixture format: what to say, and what must be true afterwards.

One file per scenario, one scenario per capability-shaped thing a person would
actually do. The format is deliberately small — `say` and `expect` — because a
fixture language rich enough to express anything becomes a program nobody
reviews, and the assertions that matter here are about the *house*, not about
JSON shapes.

    name: hall-light-on
    capability: house
    gated-on: M18          # optional: this needs a milestone that is not done
    variants: [voice, text]
    setup:
      states: {light.hall: "off"}
    turns:
      - say: "Turn on the hall light"
        audio: {snr_db: 10, noise: fan}       # voice variants only
        expect:
          service: {domain: light, service: turn_on, entity_id: light.hall}
          state: {light.hall: "on"}
          reply_means: "confirms the hall light is now on"

`gated-on` is the whole reason the suite can be written before the system is:
a gated scenario is expected to fail, `--implemented-only` does not run it, and
full mode does. Nothing is ever "skipped" — `PROCESS.md` §2.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SCENARIO_DIR = HERE / "scenarios"

#: Everything a turn may assert. A key outside this set is a typo, and a typo
#: in an expectation is an assertion that silently never runs — which is worse
#: than a failing one.
EXPECT_KEYS = {
    "service",           # a service call happened: {domain, service, entity_id?, data?}
    "no_service",        # these domains/services must NOT have been called
    "state",             # entity_id -> state after the turn
    "reply_contains",    # substring, case-insensitive — for a literal fact
    "reply_matches",     # regex
    "reply_means",       # semantic, judged by a local model
    "reply_absent",      # a substring that must NOT appear (no invented facts)
    "transcript_wer",    # per-turn override of the WER ceiling
    "no_reply",          # nothing should have been said at all
    "wake_word",         # the detected wake word id, or false for "none"
    "task",              # {kind?, status?, title_contains?, steps_at_least?, within?}
    "schedule",          # {title_contains?, within?} — a reminder registered, not yet fired
    "no_task",           # no task was created by this turn
    "note",              # {title_contains?, body_contains?, citations_at_least?}
    "notification",      # {title_contains?, kind?, source?, within?}
    "surface",           # {entity?, kind?, count?, within?} — the voice screen's panels (M83/M92)
    "memory",            # {recalls?, forgotten?}
    "approval",          # {tool, decision: approve|deny|hold, within?} — hold leaves it for the next turn
    "ui",                # {testid, contains, within?} or a list of them — the page
                         # after the answer; only the voice-ui/text-ui variants look
    "file",              # {path, exists: bool} — containment checks
    "error",             # the turn failed, visibly: {contains?, code?}
    "within_seconds",    # the whole turn must finish inside this
    "capability",        # which capability the router should have chosen
    "extension",         # {key, enabled?, granted?, tool_offered?, tool_withheld?,
                         #  skill_offered?, skill_withheld?}
}

#: The API transports, and the two that drive the real console in a browser.
#: A scenario names the `-ui` ones only when it asserts on the page.
API_VARIANTS = ("voice", "text")
VARIANTS = API_VARIANTS + ("voice-ui", "text-ui")

#: Where a scenario can run. See `Scenario.ground`.
GROUNDS = ("stack", "fixture")


@dataclass
class Expectation:
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.raw) - EXPECT_KEYS
        if unknown:
            raise ValueError(
                f"unknown expectation key(s): {', '.join(sorted(unknown))} "
                f"(known: {', '.join(sorted(EXPECT_KEYS))})"
            )

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.raw

    def __bool__(self) -> bool:
        return bool(self.raw)


@dataclass
class Turn:
    say: str
    expect: Expectation
    audio: dict[str, Any] = field(default_factory=dict)
    #: Wait for this many seconds before the turn — a scheduled task's clock.
    wait: float = 0.0
    #: Send raw audio instead of speech: "silence" | "room_tone".
    sound: str = ""
    #: Say nothing, send nothing: wait, then assert on what happened by itself
    #: — a reminder firing, a task finishing. A silent audio turn is not the
    #: same thing: the pipeline answers silence with an STT error.
    observe: bool = False
    #: Start this turn in a NEW conversation, as a different thread would.
    #:
    #: The whole of `redteam-cross-conversation-leak`: without it, every turn
    #: in a scenario shares one conversation id, and "the second thread does
    #: not know what the first was told" cannot be asserted at all — the two
    #: turns ARE one thread.
    new_conversation: bool = False
    #: Restart jarvis-core before this turn (memory must survive it). On the
    #: stack ground that is `docker restart jarvis-core`; on the harness it is
    #: the process. Both answer the same question: what survived?
    restart: bool = False
    #: The night happens before this turn: `memory.reflect` is called on the
    #: house (M87), the way `restart` is a real restart. What survived, what was
    #: learned — both are questions about the house, not the scenario.
    reflect: bool = False
    #: Stop a container before this turn and bring it back at the end of the
    #: scenario. Stack ground only — there is nothing to kill on a harness.
    kill: str = ""
    #: Change something before speaking.
    #:
    #: Only `extension: {key, enabled?, permissions?}` today, and deliberately
    #: narrow: this is for asserting that an operator's decision reaches a
    #: conversation ALREADY IN PROGRESS, which is when somebody actually flips
    #: a switch. A general "call any service" key here would be a scenario
    #: format that can set up the very state it then asserts.
    do: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    capability: str
    turns: list[Turn]
    gated_on: str = ""
    variants: tuple[str, ...] = API_VARIANTS
    setup: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    path: Path | None = None
    #: Why this scenario exists — printed with a failure, so a red line in CI
    #: says what a person lost rather than only which assert tripped.
    intent: str = ""
    #: Seconds one turn of THIS scenario may take, overriding the rig's default.
    #: A fan-out to four specialists is minutes of real work; a light chat turn
    #: that took two minutes is a defect. One number for both would have to be
    #: the larger, and then the second case would never fail.
    timeout: float = 0.0
    #: Where it runs: `stack` (the operator's containers, the default) or
    #: `fixture` (a jarvis-core of our own, behind this repository's fixture
    #: web). Only a scenario whose assertions are about page content this
    #: repository owns needs the second — everything else is more honest run
    #: against the deployment.
    ground: str = "stack"

    @property
    def gated(self) -> bool:
        """Waiting on a milestone that has not landed.

        `gated-on` names a milestone; a scenario is gated while that milestone
        is unticked in `MILESTONES.md` and not a moment longer. This used to be
        `bool(self.gated_on)`, which made "gated" permanent: twenty-five
        scenarios written against M16, M18 and M25 were still being skipped by
        `--implemented-only` months after those milestones were ticked, and the
        one place they ran at all was each milestone's own `--capability`
        slice. Full mode ran them, and full mode had never been run.
        """
        return bool(self.gated_on) and self.gated_on not in ticked_milestones()


_TICKED: set[str] | None = None


def ticked_milestones(ledger: Path | None = None) -> set[str]:
    """Every `- [x] **Mnn` in MILESTONES.md, read once."""
    global _TICKED
    if _TICKED is None or ledger is not None:
        path = ledger or (Path(__file__).resolve().parents[2] / "MILESTONES.md")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        found = set(re.findall(r"^- \[x\] \*\*(M\d{2})", text, re.M))
        if ledger is not None:
            return found
        _TICKED = found
    return _TICKED


def _turn(raw: Any, index: int, name: str) -> Turn:
    if not isinstance(raw, dict):
        raise ValueError(f"{name}: turn {index} is not a mapping")
    say = str(raw.get("say") or "").strip()
    sound = str(raw.get("sound") or "")
    observe = bool(raw.get("observe"))
    if not say and not sound and not observe:
        raise ValueError(f"{name}: turn {index} says nothing and plays nothing")
    expect = raw.get("expect") or {}
    if not isinstance(expect, dict):
        raise ValueError(f"{name}: turn {index}'s expect is not a mapping")
    do = raw.get("do") or {}
    if not isinstance(do, dict):
        raise ValueError(f"{name}: turn {index}'s do is not a mapping")
    unknown_do = set(do) - {"extension", "mqtt_publish", "fixture_write"}
    if unknown_do:
        raise ValueError(
            f"{name}: turn {index} asks to do {', '.join(sorted(unknown_do))}, "
            "which the rig cannot do — only 'extension'"
        )
    return Turn(
        say=say,
        expect=Expectation(expect),
        audio=dict(raw.get("audio") or {}),
        wait=float(raw.get("wait") or 0.0),
        sound=sound,
        observe=observe,
        restart=bool(raw.get("restart")),
        reflect=bool(raw.get("reflect")),
        new_conversation=bool(raw.get("new_conversation")),
        kill=str(raw.get("kill") or ""),
        do=do,
    )


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: not a mapping")
    name = str(raw.get("name") or path.stem)
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"{name}: no turns")
    # A scenario that says nothing runs through the API both ways; the browser
    # variants are opted into, because they assert on a page.
    variants = tuple(str(v) for v in (raw.get("variants") or API_VARIANTS))
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        raise ValueError(f"{name}: unknown variant(s) {sorted(unknown)}")
    ground = str(raw.get("ground") or "stack")
    if ground not in GROUNDS:
        raise ValueError(f"{name}: unknown ground {ground!r} (known: {', '.join(GROUNDS)})")
    return Scenario(
        name=name,
        capability=str(raw.get("capability") or "unknown"),
        gated_on=str(raw.get("gated-on") or raw.get("gated_on") or ""),
        variants=variants,
        setup=dict(raw.get("setup") or {}),
        tags=tuple(str(t) for t in (raw.get("tags") or ())),
        intent=str(raw.get("intent") or "").strip(),
        ground=str(raw.get("ground") or "stack"),
        timeout=float(raw.get("timeout") or 0.0),
        turns=[_turn(turn, i, name) for i, turn in enumerate(turns)],
        path=path,
    )


def load_all(directory: str | Path = SCENARIO_DIR) -> list[Scenario]:
    directory = Path(directory)
    out = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
    names = [s.name for s in out]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(f"duplicate scenario name(s): {sorted(duplicates)}")
    return out
