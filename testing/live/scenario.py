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
    "no_task",           # no task was created by this turn
    "note",              # {title_contains?, body_contains?, citations_at_least?}
    "notification",      # {title_contains?, kind?, source?, within?}
    "memory",            # {recalls?, forgotten?}
    "approval",          # {tool, decision: approve|deny}
    "ui",                # {testid, contains?, visible?} — asserted in the browser
    "file",              # {path, exists: bool} — containment checks
    "error",             # the turn failed, visibly: {contains?, code?}
    "within_seconds",    # the whole turn must finish inside this
    "capability",        # which capability the router should have chosen
}

VARIANTS = ("voice", "text")

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
    #: Restart jarvis-core before this turn (memory must survive it). On the
    #: stack ground that is `docker restart jarvis-core`; on the harness it is
    #: the process. Both answer the same question: what survived?
    restart: bool = False
    #: Stop a container before this turn and bring it back at the end of the
    #: scenario. Stack ground only — there is nothing to kill on a harness.
    kill: str = ""


@dataclass
class Scenario:
    name: str
    capability: str
    turns: list[Turn]
    gated_on: str = ""
    variants: tuple[str, ...] = VARIANTS
    setup: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    path: Path | None = None
    #: Why this scenario exists — printed with a failure, so a red line in CI
    #: says what a person lost rather than only which assert tripped.
    intent: str = ""
    #: Where it runs: `stack` (the operator's containers, the default) or
    #: `fixture` (a jarvis-core of our own, behind this repository's fixture
    #: web). Only a scenario whose assertions are about page content this
    #: repository owns needs the second — everything else is more honest run
    #: against the deployment.
    ground: str = "stack"

    @property
    def gated(self) -> bool:
        return bool(self.gated_on)


def _turn(raw: Any, index: int, name: str) -> Turn:
    if not isinstance(raw, dict):
        raise ValueError(f"{name}: turn {index} is not a mapping")
    say = str(raw.get("say") or "").strip()
    sound = str(raw.get("sound") or "")
    if not say and not sound:
        raise ValueError(f"{name}: turn {index} says nothing and plays nothing")
    expect = raw.get("expect") or {}
    if not isinstance(expect, dict):
        raise ValueError(f"{name}: turn {index}'s expect is not a mapping")
    return Turn(
        say=say,
        expect=Expectation(expect),
        audio=dict(raw.get("audio") or {}),
        wait=float(raw.get("wait") or 0.0),
        sound=sound,
        restart=bool(raw.get("restart")),
        kill=str(raw.get("kill") or ""),
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
    variants = tuple(str(v) for v in (raw.get("variants") or VARIANTS))
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
