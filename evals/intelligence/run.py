#!/usr/bin/env python3
"""The intelligence scorecard: is it any good, measured rather than felt.

Everything else in this repository asks whether a mechanism works. This asks
the question a person asks after a week of living with it — does it follow what
I said, pick the right tool, reason past one step, do what I asked in the shape
I asked for, admit what it cannot do, and answer fast enough to talk to. Six
sections, a fixed prompt set (`prompts.yaml`), one number each.

    python3 evals/intelligence/run.py --out .verify/live/scorecard.json

How it is run, and why:

* **Through the voice pipeline.** Every prompt is spoken by Piper, heard by the
  real Whisper, answered by the real model and said back through Piper. A mis-
  heard prompt is a wrong answer here, exactly as it is in the kitchen.
* **Scored on what Jarvis produced, not on what came back through the speaker.**
  The text assertions read `reply_text`. Running a regex over a transcription of
  synthetic speech measures the recogniser — "16 °C" and "sixteen degrees" are
  the same answer and different strings. The spoken half is not ignored: the
  round trip's WER is measured on every turn and reported beside the scores,
  because an answer nobody can make out is also a failure, just a different one.
* **Deterministic wherever the state is inspectable.** A service call, an entity
  state, which tools ran, how many words. The judge (`testing/live/judge.py`)
  is used only for meaning, its reasons are logged, and a judge that cannot be
  reached is an error rather than a pass.
* **On its own harness**, with this repository's fixture web behind it, so the
  research prompt has pages whose content we own and the coding prompt has a
  repository it is allowed to ruin. It never touches the operator's house.

Nothing here is approved. Every held action is DENIED — the coding prompt is
scored on the fact that it reached `start_coding_job` at all, which the gate
records before it blocks. A scorecard that started real jobs would measure the
jobs.

Latency is measured twice over the same four probes: idle, then again with a
real research task running on the same model server. The second pass is the
honest one; a number measured on an idle box is the number you never get.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "jarvis-core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from testing.harness import JarvisClient  # noqa: E402
from testing.live import LiveError  # noqa: E402
from testing.live import audio as audio_mod  # noqa: E402
from testing.live.capability import HOUSE_DOMAINS, capability_of  # noqa: E402
from testing.live.ground import HarnessGround  # noqa: E402
from testing.live.judge import Judge  # noqa: E402
from testing.live.report import wer, write_json  # noqa: E402
from testing.live.rig import the_rig  # noqa: E402
from testing.live.transport import TURN_TIMEOUT, ApiVoice, Link  # noqa: E402
from testing.live.voice import Ears, Mouth, services_are_up  # noqa: E402
from testing.live.web import FixtureWeb  # noqa: E402
from testing.live.world import Observer  # noqa: E402

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts.yaml"

#: The sections, in the order the scorecard prints them.
SECTIONS = ("context_retention", "routing", "reasoning", "instructions", "graceful_failure")

#: What each section has to score for the eval to pass. Floors, not targets.
#:
#: Set from the first full run on this host (see `docs/verification.md`) and
#: deliberately below it: a floor at the measurement is a floor that fails on
#: the next run for no reason, and one far below it stops meaning anything. The
#: rule for moving these is `PROCESS.md`'s — re-measure, never edit to taste.
FLOORS = {
    "context_retention": 0.75,
    "routing": 0.85,
    "reasoning": 0.60,
    "instructions": 0.80,
    "graceful_failure": 0.80,
}

#: A task the server considers to be in flight.
RUNNING = ("running", "queued", "pending", "waiting")

#: Latency ceilings, in seconds, on the MEDIAN of the four probes. `total` is
#: the whole turn: speech in, answer out, speech back. `ttft` is when the
#: answer started, which is what a person actually waits for.
#:
#: Two numbers per stage because the second is the one that matters — a box
#: that answers in three seconds when idle and forty when a research job is
#: running is a box that is never idle when you want it.
CEILINGS = {
    "idle": {"ttft": 18.0, "total": 35.0},
    "under_load": {"ttft": 22.0, "total": 45.0},
}
#: Where those numbers come from. Four runs on this host on 2026-08-25, median
#: of the same four probes each time:
#:
#:     idle       first word  6.2  8.1  5.5  10.8      whole turn  8.0  9.4  7.1  13.5
#:     under load first word  ---  6.4  ---   7.2      whole turn  ---  9.3  ---  12.2
#:
#: The spread is the model server, which is shared and over a tailnet, not the
#: code: the same probe took 3.8 s and 15.6 s to its first word in one run. The
#: ceilings sit above the worst of those with room for a slow afternoon,
#: because a ceiling that fails on a busy hour is one people learn to re-run
#: rather than read. They are still low enough that a change which doubled a
#: number would fail, which is the regression this is here to catch.

#: How far the loudspeaker may be from the words Jarvis chose, averaged. Not a
#: section score — reported beside them, because it is a different failure.
WER_CEILING = 0.20


@dataclass
class CaseResult:
    section: str
    name: str
    said: list[str] = field(default_factory=list)
    #: What Whisper made of each thing that was said. Kept because it is the
    #: first thing to look at when a voice turn goes wrong: an assistant that
    #: answered the wrong question usually heard one.
    transcripts: list[str] = field(default_factory=list)
    reply: str = ""
    heard: str = ""
    routed: str = ""
    expected: str = ""
    failures: list[str] = field(default_factory=list)
    judged: list[dict[str, Any]] = field(default_factory=list)
    #: How far the loudspeaker was from the words Jarvis wrote (the way out).
    wer: float | None = None
    #: How far Whisper was from the words the eval spoke (the way in).
    wer_in: float | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "said": self.said,
            "transcripts": self.transcripts,
            "reply": self.reply,
            "heard": self.heard,
            "routed": self.routed or None,
            "expected": self.expected or None,
            "failures": self.failures,
            "judged": self.judged,
            "wer": self.wer,
            "wer_in": self.wer_in,
            "seconds": round(self.seconds, 2),
        }


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", str(text).strip()) if w]


def sentences(text: str) -> list[str]:
    """Sentences, as a listener would count them.

    Abbreviations are not split on, because "Sgt. Wilson" is one sentence and a
    naive split scored a perfectly obedient one-sentence answer as two.
    """
    body = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|Sgt|St|approx|etc|e\.g|i\.e)\.", r"\1", str(text), flags=re.I
    )
    return [s for s in re.split(r"[.!?]+(?:\s|$)", body) if s.strip()]


class Intelligence:
    """One harness, one conversation at a time, six sections."""

    def __init__(self, *, keep: bool = False, verbose: bool = False,
                 only: str = "") -> None:
        self.keep = keep
        self.verbose = verbose
        self.only = {name for name in only.split(",") if name}
        self.cases: list[CaseResult] = []
        self.judge = Judge()
        self.mouth = Mouth()
        self.ears = Ears()
        self.web = FixtureWeb()
        self.ground: HarnessGround | None = None
        self.client: JarvisClient | None = None
        self.observer: Observer | None = None
        self.voice: ApiVoice | None = None
        self.latency: dict[str, list[dict[str, Any]]] = {"idle": [], "under_load": []}
        self.load_task: dict[str, Any] | None = None
        #: Whether the idle pass was actually idle — see `_quiesce`.
        self.idle_quiet = True
        self._denier: asyncio.Task | None = None

    # --- the rig ----------------------------------------------------------
    async def preflight(self) -> None:
        up = await services_are_up()
        missing = [name for name, ok in up.items() if not ok]
        if missing:
            raise LiveError(
                f"the voice services are not running: {', '.join(missing)}. "
                "This eval speaks and listens for real; there is no fake to fall back to."
            )
        if not self.judge.available():
            raise LiveError(
                "LLM_URL and LLM_MODEL must point at the local model server (source .env)"
            )

    async def start(self) -> None:
        self.ground = HarnessGround(verbose=self.verbose, keep=True, web=self.web.start())
        self.ground.start()
        # Long enough for the slowest thing here: a research prompt on a loaded
        # box is minutes, and a client timeout shorter than the turn reads as a
        # server failure rather than a slow answer.
        self.client = JarvisClient(self.ground.base_url, self.ground.token, timeout=TURN_TIMEOUT)
        await self.client.connect()
        self.link = Link(self.client)
        self.observer = await Observer(self.client).start()
        self.voice = ApiVoice(self.link, self.ground, self.mouth, self.ears)
        self._denier = asyncio.create_task(self._deny_everything())

    async def stop(self) -> None:
        if self._denier is not None:
            self._denier.cancel()
        if self.observer is not None:
            await self.observer.stop()
        if self.client is not None:
            await self.client.aclose()
        if self.ground is not None:
            self.ground.stop()
        self.web.stop()

    async def _deny_everything(self) -> None:
        """Say no to every held action, promptly.

        Not politeness towards the gate: a Tier-3 tool BLOCKS until somebody
        answers, so an eval that ignored the prompt would score "no answer in
        180 seconds" as a routing failure when the routing was right and the
        gate was working.
        """
        seen: set[str] = set()
        while True:
            for row in list(self.observer.approvals if self.observer else []):
                if row["request_id"] in seen:
                    continue
                seen.add(row["request_id"])
                await self.observer.answer(row["request_id"], False)
            await asyncio.sleep(0.4)

    # --- one turn ---------------------------------------------------------
    async def ask(self, said: str, conversation_id: str | None, *,
                  garble: bool = False, timeout: float = TURN_TIMEOUT):
        pcm = rate = None
        if garble:
            # Spoken over a fan at 5 dB SNR: a real misheard sentence, rather
            # than a tidy nonsense string that Whisper transcribes perfectly.
            utterance = self.mouth.say(said)
            pcm = audio_mod.add_noise(utterance.pcm, 5.0, rate=utterance.rate, shape="fan")
            rate = utterance.rate
        return await self.voice.say(
            said, pcm=pcm, rate=rate, conversation_id=conversation_id, timeout=timeout
        )

    async def check(self, expect: dict[str, Any], turn, mark: int, tool_mark: int,
                    started_at: float, result: CaseResult) -> None:
        """Every assertion in one `expect` block, deterministic ones first."""
        reply = turn.reply_text or ""
        observer = self.observer
        assert observer is not None

        def fail(why: str) -> None:
            result.failures.append(why)

        low = reply.lower()
        for needle in expect.get("contains") or []:
            if str(needle).lower() not in low:
                fail(f"the reply does not contain {needle!r}: {reply[:160]!r}")
        wanted_any = expect.get("contains_any") or []
        if wanted_any and not any(str(n).lower() in low for n in wanted_any):
            fail(f"the reply contains none of {wanted_any}: {reply[:160]!r}")
        for needle in expect.get("absent") or []:
            if str(needle).lower() in low:
                fail(f"the reply contains {needle!r} and should not: {reply[:160]!r}")
        pattern = expect.get("matches")
        if pattern and not re.search(str(pattern), reply, re.IGNORECASE | re.MULTILINE):
            fail(f"the reply does not match /{pattern}/: {reply[:160]!r}")
        if expect.get("max_words") and len(words(reply)) > int(expect["max_words"]):
            fail(f"{len(words(reply))} words, asked for at most {expect['max_words']}: {reply[:160]!r}")
        if expect.get("max_sentences"):
            count = len(sentences(reply))
            if count > int(expect["max_sentences"]):
                fail(f"{count} sentences, asked for at most {expect['max_sentences']}: {reply[:160]!r}")

        if expect.get("no_house_calls"):
            moved = [
                f"{c.domain}.{c.service}({', '.join(c.entity_ids) or '—'})"
                for c in observer.calls_since(mark)
                if c.domain in HOUSE_DOMAINS
            ]
            if moved:
                fail(f"the house moved and should not have: {', '.join(moved)}")

        for entity_id, want in (expect.get("state") or {}).items():
            # Read back from the server, not from the reply: "the bed light is
            # on" is a sentence, and the state is the fact.
            got = await self.client.wait_for_state(entity_id, str(want), timeout=15.0)
            if not got:
                current = (await self.client.state(entity_id) or {}).get("state")
                fail(f"{entity_id} is {current!r}, expected {want!r}")

        # Routing is read off what ran, in every section — a reasoning prompt
        # that quietly started a background job is worth knowing about even
        # where the section does not score it.
        fresh = [
            task for task in await observer.tasks()
            if float(task.get("created") or 0) >= started_at
        ]
        kinds = [str(t.get("kind") or "") for t in fresh]
        calls = [f"{c.domain}.{c.service}" for c in observer.calls_since(mark)]
        tools = observer.tools_since(tool_mark)
        # A held tool is a routed tool: `jarvis_tool_started` fires before the
        # gate blocks, so denying the coding job still proves where it went.
        held = [str(row.get("tool") or "") for row in observer.approvals]
        result.routed = capability_of(kinds, calls, tools + held, reply)
        if expect.get("capability"):
            result.expected = str(expect["capability"])
            if result.routed != result.expected:
                fail(
                    f"routed to {result.routed!r}, expected {result.expected!r} "
                    f"(tools: {tools or 'none'}; calls: {calls or 'none'})"
                )

        criterion = expect.get("means")
        if criterion:
            verdict = await self.judge.check(str(criterion).strip(), reply)
            result.judged.append({"ok": verdict.ok, "why": verdict.why, "criterion": criterion})
            if not verdict.ok:
                fail(f"judge: {verdict.why} (criterion: {str(criterion).strip()[:90]})")

    async def run_case(self, section: str, name: str, turns: list[dict[str, Any]]) -> CaseResult:
        result = CaseResult(section=section, name=name)
        started = time.monotonic()
        conversation_id: str | None = None
        wers: list[float] = []
        wers_in: list[float] = []
        try:
            for turn_spec in turns:
                said = str(turn_spec["say"]).strip()
                mark, tool_mark = self.observer.mark(), self.observer.tool_mark()
                started_at = time.time() - 5.0
                spoken = await self.ask(
                    said, conversation_id, garble=bool(turn_spec.get("garble"))
                )
                conversation_id = spoken.conversation_id or conversation_id
                result.said.append(said)
                result.transcripts.append(spoken.transcript or "")
                if spoken.transcript:
                    wers_in.append(wer(said, spoken.transcript))
                result.reply = spoken.reply_text or ""
                result.heard = spoken.reply_heard or ""
                if spoken.error:
                    result.failures.append(f"the turn failed: {spoken.error}")
                    break
                if spoken.reply_text and spoken.reply_heard:
                    wers.append(wer(spoken.reply_text, spoken.reply_heard))
                expect = turn_spec.get("expect") or {}
                if expect:
                    await self.check(expect, spoken, mark, tool_mark, started_at, result)
        except LiveError:
            raise
        except Exception as err:  # noqa: BLE001 - a crash is a failed case
            result.failures.append(f"{type(err).__name__}: {err}")
        result.wer = round(statistics.mean(wers), 3) if wers else None
        result.wer_in = round(statistics.mean(wers_in), 3) if wers_in else None
        result.seconds = time.monotonic() - started
        self.cases.append(result)
        flag = "ok  " if result.ok else "FAIL"
        print(f"  {flag} {section}/{name} {result.seconds:.1f}s", flush=True)
        for why in result.failures:
            print(f"       · {why}", flush=True)
        return result

    # --- the sections -----------------------------------------------------
    async def run_sections(self, data: dict[str, Any]) -> None:
        for section in SECTIONS:
            if self.only and section not in self.only:
                continue
            rows = data.get(section) or []
            print(f"\n{section} ({len(rows)} case(s))", flush=True)
            for index, row in enumerate(rows):
                if section == "routing":
                    name = f"route-{index + 1}"
                    turns = [{"say": row["say"], "expect": row.get("expect") or {}}]
                else:
                    name = str(row.get("name") or f"case-{index + 1}")
                    turns = row.get("turns") or [
                        {"say": row["say"], "expect": row.get("expect") or {},
                         "garble": row.get("garble")}
                    ]
                await self.run_case(section, name, turns)

    # --- latency ----------------------------------------------------------
    async def run_latency(self, data: dict[str, Any]) -> None:
        probes = data.get("latency") or []
        if self.only and "latency" not in self.only:
            return
        print(f"\nlatency, idle ({len(probes)} probe(s))", flush=True)
        # An idle number measured while the routing section's sensor audit was
        # still running is not an idle number, and the first run of this eval
        # measured exactly that. Everything is cancelled and the box is waited
        # out before the word "idle" is used.
        stragglers = await self._quiesce()
        self.idle_quiet = not stragglers
        if stragglers:
            print(f"       · {len(stragglers)} task(s) would not stop: {stragglers}", flush=True)
        await self._probe(probes, "idle")

        print("\nlatency, under load", flush=True)
        load = str((data.get("load") or {}).get("say") or "").strip()
        # `{{handbook}}` → the fixture web's address for this run, as the rig's
        # runner does for its scenarios: a name the house has no note of is a
        # question back through the approval channel this eval denies, and
        # then no job runs and the pass is "not measurable" (26 Aug, twice).
        for name, url in (getattr(self.ground, "web", None) or {}).items():
            load = load.replace("{{" + str(name) + "}}", str(url).rstrip("/"))
        before = {str(task.get("id")) for task in await self.observer.tasks()}
        spoken = await self.ask(load, None)
        await asyncio.sleep(2.0)
        # A NEW task, not any running one: the first run reported a leftover
        # from an earlier section as the load, which is a real background job
        # and is not the one this pass asked for.
        running = [
            task for task in await self.observer.tasks()
            if str(task.get("status")) in RUNNING and str(task.get("id")) not in before
        ]
        self.load_task = running[0] if running else None
        if not running:
            # Not a warning: the second half of this measurement is the half
            # that matters, and "we measured idle twice" must not be able to
            # pass. See PROCESS.md §2 — there is no skip.
            print("       · no background task started; the load pass is not measurable",
                  flush=True)
            print(f"       · it said: {(spoken.reply_text or '')[:160]!r}", flush=True)
        else:
            print(f"       · load: {running[0].get('title')!r} ({running[0].get('kind')})",
                  flush=True)
            await self._probe(probes, "under_load")
            for task in running:
                try:
                    await self.client.command("jarvis/tasks/cancel", task_id=task["id"])
                except Exception:  # noqa: BLE001 - the harness dies with the eval anyway
                    pass

    async def _quiesce(self, timeout: float = 180.0) -> list[str]:
        """Cancel everything still in flight, and wait until nothing is.

        Returns the ids of anything that would not stop, which is a failure of
        the measurement rather than of the assistant — and is reported as one.
        """
        deadline = time.monotonic() + timeout
        while True:
            running = [
                task for task in await self.observer.tasks()
                if str(task.get("status")) in RUNNING
            ]
            if not running:
                return []
            if time.monotonic() > deadline:
                return [str(task.get("id")) for task in running]
            for task in running:
                try:
                    await self.client.command("jarvis/tasks/cancel", task_id=task["id"])
                except Exception:  # noqa: BLE001 - it may have finished as we asked
                    pass
            await asyncio.sleep(2.0)

    async def _probe(self, probes: list[dict[str, Any]], condition: str) -> None:
        for probe in probes:
            spoken = await self.ask(str(probe["say"]).strip(), None)
            row = {
                "name": str(probe.get("name") or probe["say"])[:40],
                "stt": spoken.latency.get("stt"),
                "ttft": spoken.latency.get("ttft"),
                "tts_request": spoken.latency.get("tts_request"),
                "total": spoken.latency.get("total"),
                "spoke": spoken.spoke,
            }
            self.latency[condition].append(row)
            print(
                f"  {row['name']}: stt {row['stt'] or 0:.1f}s  "
                f"ttft {row['ttft'] or 0:.1f}s  total {row['total'] or 0:.1f}s",
                flush=True,
            )


# ---------------------------------------------------------------------------
# the scorecard
# ---------------------------------------------------------------------------
def _median(values: list[float | None]) -> float | None:
    real = [float(v) for v in values if isinstance(v, (int, float)) and v > 0]
    return round(statistics.median(real), 2) if real else None


def scorecard(rig: Intelligence) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section in SECTIONS:
        rows = [c for c in rig.cases if c.section == section]
        passed = sum(1 for c in rows if c.ok)
        out[section] = {
            "passed": passed,
            "total": len(rows),
            "rate": round(passed / len(rows), 3) if rows else None,
            "floor": FLOORS[section],
            "cases": [c.as_dict() for c in rows],
        }
    routed = [c for c in rig.cases if c.expected]
    out["routing"]["accuracy"] = (
        round(sum(1 for c in routed if c.routed == c.expected) / len(routed), 3)
        if routed else None
    )
    stages = ("stt", "ttft", "tts_request", "total")
    out["latency"] = {
        condition: {
            "n": len(rows),
            **{stage: _median([row.get(stage) for row in rows]) for stage in stages},
            "probes": rows,
            "ceilings": CEILINGS[condition],
            **({"quiet": rig.idle_quiet} if condition == "idle" else {}),
        }
        for condition, rows in rig.latency.items()
    }
    out["latency"]["load"] = (
        {"title": rig.load_task.get("title"), "kind": rig.load_task.get("kind")}
        if rig.load_task else None
    )
    heard = [c.wer for c in rig.cases if c.wer is not None]
    spoken_in = [c.wer_in for c in rig.cases if c.wer_in is not None]
    out["speech"] = {
        "wer_mean": round(statistics.mean(heard), 3) if heard else None,
        # The way in, reported beside it and NOT gated: the garbled case is
        # deliberately unintelligible, so a ceiling on this number would be a
        # ceiling on a test that exists to be failed by the recogniser.
        "wer_in_mean": round(statistics.mean(spoken_in), 3) if spoken_in else None,
        "wer_ceiling": WER_CEILING,
        "turns": len(heard),
    }
    out["judge"] = [j for c in rig.cases for j in c.judged]
    return out


def problems(card: dict[str, Any]) -> list[str]:
    """Everything that is under its floor or over its ceiling, in words."""
    out: list[str] = []
    for section in SECTIONS:
        row = card[section]
        if row["rate"] is None:
            out.append(f"{section}: nothing ran, so its floor cannot be met")
        elif row["rate"] < row["floor"]:
            out.append(
                f"{section} {row['rate']:.0%} is under its {row['floor']:.0%} floor "
                f"({row['passed']}/{row['total']})"
            )
    for condition, ceilings in CEILINGS.items():
        row = card["latency"][condition]
        if not row["n"]:
            out.append(f"latency {condition}: not measured, so its ceiling cannot be met")
            continue
        if condition == "idle" and row.get("quiet") is False:
            out.append(
                "latency idle: something was still running, so these are not idle numbers"
            )
        for stage, limit in ceilings.items():
            value = row.get(stage)
            if value is None:
                out.append(f"latency {condition}: {stage} was never reported")
            elif value > limit:
                out.append(f"latency {condition} {stage} {value:.1f}s is over the {limit:.0f}s ceiling")
    speech = card["speech"]
    if speech["wer_mean"] is None:
        out.append("speech: nothing was heard back, so WER could not be measured")
    elif speech["wer_mean"] > WER_CEILING:
        out.append(
            f"speech: WER {speech['wer_mean']:.3f} is over the {WER_CEILING} ceiling — "
            "the answers were right and the loudspeaker was not"
        )
    return out


def markdown(card: dict[str, Any]) -> str:
    lines = [
        "# Intelligence scorecard",
        "",
        "Measured through the full voice pipeline against the real Whisper, Piper and",
        "model this host runs. `evals/intelligence/run.py`; the prompt set is",
        "`evals/intelligence/prompts.yaml`.",
        "",
        "| Section | Score | Floor | |",
        "| --- | --- | --- | --- |",
    ]
    for section in SECTIONS:
        row = card[section]
        rate = "—" if row["rate"] is None else f"{row['rate']:.0%} ({row['passed']}/{row['total']})"
        met = "ok" if row["rate"] is not None and row["rate"] >= row["floor"] else "**under**"
        lines.append(f"| {section.replace('_', ' ')} | {rate} | {row['floor']:.0%} | {met} |")
    lines += [
        "",
        "| Latency (median) | STT | first word | speaking | whole turn |",
        "| --- | --- | --- | --- | --- |",
    ]
    for condition in ("idle", "under_load"):
        row = card["latency"][condition]
        cells = [
            f"{row[stage]:.1f}s" if row.get(stage) else "—"
            for stage in ("stt", "ttft", "tts_request", "total")
        ]
        lines.append(f"| {condition.replace('_', ' ')} | " + " | ".join(cells) + " |")
    load = card["latency"]["load"]
    lines += [
        "",
        f"Under load means: {load['title']!r} ({load['kind']}) running on the same model "
        f"server." if load else "Under load: NOT MEASURED — no background task started.",
        "",
        f"Round-trip WER (what Piper said against what Jarvis wrote): "
        f"{card['speech']['wer_mean']}" if card["speech"]["wer_mean"] is not None
        else "Round-trip WER: not measured.",
        "",
        "## Failures",
        "",
    ]
    failed = [
        (section, case)
        for section in SECTIONS
        for case in card[section]["cases"]
        if not case["ok"]
    ]
    if not failed:
        lines.append("None.")
    for section, case in failed:
        lines.append(f"* **{section}/{case['name']}** — {case['failures'][0]}")
    return "\n".join(lines) + "\n"


async def _main(args: argparse.Namespace) -> int:
    data = yaml.safe_load(PROMPTS.read_text(encoding="utf-8"))
    rig = Intelligence(keep=args.keep, verbose=args.verbose, only=args.only)
    await rig.preflight()
    started = time.monotonic()
    with the_rig():
        await rig.start()
        try:
            await rig.run_sections(data)
            await rig.run_latency(data)
        finally:
            await rig.stop()

    card = scorecard(rig)
    card["seconds"] = round(time.monotonic() - started, 1)
    out = Path(args.out)
    write_json(out, card)
    out.with_suffix(".md").write_text(markdown(card), encoding="utf-8")

    print("\n" + markdown(card))
    trouble = problems(card)
    for line in trouble:
        print(f"threshold: {line}")
    print(f"scorecard: {out}")
    return 1 if trouble else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO / ".verify" / "live" / "scorecard.json"))
    parser.add_argument("--only", default="", help="comma-separated section names")
    parser.add_argument("--keep", action="store_true", help="keep the harness work dir")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main(args))
    except LiveError as err:
        print(f"the rig could not run: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
