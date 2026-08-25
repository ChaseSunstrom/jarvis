"""Run the scenarios. This is the suite.

    python3 -m testing.live.runner --implemented-only
    python3 -m testing.live.runner --full --report docs/LIVE_TEST_REPORT.md

What it boots: a real jarvis-core (the harness, with a throwaway house), pointed
at the **real** Whisper, the **real** Piper and the **real** model on this box.
No fake model, no fake voice services — a scenario that passed against a fake
recogniser would prove nothing about talking to Jarvis.

What it refuses to do: skip. A scenario whose capability does not exist yet is
marked `gated-on: <milestone>` in its own fixture and is not selected by
`--implemented-only`; in full mode it runs and it fails. There is no third
outcome, and `PROCESS.md` §2 says why.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
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
from testing.live.judge import Judge  # noqa: E402
from testing.live.report import (  # noqa: E402
    ScenarioResult,
    TurnResult,
    latency_table,
    summarise,
    wer,
    write_json,
)
from testing.live.scenario import Scenario, load_all  # noqa: E402
from testing.live.fixture_browser import Browser as FixtureBrowser  # noqa: E402
from testing.live.fixture_search import Search as FixtureSearch  # noqa: E402
from testing.live.fixture_site import SITES, Site as FixtureSite, pages_for  # noqa: E402
from testing.live.transport import (  # noqa: E402
    TURN_TIMEOUT,
    ApiVoice,
    Browser,
    Console,
    Link,
    Text,
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


#: Domains that ARE the house. A call outside them is plumbing.
HOUSE_DOMAINS = {
    "light", "switch", "lock", "cover", "climate", "fan", "media_player",
    "scene", "script", "vacuum", "button", "number", "select", "text",
    "input_boolean", "input_number", "input_select", "input_text",
}

#: Which capability a tool belongs to. The tools are the evidence: a request
#: was "routed to memory" if and only if it called a memory tool.
TOOL_CAPABILITY = {
    "use_skill": "skills",
    "remember": "memory",
    "recall": "memory",
    "forget": "memory",
    "note_create": "notes",
    "note_append": "notes",
    "note_search": "notes",
    "deep_research": "research",
    # A quick look-up IS research in the sense that matters here: it went to
    # the web rather than answering from the model. The two modes are one
    # engine (`MODE_BUDGETS`), and the routing table should not pretend
    # otherwise.
    "web_search": "research",
    "web_fetch": "research",
    "code_task": "coding",
    "apply_code_task": "coding",
    "run_background_task": "task",
}


def _capability_of(task_kinds: list[str], calls: list[str], tools: list[str],
                   reply: str) -> str:
    """What Jarvis actually did with the request, in one word.

    Read off the consequences rather than asked of the model: routing accuracy
    that a model self-reports is a model grading its own homework. Ordered by
    specificity — a coding job that also called `get_state` is still coding.
    """
    if "code" in task_kinds:
        return "coding"
    if "research" in task_kinds:
        return "research"
    # By PRECEDENCE, not by the order the tools happened to be called. A
    # look-up that searched the notes first and then went to the web is
    # research: the notes search was a means, and reading tools in call order
    # scored it as "notes" because that call came first.
    chosen = {
        TOOL_CAPABILITY[tool] for tool in tools if tool in TOOL_CAPABILITY
    }
    if any(call.startswith("memory.") for call in calls):
        chosen.add("memory")
    if any(call.startswith("notes.") for call in calls):
        chosen.add("notes")
    for capability in ("coding", "research", "memory", "notes"):
        if capability in chosen:
            return capability
    if task_kinds or "run_background_task" in tools:
        return "task"
    # Only calls that moved something in the HOUSE count as house control. Any
    # service call at all was too crude: a turn that read a skill and answered
    # was routed to "house" because something incidental had gone through the
    # service layer.
    if any(call.split(".", 1)[0] in HOUSE_DOMAINS for call in calls):
        return "house"
    if "use_skill" in tools:
        return "skills"
    return "answer"


@contextlib.contextmanager
def _the_rig(timeout: float = 3600.0):
    """Only one live run at a time, on this box.

    Not tidiness: the rig shares the machine's *real* Whisper, Piper and model
    server with anything else using them, and two harnesses at once means two
    conversations against one recogniser. The symptom is not an error — it is a
    scenario failing with an empty transcript, which reads as a defect in
    Jarvis and is not one. This was observed: a milestone's live check run
    beside the whole suite failed on a turn that had passed minutes earlier.

    Waits rather than refuses, because `verify-all` runs its scripts in order
    and the right behaviour for the second one is to take its turn.
    """
    lock_path = OUT_DIR / "rig.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "w")  # noqa: SIM115 - held for the block
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "live: another live run holds the rig (the voice services are "
                "shared); waiting for it to finish",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise LiveError(
                            f"another live run still holds {lock_path} after "
                            f"{timeout:g}s"
                        ) from None
                    time.sleep(2.0)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


class Runner:
    def __init__(
        self,
        scenarios: list[Scenario],
        *,
        variants: tuple[str, ...] = ("voice", "text"),
        browser: bool = True,
        keep: bool = False,
        verbose: bool = False,
    ) -> None:
        self.scenarios = scenarios
        self.variants = variants
        self.want_browser = browser
        self.keep = keep
        self.verbose = verbose
        self.results: list[ScenarioResult] = []
        #: Rebuilt whenever a scenario restarts the server.
        self.link: Link | None = None
        self.observer: Observer | None = None
        #: The fixture web, started with the harness and stopped with it.
        self._sites: list[FixtureSite] = []
        self._search: FixtureSearch | None = None
        self._browser: FixtureBrowser | None = None
        self.judge = Judge()
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

    def _fixture_web(self) -> dict[str, str]:
        """A small web this repository owns, for the scenarios that research.

        Started for every run rather than only the research ones: it is three
        threads and no network, and a `web:` block that appears for some
        scenarios and not others would be a different Jarvis depending on which
        test you ran. Each site gets its own loopback ADDRESS, because the
        per-domain cap and the cross-check both key on the host.
        """
        self._sites = [
            FixtureSite(host=f"127.0.0.{index + 2}", pages=pages_for(name)).start()
            for index, name in enumerate(SITES)
        ]
        by_name = dict(zip(SITES, (site.url for site in self._sites)))
        self._search = FixtureSearch(by_name).start()
        self._browser = FixtureBrowser([site.url for site in self._sites]).start()
        return {"search": self._search.url, "browser": self._browser.url}

    def _stop_fixture_web(self) -> None:
        for closing in (self._browser, self._search, *self._sites):
            if closing is not None:
                closing.stop()
        self._sites, self._search, self._browser = [], None, None

    def _harness(self):
        from testing.harness import Harness

        web = self._fixture_web()
        work_dir = os.environ.get("LIVE_WORK_DIR") or str(OUT_DIR / "harness")
        return Harness(
            work_dir=work_dir,
            keep=True,
            verbose=self.verbose,
            model=os.environ.get("LLM_MODEL", ""),
            ollama_url=os.environ.get("LLM_URL", ""),
            wyoming={
                "host": os.environ.get("LIVE_STT_HOST", "127.0.0.1"),
                "stt": int(os.environ.get("LIVE_STT_PORT", "10300")),
                "tts": int(os.environ.get("LIVE_TTS_PORT", "10200")),
                "wake": int(os.environ.get("LIVE_WAKE_PORT", "10400")),
            },
            search_url=web["search"],
            browser_url=web["browser"],
        )

    async def run(self) -> list[ScenarioResult]:
        await self._preflight()
        with _the_rig():
            return await self._run_everything()

    async def _run_everything(self) -> list[ScenarioResult]:
        from testing.harness import JarvisClient

        harness = self._harness()
        harness.start()
        console: Console | None = None
        try:
            if self.want_browser:
                console = Console(harness.base_url, harness.token).start()
            # The client's own timeout has to be at least a turn: the text
            # transport is one REST call that waits for the whole answer, and
            # a 30 s default cut a 27-second turn off as a `ReadTimeout` that
            # read like a server failure.
            client = JarvisClient(harness.base_url, harness.token, timeout=TURN_TIMEOUT)
            await client.connect()
            self.link = Link(client)
            observer = await Observer(client).start()
            self.observer = observer
            transports = {
                "voice": ApiVoice(self.link, harness, self.mouth, self.ears),
                "text": Text(self.link),
            }
            if console is not None:
                transports["voice-ui"] = Browser(console, self.mouth, self.ears)
                transports["text-ui"] = Browser(console, self.mouth, self.ears)
            try:
                for scenario in self.scenarios:
                    for variant in scenario.variants:
                        if variant not in self.variants:
                            continue
                        result = await self._run_scenario(
                            scenario, variant, transports, harness
                        )
                        self.results.append(result)
                        self._say_result(result)
            finally:
                await self.observer.stop()
                await self.link.client.aclose()
        finally:
            if console is not None:
                console.stop()
            harness.stop(cleanup=not self.keep)
            self._stop_fixture_web()
        return self.results

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
        self, scenario: Scenario, variant: str, transports: dict[str, Any], harness
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
        transport = transports.get(variant)
        if transport is None:
            result.ok = False
            result.error = f"no transport for variant {variant!r}"
            result.seconds = time.monotonic() - started
            return result

        try:
            await self._setup(scenario, self.link.client, observer)
            conversation_id: str | None = None
            for index, turn in enumerate(scenario.turns):
                if turn.wait:
                    await asyncio.sleep(turn.wait)
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
                    harness.restart_core()
                    from testing.harness import JarvisClient as _Client

                    fresh = _Client(harness.base_url, harness.token, timeout=TURN_TIMEOUT)
                    await fresh.connect()
                    self.link.client = fresh
                    observer = await Observer(fresh).start()
                    self.observer = observer
                mark, event_mark = observer.mark(), observer.event_mark()
                tool_mark = observer.tool_mark()
                spoken = await self._speak(transport, turn, variant, conversation_id)
                conversation_id = spoken.conversation_id or conversation_id
                turn_result = await self._check(
                    scenario, variant, index, turn, spoken, observer, mark,
                    event_mark, tool_mark,
                )
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
        result.seconds = time.monotonic() - started
        return result

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

    async def _speak(self, transport, turn, variant: str, conversation_id: str | None):
        if turn.sound:
            pcm = (
                audio_mod.silence(float(turn.audio.get("seconds") or 2.0))
                if turn.sound == "silence"
                else audio_mod.room_tone(float(turn.audio.get("seconds") or 2.0))
            )
            return await transport.say(
                "(no speech)", pcm=pcm, rate=16000, conversation_id=conversation_id
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
        )

    # --- the assertions ----------------------------------------------------
    async def _check(
        self, scenario: Scenario, variant: str, index: int, turn, spoken,
        observer: Observer, mark: int, event_mark: int, tool_mark: int = 0,
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
            )
            if task is None:
                seen = [(t.get("kind"), t.get("status")) for t in await observer.tasks()]
                fail(f"no task matching {want_task} appeared; tasks were {seen}")
            elif want_task.get("steps_at_least") and len(task.get("steps") or []) < int(
                want_task["steps_at_least"]
            ):
                fail(
                    f"task has {len(task.get('steps') or [])} step(s), "
                    f"expected at least {want_task['steps_at_least']}"
                )
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

        for unsupported in ("approval", "ui", "file"):
            if unsupported in expect:
                fail(
                    f"this scenario asserts {unsupported!r}, which the rig checks only "
                    f"through the capability that owns it — see gated-on"
                )

        out.ok = not out.failures
        return out


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
    )
    started = time.monotonic()
    results = await runner.run()
    totals = summarise(results)
    latencies = latency_table(results)

    payload = {
        "mode": "implemented-only" if args.implemented_only else "full",
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
    parser.add_argument("--variants", default="voice,text")
    parser.add_argument("--no-browser", action="store_true",
                        help="skip the browser transports (no console build needed)")
    parser.add_argument("--write-report", action="store_true",
                        help="write docs/LIVE_TEST_REPORT.md from this run")
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
