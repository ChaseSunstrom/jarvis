"""Run the scenarios. This is the suite.

    python3 -m testing.live.runner --implemented-only
    python3 -m testing.live.runner --full --report docs/LIVE_TEST_REPORT.md

What it talks to: the containers this host actually runs — jarvis-core, the
console on :8199, the real Whisper, the real Piper, the real model. Not a copy
of them. A suite that only ever spoke to a jarvis-core it started itself is how
this host ran a console that had reported *unhealthy* for two days with every
test in the repository green.

The exception is a scenario whose assertions are about page content this
repository owns — every research one — which says `ground: fixture` and gets a
throwaway jarvis-core behind the fixture web. `--target harness` puts
everything there, for a machine with no stack up.

Running against somebody's real house is only reasonable because of what
surrounds it: the named volumes and config directory are snapshotted before the
first word and restored after the last, every thread the suite opens is named
`test:…`, and anything a scenario creates is deleted and its absence asserted
before the next one starts.

What it refuses to do: skip. A scenario whose capability does not exist yet is
marked `gated-on: <milestone>` in its own fixture and is not selected by
`--implemented-only`; in full mode it runs and it fails. There is no third
outcome, and `PROCESS.md` §2 says why.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra in (REPO_ROOT, REPO_ROOT / "jarvis-core"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from testing.live import LiveError  # noqa: E402
from testing.live import audio as audio_mod  # noqa: E402
from testing.live.capability import (  # noqa: E402
    HOUSE_DOMAINS,
    capability_of as _capability_of,
)
from testing.live.judge import Judge  # noqa: E402
from testing.live.rig import the_rig as _the_rig  # noqa: E402
from testing.live.report import (  # noqa: E402
    ScenarioResult,
    TurnResult,
    latency_table,
    summarise,
    wer,
    write_json,
)
from testing.live.scenario import Scenario, load_all  # noqa: E402
from testing.live.ground import (  # noqa: E402
    TEST_NAMESPACE,
    Ground,
    HarnessGround,
    StackGround,
)
from testing.live.web import FixtureWeb  # noqa: E402
from testing.live.transport import (  # noqa: E402
    TURN_TIMEOUT,
    ApiVoice,
    Browser,
    Link,
    Text,
    Turn,
)
from testing.live.voice import Ears, Mouth, services_are_up  # noqa: E402
from testing.live.world import Observer  # noqa: E402

OUT_DIR = REPO_ROOT / ".verify" / "live"

#: The ceiling a turn's transcript may not exceed unless it says otherwise.
DEFAULT_WER = 0.25

#: Full-mode thresholds, from the brief. `--implemented-only` does not apply
#: them: a suite that is deliberately partial cannot have a meaningful rate.
THRESHOLDS = {
    "intent_accuracy": 0.95,
    "wer_mean": 0.10,
    "routing_accuracy": 0.90,
    "round_trip_median": 2.0,
}


def _ui_probes(expect: dict[str, Any]) -> list[dict[str, Any]]:
    """The `ui:` expectations of a turn as probes for the browser turn.

    One mapping or a list of them, each `{testid, contains, within}`; `within`
    is seconds (default 30). Only a browser transport acts on these — the API
    transports accept and ignore the argument.
    """
    raw = expect.get("ui")
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [
        {
            "testid": str(item.get("testid") or ""),
            "contains": str(item.get("contains") or ""),
            "withinMs": int(float(item.get("within") or 30.0) * 1000),
        }
        for item in items
    ]


class Runner:
    def __init__(
        self,
        scenarios: list[Scenario],
        *,
        variants: tuple[str, ...] = ("voice", "text"),
        browser: bool = True,
        keep: bool = False,
        verbose: bool = False,
        target: str = "stack",
        protect: bool = True,
    ) -> None:
        self.scenarios = scenarios
        self.variants = variants
        self.want_browser = browser
        self.keep = keep
        self.verbose = verbose
        #: Which ground scenarios prefer: the running containers, or a
        #: jarvis-core of our own. `stack` is the default because a suite that
        #: only ever talks to a core it started proves nothing about the one
        #: the operator runs — which is how a console sat unhealthy for two
        #: days with every test green.
        self.target = target
        #: Snapshot the real house before touching it, and put it back after.
        self.protect = protect
        #: Set once a stack ground has been used, so the end-of-run log gate
        #: knows both that there is a stack and when the run began.
        self.stack_ground: StackGround | None = None
        self.results: list[ScenarioResult] = []
        #: Rebuilt whenever a scenario restarts the server.
        self.link: Link | None = None
        self.observer: Observer | None = None
        #: The fixture web, started with the harness and stopped with it.
        self.web = FixtureWeb()
        self.judge = Judge()
        #: Background "keep clicking approve" loops, one per scenario that
        #: approved something. Cancelled when the scenario ends, so a later one
        #: never inherits a hand that says yes to everything.
        self._approvers: list[asyncio.Task] = []
        #: How far through the held actions this scenario has answered. A
        #: scenario is one conversation, so the cursor is per scenario rather
        #: than per turn.
        self._approval_cursor = 0
        self.mouth = Mouth()
        self.ears = Ears()

    # --- the rig ----------------------------------------------------------
    async def _preflight(self) -> None:
        up = await services_are_up()
        missing = [name for name, ok in up.items() if not ok]
        if missing:
            raise LiveError(
                f"the voice services are not running: {', '.join(missing)}. "
                "The live rig uses the REAL Whisper/Piper/openWakeWord this host runs "
                "(ports 10300/10200/10400); there is no fake to fall back to here."
            )
        if not self.judge.available():
            raise LiveError(
                "LLM_URL and LLM_MODEL must point at the local model server "
                "(source .env); the rig runs Jarvis against the real model"
            )

    def _ground_for(self, scenario: Scenario) -> str:
        """Which ground this scenario belongs on.

        A scenario says `ground: fixture` when its answers have to come from
        pages this repository owns — every research scenario does, because
        "did it cite three independent sources" is a question about a web we
        control, not about today's internet. Everything else runs against the
        containers the operator actually runs.
        """
        if self.target == "harness":
            return "harness"
        return "harness" if scenario.ground == "fixture" else "stack"

    def _make_ground(self, name: str) -> Ground:
        if name == "stack":
            ground = StackGround(protect=self.protect)
            self.stack_ground = ground
            return ground
        return HarnessGround(verbose=self.verbose, keep=self.keep, web=self.web.start())

    async def run(self) -> list[ScenarioResult]:
        await self._preflight()
        with _the_rig():
            return await self._run_everything()

    async def _run_everything(self) -> list[ScenarioResult]:
        # Grouped by ground and run a group at a time: each ground costs a
        # jarvis-core start or a compose bring-up, and interleaving them would
        # pay that per scenario.
        groups: dict[str, list[Scenario]] = {}
        for scenario in self.scenarios:
            groups.setdefault(self._ground_for(scenario), []).append(scenario)
        started = time.time()
        for name in ("stack", "harness"):
            if not groups.get(name):
                continue
            ground = self._make_ground(name)
            try:
                ground.start()
                await self._run_group(ground, groups[name])
            finally:
                ground.stop()
                if name == "harness":
                    self.web.stop()
        self.results.extend(self._stack_logs_are_clean(started))
        return self.results

    def _stack_logs_are_clean(self, since: float) -> list[ScenarioResult]:
        """The assertion no scenario can make: did anything shout in the logs?

        A container that is up and erroring is invisible to every expectation
        in this suite — the turn passes, the house changes, and MQTT has been
        reconnecting in a loop for two days. So the run ends by reading what
        the services said about themselves, and a failure here fails the run
        exactly like a failed scenario.
        """
        ground = self.stack_ground
        if ground is None or ground.stack is None:
            return []
        result = ScenarioResult(
            name="stack-logs-clean", capability="stack", variant="containers"
        )
        errors = ground.stack.errors_since(since)
        if errors:
            result.ok = False
            result.error = (
                f"{len(errors)} ERROR-level record(s) in container logs during the run:\n"
                + "\n".join(f"  · {line}" for line in errors[:8])
            )
        self._say_result(result)
        return [result]

    async def _run_group(self, ground: Ground, scenarios: list[Scenario]) -> None:
        from testing.harness import JarvisClient

        console = None
        try:
            if self.want_browser:
                console = ground.console()
            # The client's own timeout has to be at least a turn: the text
            # transport is one REST call that waits for the whole answer, and
            # a 30 s default cut a 27-second turn off as a `ReadTimeout` that
            # read like a server failure.
            # Long enough for the most patient scenario in this group: the REST
            # transport is one call that waits for the whole answer, so a
            # scenario's own `timeout:` has to reach the HTTP client or it is
            # only an opinion.
            patience = max([TURN_TIMEOUT, *(s.timeout for s in scenarios if s.timeout)])
            client = JarvisClient(ground.base_url, ground.token, timeout=patience)
            await client.connect()
            self.link = Link(client)
            observer = await Observer(client).start()
            self.observer = observer
            if ground.name == "stack":
                await self._house_exists(client)
            transports = {
                "voice": ApiVoice(self.link, ground, self.mouth, self.ears),
                "text": Text(self.link),
            }
            if console is not None:
                transports["voice-ui"] = Browser(console, self.mouth, self.ears)
                transports["text-ui"] = Browser(console, self.mouth, self.ears)
            try:
                for scenario in scenarios:
                    for variant in scenario.variants:
                        if variant not in self.variants:
                            continue
                        if variant.endswith("-ui") and console is None:
                            # `--no-browser`, or no console to drive: the page
                            # cannot be watched, so the page is not asserted on.
                            print(f"  skip {scenario.name} ({variant}): no console", flush=True)
                            continue
                        result = await self._run_scenario(
                            scenario, variant, transports, ground
                        )
                        self.results.append(result)
                        self._say_result(result)
            finally:
                await self.observer.stop()
                await self.link.client.aclose()
        finally:
            if console is not None:
                console.stop()

    def _say_result(self, result: ScenarioResult) -> None:
        flag = "ok  " if result.ok else "FAIL"
        gate = f" [gated-on {result.gated_on}]" if result.gated_on else ""
        print(f"  {flag} {result.name} ({result.variant}){gate} {result.seconds:.1f}s", flush=True)
        if not result.ok:
            for turn in result.turns:
                for failure in turn.failures:
                    print(f"       · turn {turn.index}: {failure}", flush=True)
            if result.error:
                print(f"       · {result.error}", flush=True)

    # --- one scenario ------------------------------------------------------
    async def _run_scenario(
        self, scenario: Scenario, variant: str, transports: dict[str, Any], ground: Ground
    ) -> ScenarioResult:
        # Read through the link every time: a `restart: true` turn replaces
        # both, and a captured client is a closed one from the turn after.
        observer = self.observer
        started = time.monotonic()
        result = ScenarioResult(
            name=scenario.name,
            capability=scenario.capability,
            variant=variant,
            gated_on=scenario.gated_on,
        )
        killed: list[str] = []
        self._approval_cursor = 0
        # The floor for "a task appeared": anything created before the
        # scenario began is history, not a result — but a task made in turn 0
        # and cancelled in turn 1 is this scenario's, so the floor is the
        # scenario's start, not the turn's.
        self._scenario_started_at = time.time() - 5.0
        transport = transports.get(variant)
        if transport is None:
            result.ok = False
            result.error = f"no transport for variant {variant!r}"
            result.seconds = time.monotonic() - started
            return result

        try:
            baseline = await self._baseline(observer, ground)
            await self._setup(scenario, self.link.client, observer)
            # Every thread this suite opens on a real house is named, so a
            # person looking at their own console can tell which conversations
            # were the tests and the sweep below knows what it may delete.
            conversation_id: str | None = (
                f"{TEST_NAMESPACE}{scenario.name}:{variant}"
                if ground.name == "stack"
                else None
            )
            for index, turn in enumerate(scenario.turns):
                if turn.wait and not turn.observe:
                    # "Wait, then say the next thing": the wait is before the
                    # turn's marks, so the check covers the turn's own effects.
                    await asyncio.sleep(turn.wait)
                if turn.kill:
                    # Pull a service out from under a turn in flight. The
                    # `finally` below puts it back whatever happens, because
                    # the next scenario needs speech to work.
                    killed.append(turn.kill)
                    ground.stack.stop(turn.kill)  # type: ignore[union-attr]
                if turn.do.get("mqtt_publish"):
                    # A sensor announcing itself and reporting, the way a
                    # Zigbee bridge or an rtl_433 does: the rig is the device.
                    await self._mqtt_publish(turn.do["mqtt_publish"])
                if turn.do.get("fixture_write"):
                    # A page the fixture web serves, rewritten: the rig is the
                    # website, so a watch has something real to notice (M59).
                    self._fixture_write(turn.do["fixture_write"], ground)
                turn = self._expanded(turn, ground)
                if turn.do.get("extension"):
                    # An operator flipping a switch while a conversation is
                    # already in progress, which is when they actually do it.
                    patch = dict(turn.do["extension"])
                    key = str(patch.pop("key", ""))
                    await observer.set_extension(key, **patch)
                if turn.new_conversation:
                    # A different thread, as a second person or a later day
                    # would be. Without this every turn shares one conversation
                    # id and "the other thread does not know" is unassertable.
                    conversation_id = (
                        f"{TEST_NAMESPACE}{scenario.name}:{variant}:{index}"
                        if ground.name == "stack"
                        else None
                    )
                if turn.restart:
                    # The whole point of the turn: kill the process and see
                    # what survived. The socket does not, so the client and
                    # the observer are rebuilt around the new one — and the
                    # old client is closed FIRST, because closing it after
                    # dialling again leaves the new connection using a closed
                    # transport ("Cannot send a request, as the client has
                    # been closed").
                    await observer.stop()
                    await self.link.client.aclose()
                    ground.restart_core()
                    from testing.harness import JarvisClient as _Client

                    fresh = _Client(ground.base_url, ground.token, timeout=TURN_TIMEOUT)
                    await fresh.connect()
                    self.link.client = fresh
                    observer = await Observer(fresh).start()
                    self.observer = observer
                mark, event_mark = observer.mark(), observer.event_mark()
                tool_mark, approval_mark = observer.tool_mark(), observer.approval_mark()
                if turn.observe and turn.wait:
                    # An observe turn is the opposite: what happens DURING the
                    # wait is the point, so the marks are taken first. The
                    # first reminder scenario slept through its own reminder
                    # and then looked for it after the mark.
                    await asyncio.sleep(turn.wait)
                if turn.observe:
                    # Nothing is said or sent: the turn waited (above) and
                    # now asserts on what the house did by itself.
                    spoken = Turn(said="", conversation_id=conversation_id or "")
                else:
                    spoken = await self._speak(
                        transport, turn, variant, conversation_id, scenario.timeout
                    )
                conversation_id = spoken.conversation_id or conversation_id
                turn_result = await self._check(
                    scenario, variant, index, turn, spoken, observer, mark,
                    event_mark, tool_mark, approval_mark,
                )
                if turn.kill:
                    # Back immediately, not at the end of the scenario: the
                    # next turn is the one that proves the failure was
                    # transient, and it needs the service it was denied.
                    ground.stack.start(turn.kill)  # type: ignore[union-attr]
                    killed.remove(turn.kill)
                result.turns.append(turn_result)
                if not turn_result.ok:
                    result.ok = False
                    break
        except LiveError as err:
            result.ok = False
            result.error = str(err)
        except Exception as err:  # noqa: BLE001 - a crash is a failed scenario
            result.ok = False
            result.error = f"{type(err).__name__}: {err}"
        finally:
            for approver in self._approvers:
                approver.cancel()
            self._approvers.clear()
            for container in killed:
                try:
                    ground.stack.start(container)  # type: ignore[union-attr]
                except Exception as err:  # noqa: BLE001 - say it, do not hide it
                    result.ok = False
                    result.error = f"{result.error} (and {container} did not come back: {err})"
            swept = await self._sweep(observer, ground, baseline)
            if swept and result.ok:
                result.ok = False
                result.error = swept
        result.seconds = time.monotonic() - started
        return result

    async def _house_exists(self, client) -> None:
        """Refuse to run house scenarios against an installation with no house.

        A fresh Jarvis controls nothing — deliberately, because a default
        configuration that invents devices nobody owns is worse than an empty
        one (`test_the_default_boots_into_an_empty_house_that_is_still_alive`).
        Run the suite against that and every house scenario fails on a missing
        entity, which reads like a broken assistant rather than an empty house.
        """
        try:
            states = await client.command("get_states")
        except Exception as err:  # noqa: BLE001 - no house is the answer here
            raise LiveError(f"could not read the running Jarvis's states: {err}") from err
        controllable = [
            row["entity_id"]
            for row in states or []
            if isinstance(row, dict)
            and str(row.get("entity_id", "")).split(".", 1)[0] in HOUSE_DOMAINS
        ]
        if not controllable:
            raise LiveError(
                "the running Jarvis controls nothing, so the house scenarios cannot mean "
                "anything. Give it a house of software:\n"
                "  cp jarvis-core/config/examples/house/packages-demo-house.yaml \\\n"
                "     jarvis-core/config/packages/demo-house.yaml\n"
                "  docker compose -f jarvis-core/docker-compose.yml restart jarvis-core"
            )

    # --- leaving the house as it was ---------------------------------------
    async def _baseline(self, observer: Observer, ground: Ground) -> dict[str, set[str]]:
        """What the house held before the scenario touched it.

        Only on the stack ground: the harness's house is a temporary directory
        that is deleted at the end of the run, and diffing it would be work in
        service of nothing.
        """
        if ground.name != "stack":
            return {}
        return {
            "notes": {str(row.get("id")) for row in await observer.notes()},
            "memory": {str(row.get("id")) for row in await observer.memories()},
            "conversations": await self._conversation_ids(),
        }

    async def _conversation_ids(self) -> set[str]:
        try:
            answer = await self.link.client.command("jarvis/conversation/list")
        except Exception:  # noqa: BLE001 - an absent capability is not a leak
            return set()
        rows = answer.get("conversations") or answer.get("result") or []
        return {str(row.get("id")) for row in rows if isinstance(row, dict)}

    async def _sweep(
        self, observer: Observer, ground: Ground, baseline: dict[str, set[str]]
    ) -> str:
        """Delete what this scenario created, and say so if anything survives.

        The scenario's own cleanup, asserted rather than hoped for. The whole
        run is wrapped in a volume snapshot as well, but a snapshot is a
        recovery; this is the difference between a suite that can be run
        against somebody's house and one that merely can be undone afterwards.
        """
        if not baseline or ground.name != "stack":
            return ""
        problems: list[str] = []
        for row in await observer.notes():
            note_id = str(row.get("id"))
            if note_id in baseline.get("notes", set()):
                continue
            try:
                await self.link.client.command("jarvis/notes/delete", note_id=note_id)
            except Exception as err:  # noqa: BLE001
                problems.append(f"note {note_id} could not be removed: {err}")
        for row in await observer.memories():
            entry_id = str(row.get("id"))
            if entry_id in baseline.get("memory", set()):
                continue
            try:
                await self.link.client.command("jarvis/memory/forget", entry_id=entry_id)
            except Exception as err:  # noqa: BLE001
                problems.append(f"memory {entry_id} could not be removed: {err}")
        # Tasks the scenario started and left running: a twelve-minute sensor
        # audit from `interactions-proactive-moment` was still the top row of
        # the task dock when `task-live-ui` looked for its own. A leftover is
        # a failure, and a running one is also a load on the next scenario.
        self._fixture_cleanup(problems)
        since = getattr(self, "_scenario_started_at", 0.0)
        for task in await observer.tasks():
            if float(task.get("created") or 0.0) < since:
                continue
            if str(task.get("status") or "") not in ("running", "queued", "blocked"):
                continue
            try:
                await self.link.client.command("jarvis/tasks/cancel", task_id=str(task.get("id")))
            except Exception as err:  # noqa: BLE001
                problems.append(f"task {task.get('id')} could not be cancelled: {err}")
        for conversation_id in await self._conversation_ids():
            if conversation_id in baseline.get("conversations", set()):
                continue
            if not conversation_id.startswith(TEST_NAMESPACE):
                # Not ours to delete. A thread that appeared during the run
                # without our prefix belongs to whoever else is using this
                # house, and this suite does not touch it.
                continue
            try:
                await self.link.client.command(
                    "jarvis/conversation/delete", conversation_id=conversation_id
                )
            except Exception as err:  # noqa: BLE001
                problems.append(f"thread {conversation_id} could not be removed: {err}")

        left = {
            note_id
            for note_id in {str(r.get("id")) for r in await observer.notes()}
            if note_id not in baseline.get("notes", set())
        }
        if left:
            problems.append(f"{len(left)} note(s) left behind on a real house: {sorted(left)}")
        return "; ".join(problems)

    async def _setup(self, scenario: Scenario, client, observer: Observer) -> None:
        # `clear: [memory, notes]` — a scenario that asserts "it remembered"
        # has to start from a house that does not already know. Without this
        # the voice variant stored the fact and the text variant answered
        # "already noted, sir" without calling anything, which is correct
        # behaviour and a useless test.
        for what in scenario.setup.get("clear") or []:
            try:
                if what == "memory":
                    await client.command("jarvis/memory/forget", all=True)
                elif what == "notes":
                    for note in (await client.command("jarvis/notes/list")).get("notes") or []:
                        await client.command("jarvis/notes/delete", note_id=note["id"])
                else:
                    raise LiveError(f"setup cannot clear {what!r}")
            except LiveError:
                raise
            except Exception as err:  # noqa: BLE001 - a missing capability is a failure
                raise LiveError(f"setup could not clear {what}: {err}") from err

        for entity_id, state in (scenario.setup.get("states") or {}).items():
            domain = str(entity_id).split(".", 1)[0]
            service = "turn_on" if str(state).lower() in ("on", "true") else "turn_off"
            try:
                await client.call_service(domain, service, target={"entity_id": entity_id})
            except Exception as err:  # noqa: BLE001 - a bad fixture must say so
                raise LiveError(f"setup could not put {entity_id} to {state}: {err}") from err

    async def _speak(self, transport, turn, variant: str, conversation_id: str | None,
                     timeout: float = 0.0):
        if turn.sound:
            pcm = (
                audio_mod.silence(float(turn.audio.get("seconds") or 2.0))
                if turn.sound == "silence"
                else audio_mod.room_tone(float(turn.audio.get("seconds") or 2.0))
            )
            return await transport.say(
                "(no speech)", pcm=pcm, rate=16000, conversation_id=conversation_id,
                timeout=timeout or TURN_TIMEOUT, probes=_ui_probes(turn.expect),
            )

        pcm = rate = None
        wake_phrase = str(turn.audio.get("wake") or "") if variant.startswith("voice") else ""
        if variant.startswith("voice") and turn.audio:
            utterance = self.mouth.say(turn.say)
            pcm, rate = utterance.pcm, utterance.rate
            if turn.audio.get("snr_db") is not None:
                pcm = audio_mod.add_noise(
                    pcm,
                    float(turn.audio["snr_db"]),
                    rate=rate,
                    shape=str(turn.audio.get("noise") or "white"),
                )
            if turn.audio.get("clip"):
                pcm = audio_mod.clip(pcm, float(turn.audio["clip"]))
        return await transport.say(
            turn.say,
            pcm=pcm,
            rate=rate,
            conversation_id=conversation_id,
            wake_phrase=wake_phrase,
            timeout=timeout or TURN_TIMEOUT,
            probes=_ui_probes(turn.expect),
        )

    # --- the fixture web, rewritten for a scenario (M59) -----------------------
    def _fixture_write(self, rows: Any, ground: Any) -> None:
        """Write pages under `<fixture site>/live/` for the fixture web to serve.

        Only under `live/`: the committed fixture pages are the handbook every
        other scenario reads, and a scenario that could rewrite them could
        break the next one. What is written is removed when the scenario ends.
        """
        from testing.live.fixture_site import pages_for

        if not isinstance(rows, list):
            raise LiveError(f"fixture_write needs a list of {{site, path, content}} rows, not {rows!r}")
        written: list[Path] = getattr(self, "_fixture_written", [])
        for row in rows:
            if not isinstance(row, dict) or not row.get("site") or not row.get("path"):
                raise LiveError(f"fixture_write needs {{site, path, content}} rows, not {row!r}")
            rel = str(row["path"]).strip("/")
            if not rel.startswith("live/") or ".." in rel.split("/"):
                raise LiveError(f"fixture_write may only write under live/: {rel!r}")
            target = pages_for(str(row["site"])) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._expand_text(str(row.get("content") or ""), ground))
            if target not in written:
                written.append(target)
        self._fixture_written = written

    def _fixture_cleanup(self, problems: list[str]) -> None:
        for target in getattr(self, "_fixture_written", []):
            try:
                target.unlink(missing_ok=True)
            except OSError as err:
                problems.append(f"fixture page {target} could not be removed: {err}")
        self._fixture_written = []

    def _expand_text(self, text: str, ground: Any) -> str:
        """`{{handbook}}` and friends → the fixture web's addresses for this run."""
        web = getattr(ground, "web", None) or {}
        out = str(text or "")
        for name, url in (web.items() if isinstance(web, dict) else []):
            out = out.replace("{{" + str(name) + "}}", str(url).rstrip("/"))
        return out

    def _expanded(self, turn: Any, ground: Any) -> Any:
        """The turn with its spoken text expanded; the scenario file keeps the placeholder."""
        if not getattr(turn, "say", None) or "{{" not in turn.say:
            return turn
        import dataclasses

        if dataclasses.is_dataclass(turn):
            return dataclasses.replace(turn, say=self._expand_text(turn.say, ground))
        turn.say = self._expand_text(turn.say, ground)
        return turn

    async def _mqtt_publish(self, messages: Any) -> None:
        """Publish each `{topic, payload, retain?}` to the house's broker.

        The broker is the stack's mosquitto (LIVE_MQTT_HOST/PORT, default
        127.0.0.1:1883, LIVE_MQTT_USERNAME/PASSWORD when it wants them). A dict
        payload is sent as JSON, which is what every discovery config is.
        """
        import aiomqtt

        host = os.environ.get("LIVE_MQTT_HOST", "127.0.0.1")
        port = int(os.environ.get("LIVE_MQTT_PORT", "1883"))
        username = os.environ.get("LIVE_MQTT_USERNAME") or None
        password = os.environ.get("LIVE_MQTT_PASSWORD") or None
        rows = messages if isinstance(messages, list) else [messages]
        async with aiomqtt.Client(host, port, username=username, password=password) as client:
            for row in rows:
                if not isinstance(row, dict) or not row.get("topic"):
                    raise LiveError(f"mqtt_publish needs {{topic, payload}} rows, not {row!r}")
                payload = row.get("payload", "")
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload)
                await client.publish(
                    str(row["topic"]), str(payload), retain=bool(row.get("retain", False)), qos=1
                )
                await asyncio.sleep(0.2)

    # --- the assertions ----------------------------------------------------
    async def _check(
        self, scenario: Scenario, variant: str, index: int, turn, spoken,
        observer: Observer, mark: int, event_mark: int, tool_mark: int = 0,
        approval_mark: int = 0,   # noqa: ARG002 - see `_approval_cursor`
    ) -> TurnResult:
        expect = turn.expect
        # Wall clock, because a task's `created` is one: the comparison has to
        # be in the same units as the field it is filtering on.
        started_at = time.time() - (spoken.latency.get("total") or 0.0) - 5.0
        out = TurnResult(
            scenario=scenario.name,
            capability=scenario.capability,
            variant=variant,
            index=index,
            said=turn.say,
            heard=spoken.transcript,
            reply=spoken.reply_text,
            reply_heard=spoken.reply_heard,
            latency=dict(spoken.latency),
            error=spoken.error,
        )
        fail = out.failures.append

        # --- what it heard
        if variant.startswith("voice") and turn.say and not turn.sound:
            out.wer = wer(turn.say, spoken.transcript)
            ceiling = float(expect.get("transcript_wer", DEFAULT_WER))
            if out.wer > ceiling:
                fail(f"WER {out.wer:.2f} > {ceiling:.2f}: heard {spoken.transcript!r}")

        if "wake_word" in expect:
            want = expect.get("wake_word")
            if want is False and spoken.wake_word:
                fail(f"a wake word fired on audio that has none: {spoken.wake_word!r}")
            elif isinstance(want, str) and spoken.wake_word != want:
                fail(f"wake word was {spoken.wake_word!r}, expected {want!r}")

        # --- an action a human had to answer
        #
        # The rig is the human: it waits for the request, says yes or no, and
        # asserts what followed. Saying **no** is the half that is never tested
        # anywhere and the half that matters — a gate that can be worn down by
        # asking again is not a gate.
        want_approval = expect.get("approval")
        if want_approval:
            decision = str(want_approval.get("decision") or "approve").lower()
            # From the scenario's cursor, not from this turn's mark: a coding
            # job starts during the FIRST turn and is holding its first action
            # long before somebody says "actually, don't". Looking only at what
            # was raised after the current sentence found nothing, every time
            # the job was quicker than the person.
            held = await observer.wait_for_approval(
                mark=self._approval_cursor,
                kind=str(want_approval.get("kind") or ""),
                tool=str(want_approval.get("tool") or ""),
                timeout=float(want_approval.get("within") or 300.0),
            )
            if held is None:
                fail(
                    "nothing was held for approval, so there was no gate to "
                    f"{decision}. Held so far: {observer.approvals[approval_mark:]!r}"
                )
            else:
                self._approval_cursor = observer.approvals.index(held) + 1
                answered = await observer.answer(held["request_id"], decision == "approve")
                if not answered:
                    fail(f"could not answer the held action {held!r}")
                elif decision == "approve" and want_approval.get("keep_approving"):
                    # Opt-in, and it has to be: a scenario that approves the
                    # FIRST gate and then denies the second one (which is the
                    # only way to test a denial mid-job) had its denial
                    # approved out from under it by a loop that kept clicking
                    # yes. `keep_approving` says "be the person who watches the
                    # whole job through", and nothing else does.
                    self._approvers.append(
                        asyncio.create_task(
                            _keep_approving(observer, self._approval_cursor)
                        )
                    )

        # --- what it did
        wanted = expect.get("service")
        if wanted:
            call = observer.called(
                mark,
                str(wanted.get("domain") or ""),
                str(wanted.get("service") or ""),
                str(wanted.get("entity_id") or ""),
            )
            if call is None:
                did = [f"{c.domain}.{c.service}" for c in observer.calls_since(mark)]
                fail(
                    f"expected {wanted.get('domain')}.{wanted.get('service')} on "
                    f"{wanted.get('entity_id') or 'anything'}; called {did or 'nothing'}"
                )

        for forbidden in expect.get("no_service") or []:
            domain, _, service = str(forbidden).partition(".")
            if observer.called(mark, domain, service):
                fail(f"{forbidden} was called and must not have been")

        # --- did it fail, and did it say so
        #
        # A turn that must fail is as much a promise as one that must work:
        # `resilience-stt-down` is about the difference between "I can't hear
        # you at the moment" and a HUD that listens forever.
        if "error" in expect:
            want = expect.get("error") or {}
            got = spoken.error or {}
            if not got:
                fail(
                    "the turn was expected to fail visibly and did not — "
                    f"it answered {spoken.reply_text[:80]!r}"
                )
            else:
                text = json.dumps(got).lower()
                needle = str(want.get("contains") or "").lower()
                if needle and needle not in text:
                    fail(f"the failure was {got!r}, which does not mention {needle!r}")
                code = str(want.get("code") or "")
                if code and str(got.get("code") or "") != code:
                    fail(f"error code {got.get('code')!r}, expected {code!r}")
        elif spoken.error and not expect.get("no_reply"):
            fail(f"the turn failed: {spoken.error}")

        for entity_id, want_state in (expect.get("state") or {}).items():
            if not await observer.wait_for_state(entity_id, str(want_state)):
                fail(
                    f"{entity_id} is {await observer.state_of(entity_id)!r}, "
                    f"expected {want_state!r}"
                )

        # --- what it said
        reply = spoken.reply_text or spoken.reply_heard
        if expect.get("no_reply") and reply.strip():
            fail(f"expected silence, got {reply!r}")
        for needle in _as_list(expect.get("reply_contains")):
            if needle.lower() not in reply.lower():
                fail(f"reply does not contain {needle!r}: {reply!r}")
        for needle in _as_list(expect.get("reply_absent")):
            if needle.lower() in reply.lower():
                fail(f"reply contains {needle!r} and must not: {reply!r}")
        for pattern in _as_list(expect.get("reply_matches")):
            if not re.search(pattern, reply, re.IGNORECASE):
                fail(f"reply does not match /{pattern}/: {reply!r}")
        criterion = expect.get("reply_means")
        if criterion:
            if not reply.strip():
                fail(f"nothing was said, so {criterion!r} cannot hold")
            else:
                verdict = await self.judge.check(str(criterion), reply)
                out.judge_reasons.append(f"{'ok' if verdict.ok else 'no'}: {verdict.why}")
                if not verdict.ok:
                    fail(f"judge: {verdict.why} (reply: {reply!r})")

        # A spoken turn must actually have been spoken, and what came out of
        # the speaker must be what was written on the screen. This is the check
        # that catches TTS that silently produced nothing.
        if variant.startswith("voice") and reply.strip() and not expect.get("no_reply"):
            if not spoken.tts_url:
                fail("nothing was synthesised: the reply was never spoken")
            elif spoken.reply_heard:
                drift = wer(spoken.reply_text, spoken.reply_heard)
                if drift > 0.5:
                    fail(
                        f"what was spoken does not match what was written "
                        f"(WER {drift:.2f}): heard {spoken.reply_heard!r}"
                    )

        # --- tasks, notes, memory
        want_task = expect.get("task")
        if want_task:
            task = await observer.wait_for_task(
                kind=str(want_task.get("kind") or ""),
                status=str(want_task.get("status") or ""),
                title_contains=str(want_task.get("title_contains") or ""),
                timeout=float(want_task.get("within") or 120.0),
                since=getattr(self, "_scenario_started_at", started_at),
            )
            if task is None:
                # With the error, when there is one: a task that ended in error is
                # the whole finding, and "(research, error)" says nothing.
                seen = [
                    (t.get("kind"), t.get("status"), str(t.get("error") or "")[:120] or None)
                    for t in await observer.tasks()
                ]
                fail(f"no task matching {want_task} appeared; tasks were {seen}")
            elif want_task.get("steps_at_least") and len(task.get("steps") or []) < int(
                want_task["steps_at_least"]
            ):
                fail(
                    f"task has {len(task.get('steps') or [])} step(s), "
                    f"expected at least {want_task['steps_at_least']}"
                )
        # A reminder is a schedule entry until it fires; only then is it a task.
        # "Remind me in a minute" is proved by the entry appearing now, and by
        # the reminder being heard a minute later — not by a task within 30 s.
        want_schedule = expect.get("schedule")
        if want_schedule:
            job = await observer.wait_for_schedule(
                title_contains=str(want_schedule.get("title_contains") or ""),
                timeout=float(want_schedule.get("within") or 60.0),
                since=getattr(self, "_scenario_started_at", started_at),
            )
            if job is None:
                seen = [(j.get("kind"), (j.get("title") or "")[:40]) for j in await observer.schedules()]
                fail(f"no schedule entry matching {want_schedule} appeared; schedules were {seen}")
        if expect.get("no_task"):
            tasks = await observer.tasks()
            fresh = [t for t in tasks if float(t.get("created") or 0) > time.time() - 300]
            if fresh:
                fail(f"a task was created and should not have been: {fresh[0].get('title')!r}")

        # --- routing, for the scorecard
        # Only tasks THIS turn created. Reading the whole list made every turn
        # after a background job look like it had started one — the previous
        # scenario's task was still there, and routing accuracy was measured
        # against it.
        fresh = [
            task
            for task in await observer.tasks()
            if float(task.get("created") or 0) >= started_at
        ]
        kinds = [str(t.get("kind") or "") for t in fresh]
        calls = [f"{c.domain}.{c.service}" for c in observer.calls_since(mark)]
        out.tools = observer.tools_since(tool_mark)
        out.routed = _capability_of(kinds, calls, out.tools, reply)
        if expect.get("capability"):
            out.routed_expected = str(expect.get("capability"))
            if out.routed != out.routed_expected:
                fail(f"routed to {out.routed!r}, expected {out.routed_expected!r}")

        if expect.get("within_seconds"):
            limit = float(expect["within_seconds"])
            total = out.latency.get("total") or 0.0
            if total > limit:
                fail(f"the turn took {total:.1f}s, over its {limit:.1f}s budget")

        want_note = expect.get("note")
        if want_note:
            note = await observer.wait_for_note(
                contains=str(want_note.get("body_contains") or ""),
                title_contains=str(want_note.get("title_contains") or ""),
                timeout=float(want_note.get("within") or 60.0),
            )
            if note is None:
                have = [n.get("title") for n in await observer.notes()]
                fail(f"no note matching {want_note} was written; notes are {have}")
            elif want_note.get("citations_at_least"):
                body = await observer.note_body(note["id"])
                citations = len(re.findall(r"\[\d+\]|https?://", body))
                if citations < int(want_note["citations_at_least"]):
                    fail(
                        f"the note has {citations} citation(s), expected at least "
                        f"{want_note['citations_at_least']}"
                    )

        want_note_moment = expect.get("notification")
        if want_note_moment:
            moment = await observer.wait_for_notification(
                title_contains=str(want_note_moment.get("title_contains") or ""),
                kind=str(want_note_moment.get("kind") or ""),
                timeout=float(want_note_moment.get("within") or 120.0),
            )
            if moment is None:
                have = [row.get("title") for row in await observer.notifications()]
                fail(f"no notification matching {want_note_moment} was recorded; had {have}")
            elif want_note_moment.get("source") and moment.get("source") != want_note_moment[
                "source"
            ]:
                fail(
                    f"the notification says it came from {moment.get('source')!r}, "
                    f"expected {want_note_moment['source']!r}"
                )

        want_memory = expect.get("memory")
        if want_memory:
            recalls = str(want_memory.get("recalls") or "")
            forgotten = str(want_memory.get("forgotten") or "")
            entries = await observer.memories()
            texts = " | ".join(str(entry.get("text") or "") for entry in entries)
            if recalls and recalls.lower() not in texts.lower():
                fail(f"memory does not hold {recalls!r}; it holds {texts[:200]!r}")
            if forgotten and forgotten.lower() in texts.lower():
                fail(f"memory still holds {forgotten!r} after it was forgotten")

        want_file = expect.get("file")
        if want_file:
            path = REPO_ROOT / str(want_file.get("path") or "")
            wanted = bool(want_file.get("exists", True))
            if path.exists() is not wanted:
                fail(
                    f"{want_file.get('path')} "
                    + ("does not exist and should" if wanted else "exists and should not")
                )

        want_extension = expect.get("extension")
        if want_extension:
            key = str(want_extension.get("key") or "")
            rows = {str(row.get("key")): row for row in await observer.extensions()}
            row = rows.get(key)
            if row is None:
                fail(f"nothing installed called {key!r}; installed: {sorted(rows)[:8]}")
            else:
                if "enabled" in want_extension:
                    wanted = bool(want_extension["enabled"])
                    if bool(row.get("enabled")) is not wanted:
                        fail(f"{key} is {'on' if row.get('enabled') else 'off'}, expected the other")
                for permission in _as_list(want_extension.get("granted")):
                    if permission not in (row.get("granted") or []):
                        fail(f"{key} does not hold {permission!r}; it holds {row.get('granted')}")
            # The claim that matters: what the MODEL is offered, which is a
            # different question from what the console lists.
            offered = set(await observer.offered_tools())
            for tool in _as_list(want_extension.get("tool_offered")):
                if tool not in offered:
                    fail(f"{tool!r} is not offered to the model, and should be")
            for tool in _as_list(want_extension.get("tool_withheld")):
                if tool in offered:
                    fail(f"{tool!r} is still offered to the model after being withdrawn")
            wanted_skills = _as_list(want_extension.get("skill_offered"))
            unwanted_skills = _as_list(want_extension.get("skill_withheld"))
            if wanted_skills or unwanted_skills:
                # The store, which is what builds the prompt's skill index —
                # not the registry's list, which is what the console draws.
                skills = set(await observer.offered_skills())
                for skill in wanted_skills:
                    if skill not in skills:
                        fail(f"the skill {skill!r} is not offered to the model, and should be")
                for skill in unwanted_skills:
                    if skill in skills:
                        fail(f"the skill {skill!r} is still offered after being turned off")

        # --- what the console showed (browser variants only)
        want_ui = _ui_probes(expect)
        if want_ui:
            if spoken.ui is None:
                fail(
                    "this scenario asserts 'ui', which only a browser variant "
                    "(voice-ui / text-ui) can check — the API transports never open a page"
                )
            else:
                seen = {p.get("testid"): p for p in spoken.ui}
                for probe in want_ui:
                    got = seen.get(probe["testid"])
                    if got is None:
                        fail(f"ui: nothing was probed for [{probe['testid']}]")
                    elif not got.get("ok"):
                        fail(
                            f"ui: [{probe['testid']}] never showed {probe['contains']!r} "
                            f"within {probe['withinMs'] / 1000:g}s; it showed {got.get('text')!r}"
                        )

        out.ok = not out.failures
        return out


async def _keep_approving(observer: Observer, mark: int, seconds: float = 900.0) -> None:
    """Answer every further held action the way the first one was answered."""
    seen = {row["request_id"] for row in observer.approvals[:mark]}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for row in list(observer.approvals):
            if row["request_id"] in seen:
                continue
            seen.add(row["request_id"])
            await observer.answer(row["request_id"], True)
        await asyncio.sleep(0.5)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def select(scenarios: list[Scenario], implemented_only: bool, only: str = "",
           capability: str = "") -> list[Scenario]:
    chosen = scenarios
    if implemented_only:
        chosen = [s for s in chosen if not s.gated]
    if only:
        wanted = {name.strip() for name in only.split(",") if name.strip()}
        chosen = [s for s in chosen if s.name in wanted]
    if capability:
        chosen = [s for s in chosen if s.capability == capability]
    return chosen


def check_thresholds(totals: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for key, limit in THRESHOLDS.items():
        value = totals.get(key)
        if value is None:
            problems.append(f"{key}: nothing measured, so the threshold cannot be met")
            continue
        if key in ("wer_mean", "round_trip_median"):
            if value > limit:
                problems.append(f"{key} {value:.3f} is over the {limit} ceiling")
        elif value < limit:
            problems.append(f"{key} {value:.3f} is under the {limit} floor")
    return problems


async def _main(args: argparse.Namespace) -> int:
    scenarios = load_all()
    chosen = select(scenarios, args.implemented_only, args.only, args.capability)
    if not chosen:
        print("no scenarios selected", file=sys.stderr)
        return 1

    gated = sum(1 for s in scenarios if s.gated)
    print(
        f"live: {len(chosen)} scenario(s) selected of {len(scenarios)} "
        f"({gated} gated on unfinished milestones)"
    )
    runner = Runner(
        chosen,
        variants=tuple(args.variants.split(",")),
        browser=not args.no_browser,
        keep=args.keep,
        verbose=args.verbose,
        target=args.target,
        protect=not args.no_protect,
    )
    started = time.monotonic()
    results = await runner.run()
    totals = summarise(results)
    latencies = latency_table(results)

    payload = {
        "mode": "implemented-only" if args.implemented_only else "full",
        "target": args.target,
        "seconds": round(time.monotonic() - started, 1),
        "totals": totals,
        "latency": latencies,
        "judge": [
            {"ok": v.ok, "why": v.why, "criterion": v.criterion}
            for v in runner.judge.verdicts
        ],
        "scenarios": [r.as_dict() for r in results],
    }
    write_json(OUT_DIR / "results.json", payload)

    failed = [r for r in results if not r.ok]
    print(
        f"\nlive: {totals['scenarios_passed']}/{totals['scenarios']} scenarios, "
        f"{totals['turns_passed']}/{totals['turns']} turns, "
        f"WER mean {totals['wer_mean']}, "
        f"median round trip {totals['round_trip_median']}s"
    )
    if failed:
        print("failed:")
        for result in failed:
            print(f"  - {result.name} ({result.variant})")
    if args.write_report:
        from testing.live.write_report import write_report

        path = write_report(payload, results)
        print(f"report: {path}")

    problems: list[str] = []
    filtered = bool(args.only or args.capability)
    if args.implemented_only:
        pass
    elif filtered:
        # A rate over a filtered subset is not the suite's rate: one capability's
        # four scenarios cannot say anything about routing accuracy, and a
        # median round trip over them is a median of four numbers. The whole
        # suite applies them (M23), and `docs/LIVE_TEST_REPORT.md` reports them.
        print(
            "thresholds: not applied — this run was filtered "
            f"({'--only' if args.only else '--capability'}), and a rate over a "
            "subset is not the suite's rate"
        )
    else:
        problems = check_thresholds(totals)
        for problem in problems:
            print(f"threshold: {problem}")
    print(f"details: {OUT_DIR / 'results.json'}")
    return 1 if failed or problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--implemented-only", action="store_true",
                      help="only scenarios with no gated-on milestone")
    mode.add_argument("--full", action="store_true", help="everything, with thresholds")
    parser.add_argument("--only", default="", help="comma-separated scenario names")
    parser.add_argument("--capability", default="", help="one capability")
    parser.add_argument("--variants", default="voice,text,voice-ui,text-ui",
                        help="which variants to run; the -ui ones need the console")
    parser.add_argument("--no-browser", action="store_true",
                        help="skip the browser transports (no console build needed)")
    parser.add_argument("--write-report", action="store_true",
                        help="write docs/LIVE_TEST_REPORT.md from this run")
    parser.add_argument("--target", default=os.environ.get("LIVE_TARGET", "stack"),
                        choices=("stack", "harness"),
                        help="run against the running containers (default) or a "
                             "jarvis-core of our own")
    parser.add_argument("--no-protect", action="store_true",
                        help="do not snapshot/restore the real house first "
                             "(faster, and you had better mean it)")
    parser.add_argument("--keep", action="store_true", help="keep the harness work dir")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.implemented_only and not args.full:
        args.full = True
    try:
        return asyncio.run(_main(args))
    except LiveError as err:
        print(f"the rig could not run: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
