"""`schedule` integration — reminders, and work put off until later.

    schedule:
      grace_seconds: 21600     # how late a one-shot may still run
      jobs:
        - id: morning_brief
          title: Morning brief
          kind: notify
          when: {mode: daily, at: "07:30"}
          message: Good morning. Here is what today looks like.

Jobs may also be created at runtime — by the model, from the console — and
those live in `<config>/.storage/schedule.json`.

## What this is, next to automations

Automations already fire on `time`, `time_pattern` and `sun`, and they are the
right tool for *"every evening, if it is dark, turn the hall light on"*. Three
things they are not:

* **One-shot.** *"Remind me at seven"* is a job that happens once and is then
  gone. Writing that as an automation means writing one and deleting it.
* **Speakable.** An automation is authored in YAML or in the console. This can
  be created mid-sentence by the assistant, which is the whole point of a
  reminder.
* **Visible as work.** Every firing mints a task, so a scheduled research run
  shows up on `/tasks` and in the phone's overlay with the same progress bar as
  everything else. That is what *"schedule tasks … should show as a progress
  bar/UI visual"* asked for.

## Three kinds, and the door each one opens

`notify` says something to the user. `research` starts a deep-research run.
`service` calls a service — and that one is **gated exactly as an automation
is**: `reach.py` decides whether the action list needs a human, and a scheduled
`lock.unlock` is held for the same approval a scheduled automation would be.
Without that, "schedule it" would be the way round every Tier-3 gate in the
system, which is a hole shaped precisely like the feature.

The model may create `notify` and `research` jobs. It may **not** create
`service` jobs: those come from the config file or an authenticated console
request, because a tool that schedules arbitrary service calls is a tool that
launders a prompt injection through a delay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...automation.util import configured_clock
from ...services import ServiceCall
from ...store import Store
from ...tasks import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING
from .plan import (
    DEFAULT_GRACE_SECONDS,
    When,
    catch_up,
    describe_when,
    next_fire,
    parse_when,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "schedule"
DEPENDENCIES = ["llm"]

STORE_KEY = "schedule"
DATA_MANAGER = "manager"

KIND_NOTIFY = "notify"
KIND_RESEARCH = "research"
KIND_SERVICE = "service"
KIND_CODE = "code"
KINDS = (KIND_NOTIFY, KIND_RESEARCH, KIND_SERVICE, KIND_CODE)

#: Kinds the MODEL may schedule. `service` and `code` are deliberately absent:
#: a tool that schedules arbitrary service calls launders a prompt injection
#: through a delay, arriving with no turn to attribute it to — and a coding job
#: is the same shape with a repository on the end of it. Starting one directly
#: is Tier 3 and asks a human; scheduling one must not be the way round that.
#: Kinds slow enough to belong in the engine's queue rather than firing on
#: the spot. A reminder must not wait behind a twenty-minute coding job.
QUEUED_KINDS = frozenset({"research", "code"})

MODEL_KINDS = (KIND_NOTIFY, KIND_RESEARCH)

MAX_JOBS = 200
MAX_TITLE = 200
MAX_MESSAGE = 2000
#: How often the loop wakes when nothing is due. Short enough that a job
#: created for "in six minutes" is not eight, long enough to be free.
TICK_SECONDS = 20.0
EVENT_FIRED = "jarvis_schedule_fired"

#: Longest a stop waits for the ticker to unwind before moving on.
STOP_TIMEOUT = 5.0


@dataclass
class Job:
    id: str
    title: str
    kind: str = KIND_NOTIFY
    when: When = field(default_factory=When)
    #: `notify`: the message. `research`: the question. `service`: domain,
    #: service and data. `code`: the repository and the instruction.
    payload: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    #: Epoch seconds. None once a spent one-shot has nowhere left to go.
    next_at: float | None = None
    last_at: float = 0.0
    last_result: str = ""
    #: Firings that were due while nothing was running and did not happen.
    missed: int = 0
    created: float = field(default_factory=time.time)
    source: str = ""
    #: False for jobs from configuration.yaml — the file owns those.
    editable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "when": self.when.as_dict(),
            "describes": describe_when(self.when),
            "payload": dict(self.payload),
            "enabled": self.enabled,
            "next_at": self.next_at,
            "last_at": self.last_at,
            "last_result": self.last_result,
            "missed": self.missed,
            "created": self.created,
            "source": self.source,
            "editable": self.editable,
        }


def job_from_dict(raw: Any, *, editable: bool = True) -> Job | None:
    if not isinstance(raw, dict):
        return None
    when = parse_when(raw.get("when") or raw)
    if when is None:
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        # Inferred from what the payload actually carries, because a job with a
        # `service` and no `kind` plainly means one thing.
        kind = (
            KIND_SERVICE
            if raw.get("service")
            else KIND_CODE
            if raw.get("repo") and raw.get("instruction")
            else KIND_RESEARCH
            if raw.get("question")
            else KIND_NOTIFY
        )
    payload: dict[str, Any] = {}
    if kind == KIND_NOTIFY:
        payload["message"] = str(raw.get("message") or raw.get("text") or "")[:MAX_MESSAGE]
    elif kind == KIND_RESEARCH:
        payload["question"] = str(raw.get("question") or raw.get("message") or "")[:MAX_MESSAGE]
        if not payload["question"]:
            return None
    elif kind == KIND_CODE:
        payload["repo"] = str(raw.get("repo") or raw.get("repository") or "").strip()[:200]
        payload["instruction"] = str(
            raw.get("instruction") or raw.get("message") or ""
        )[:MAX_MESSAGE]
        if not payload["repo"] or not payload["instruction"]:
            return None
    else:
        service = str(raw.get("service") or "")
        if "." not in service:
            return None
        payload["service"] = service
        payload["data"] = raw.get("data") if isinstance(raw.get("data"), dict) else {}

    title = str(raw.get("title") or "").strip()[:MAX_TITLE]
    if not title:
        title = (
            payload.get("question")
            or payload.get("message")
            or payload.get("service")
            or (
                f"{payload['repo']}: {payload['instruction']}"
                if payload.get("repo")
                else ""
            )
            or "scheduled job"
        )[:MAX_TITLE]

    return Job(
        id=str(raw.get("id") or "").strip()[:64] or uuid.uuid4().hex[:12],
        title=title,
        kind=kind,
        when=when,
        payload=payload,
        enabled=bool(raw.get("enabled", True)),
        next_at=_float(raw.get("next_at")),
        last_at=_float(raw.get("last_at")) or 0.0,
        last_result=str(raw.get("last_result") or "")[:400],
        missed=int(_float(raw.get("missed")) or 0),
        created=_float(raw.get("created")) or time.time(),
        source=str(raw.get("source") or "")[:80],
        editable=editable,
    )


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ScheduleManager:
    """Every scheduled job, and the one loop that fires them."""

    def __init__(
        self,
        jarvis: "Jarvis",
        *,
        store: Store | None = None,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
    ) -> None:
        self.jarvis = jarvis
        self.store = store
        self.grace_seconds = max(60.0, float(grace_seconds or DEFAULT_GRACE_SECONDS))
        self.jobs: dict[str, Job] = {}
        self._loop: asyncio.Task | None = None
        self._wake = asyncio.Event()
        #: Jobs running right now.
        #:
        #: Tracked so shutdown can WAIT for them rather than cutting one off
        #: half-done — the same reasoning as the websocket layer's drain: a
        #: half-executed service call is worse than a slow stop. It is also
        #: what lets a test say "and then the firing finished" without sleeping.
        self._firing: set[asyncio.Task] = set()

    # --- the clock --------------------------------------------------------
    def now(self) -> datetime:
        """Local time in the zone the house is in, not the container's.

        `configured_clock` is the automation layer's own, so a scheduled job at
        07:00 and an automation at 07:00 mean the same instant. Two answers to
        that question would be a bug nobody could see from either side.
        """
        return configured_clock(self.jarvis).now()

    def _moment(self, epoch: float | None) -> datetime | None:
        if not epoch:
            return None
        return datetime.fromtimestamp(epoch, self.now().tzinfo)

    # --- persistence ------------------------------------------------------
    def add_from_config(self, raw: Any) -> None:
        for entry in raw or []:
            job = job_from_dict(entry, editable=False)
            if job is None:
                _LOGGER.warning("schedule: skipping an unusable job entry")
                continue
            self.jobs[job.id] = job

    async def async_load(self) -> None:
        if self.store is not None:
            data = await self.store.load()
            for entry in (data or {}).get("jobs") or []:
                job = job_from_dict(entry, editable=True)
                if job is None:
                    continue
                if job.id in self.jobs:
                    continue  # config wins; see the `mcp` integration for why
                self.jobs[job.id] = job
        # Reconcile every job with a clock that moved on without it, BEFORE the
        # loop starts, so a restart cannot fire a backlog on its way up.
        await self._async_settle()

    async def async_save(self) -> None:
        if self.store is None:
            return
        try:
            await self.store.save(
                {"jobs": [j.as_dict() for j in self.jobs.values() if j.editable]}
            )
        except Exception:  # pragma: no cover - a full disk is not a job failure
            _LOGGER.exception("schedule: could not save the job list")

    async def _async_settle(self) -> None:
        """Work out what each job missed while nothing was running."""
        now = self.now()
        changed = False
        for job in list(self.jobs.values()):
            if not job.enabled:
                continue
            decision = catch_up(
                job.when, self._moment(job.next_at), now, grace_seconds=self.grace_seconds
            )
            if decision.skipped:
                job.missed += decision.skipped
                job.last_result = decision.missed_reason or (
                    f"{decision.skipped} firing(s) missed while Jarvis was not running"
                )
                changed = True
            new_next = decision.next_at.timestamp() if decision.next_at else None
            if new_next != job.next_at:
                job.next_at = new_next
                changed = True
            if decision.fire:
                # Fired as a task like any other, so a late reminder is visible
                # rather than a surprise.
                self._spawn(job, late=True)
        if changed:
            await self.async_save()

    # --- the loop ---------------------------------------------------------
    def start(self) -> None:
        if self._loop is None or self._loop.done():
            self._loop = self.jarvis.async_create_task(self._run())

    async def stop(self) -> None:
        """Stop the ticker, then let anything mid-firing finish.

        That order: cancelling the loop first means no NEW job starts while the
        running ones are draining, which is the difference between a bounded
        stop and one that keeps finding more to do.
        """
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.cancel()
            # Bounded. Awaiting a task you have just cancelled is a courtesy —
            # it lets the loop unwind before anything else runs — and it must
            # not be able to hold a shutdown open: whatever the ticker is
            # suspended in, the process is going away regardless. Something in
            # this stack can leave that await outstanding under a test runner's
            # loop, and an unbounded wait there is a hang with no way out.
            with contextlib.suppress(
                asyncio.CancelledError, asyncio.TimeoutError, TimeoutError, Exception
            ):
                await asyncio.wait_for(asyncio.shield(loop), STOP_TIMEOUT)
        await self.async_drain()

    def wake(self) -> None:
        """A job changed; re-read the schedule now rather than at the next tick."""
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive one bad job
                _LOGGER.exception("schedule: a tick failed")
            self._wake.clear()
            await self._idle(self._sleep_for())

    async def _idle(self, seconds: float) -> None:
        """Sleep until woken or until `seconds` pass. Cancellable.

        `asyncio.wait`, NOT `wait_for`, and the difference is not stylistic.
        `wait_for` signals its timeout by RAISING `TimeoutError`, so a caller
        has to swallow that to treat "nothing happened" as normal — and in
        CPython 3.11 `wait_for` also converts an outer cancellation into
        `TimeoutError` when the two race. Swallowing one therefore swallows the
        other, and a ticker that eats its own cancellation is a ticker that
        cannot be stopped: `stop()` hung, and with a bound on the wait it kept
        running through shutdown, free to fire a job into a process that was
        going away.

        `asyncio.wait` returns on timeout rather than raising, so there is
        nothing to suppress and a `CancelledError` can only mean the one thing.
        """
        waiter = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({waiter}, timeout=max(0.1, seconds))
        finally:
            if not waiter.done():
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await waiter

    def _sleep_for(self) -> float:
        """Until the next job, capped. Not a fixed poll.

        A schedule with nothing due for nine hours should not wake the process
        1,600 times to find that out; a schedule with something due in forty
        seconds should not wait a fixed five minutes.
        """
        soonest = min(
            (j.next_at for j in self.jobs.values() if j.enabled and j.next_at),
            default=None,
        )
        if soonest is None:
            return TICK_SECONDS * 15
        return max(1.0, min(soonest - time.time(), TICK_SECONDS * 15))

    async def _tick(self) -> None:
        now = self.now()
        stamp = now.timestamp()
        changed = False
        for job in list(self.jobs.values()):
            if not job.enabled or not job.next_at or job.next_at > stamp:
                continue
            decision = catch_up(
                job.when, self._moment(job.next_at), now, grace_seconds=self.grace_seconds
            )
            job.missed += decision.skipped
            job.next_at = decision.next_at.timestamp() if decision.next_at else None
            changed = True
            if decision.fire:
                self._spawn(job, late=False)
            elif decision.missed_reason:
                job.last_result = decision.missed_reason
        if changed:
            await self.async_save()

    # --- firing -----------------------------------------------------------
    def _spawn(self, job: Job, *, late: bool) -> asyncio.Task:
        task = self.jarvis.async_create_task(self._async_fire(job, late=late))
        self._firing.add(task)
        task.add_done_callback(self._firing.discard)
        return task

    async def async_drain(self, timeout: float = 30.0) -> None:
        """Wait for whatever is mid-firing. Never raises."""
        while self._firing:
            running = list(self._firing)
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError, Exception):
                await asyncio.wait_for(
                    asyncio.gather(*running, return_exceptions=True), timeout
                )
            if running == list(self._firing):
                # Nothing finished within the timeout. Letting go is better
                # than a shutdown that never completes.
                break

    async def _async_fire(self, job: Job, *, late: bool) -> None:
        """Run one job, as a task on the registry so it is visible.

        The SLOW kinds go through the engine: a scheduled research run and a
        coding job that fall in the same minute are two conversations against
        one model server, and the queue is what stops a third from making all of
        them slow. `notify` and `service` do not — a reminder queued behind a
        twenty-minute coding job is a reminder that arrives after the thing it
        was reminding you about, which is a worse failure than an unqueued one.
        """
        engine = getattr(self.jarvis, "taskengine", None) if job.kind in QUEUED_KINDS else None
        registry = getattr(self.jarvis, "tasks", None)
        task = None
        if registry is not None:
            task = await registry.async_add(
                job.title,
                kind="scheduled",
                source=job.source or "schedule",
                detail=("running late" if late else describe_when(job.when)),
            )
            if engine is not None:
                async def _worker(_task_id: str, job=job, late=late) -> None:
                    await self._async_fire_now(job, late=late, task_id=_task_id)

                if engine.submit(task.id, _worker, kind="scheduled"):
                    return
            await registry.async_update(task.id, status=STATUS_RUNNING)

        await self._async_fire_now(job, late=late, task_id=task.id if task else "")

    async def _async_fire_now(self, job: Job, *, late: bool, task_id: str = "") -> None:
        """The firing itself, once a slot is free."""
        registry = getattr(self.jarvis, "tasks", None)
        task = registry.get(task_id) if registry is not None and task_id else None

        job.last_at = time.time()
        try:
            summary = await self._async_run(job)
            job.last_result = summary[:400]
            if task is not None:
                await registry.async_update(task.id, status=STATUS_DONE, result=summary[:400])
        except Exception as err:  # noqa: BLE001 - one bad job is not the loop
            _LOGGER.exception("schedule: %s failed", job.id)
            job.last_result = f"{type(err).__name__}: {err}"[:400]
            if task is not None:
                await registry.async_update(task.id, status=STATUS_ERROR, error=job.last_result)
        finally:
            await self.async_save()
            with contextlib.suppress(Exception):
                self.jarvis.bus.fire(
                    EVENT_FIRED, {"job": job.as_dict(), "late": late}
                )

    async def _async_run(self, job: Job) -> str:
        if job.kind == KIND_NOTIFY:
            return await self._notify(job)
        if job.kind == KIND_RESEARCH:
            return await self._research(job)
        if job.kind == KIND_CODE:
            return await self._code(job)
        return await self._service(job)

    async def _notify(self, job: Job) -> str:
        message = str(job.payload.get("message") or job.title)
        # A moment first: the notifications inbox keeps it until it is read,
        # on every console. Before this the phone was the only channel, and a
        # house with no phone paired got "remind me in a minute" as a task
        # result and a log line — a surface, but not one anybody watches for
        # a reminder.
        try:
            await self.jarvis.services.async_call(
                "notifications", "add",
                {"kind": "reminder", "title": message, "body": message, "source": "schedule"},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.info("schedule: no notifications inbox for %s (%s)", job.id, err)
        # Then `companion`, which is the channel that knows which device the
        # user is at. Absent it, the task's own result is still the record.
        try:
            await self.jarvis.services.async_call(
                "companion", "notify", {"message": message}, blocking=True
            )
            return f"told you: {message}"
        except Exception as err:  # noqa: BLE001
            _LOGGER.info("schedule: no companion channel for %s (%s)", job.id, err)
            return message

    async def _research(self, job: Job) -> str:
        from ..research import async_start

        started = await async_start(
            self.jarvis, str(job.payload.get("question") or ""), source="schedule"
        )
        if started is None:
            raise RuntimeError("research is not available on this server")
        # The research run is its own task with its own progress. This one's
        # job was to start it, and saying which one keeps the two connected.
        return f"research started (task {started.id})"

    async def _code(self, job: Job) -> str:
        """Start a Jarvis Code job.

        Not through `code.run`, which is gated: this job was written by an
        authenticated caller or by configuration.yaml — the same authority the
        console's own START button carries — and holding it for a second yes at
        three in the morning is a reminder nobody is awake to answer. The model
        cannot create one of these at all; see `MODEL_KINDS`.
        """
        from ..code import async_start

        started = await async_start(
            self.jarvis,
            str(job.payload.get("repo") or ""),
            str(job.payload.get("instruction") or ""),
            source="schedule",
        )
        if isinstance(started, str):
            raise RuntimeError(started)
        return f"coding job started (task {started.id})"

    async def _service(self, job: Job) -> str:
        """Call a service — through the same gate an automation goes through.

        Without this, "schedule it" would be the way round every Tier-3 control
        in the system: a held action, deferred by sixty seconds, arriving with
        nobody to ask.
        """
        from ...automation.reach import describe_reach, needs_approval

        service = str(job.payload.get("service") or "")
        data = job.payload.get("data") or {}
        action = [{"service": service, "data": data}]
        if needs_approval(action):
            approved = await self._ask(job, describe_reach(action))
            if not approved:
                return f"not run: {service} needs a yes and did not get one"

        domain, _, name = service.partition(".")
        await self.jarvis.services.async_call(domain, name, dict(data), blocking=True)
        return f"ran {service}"

    async def _ask(self, job: Job, why: str) -> bool:
        """Put the held action to a human. Fails CLOSED in every direction."""
        try:
            answer = await self.jarvis.services.async_call(
                "companion",
                "ask",
                {
                    "question": f"{job.title} is due. {why} Run it?",
                    "options": ["yes", "no"],
                },
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("schedule: could not ask about %s: %s", job.id, err)
            return False
        text = ""
        if isinstance(answer, dict):
            text = str(answer.get("answer") or answer.get("status") or "")
        return text.strip().lower() in ("yes", "y", "ok", "approve", "approved")

    # --- editing ----------------------------------------------------------
    def listing(self) -> list[dict[str, Any]]:
        return [
            j.as_dict()
            for j in sorted(
                self.jobs.values(), key=lambda j: (j.next_at or float("inf"), j.created)
            )
        ]

    async def async_add(self, data: dict[str, Any], *, allow_service: bool) -> dict[str, Any]:
        job = job_from_dict(data, editable=True)
        if job is None:
            return {"status": "error", "error": "that is not a schedule I can read"}
        if job.kind not in MODEL_KINDS and not allow_service:
            what = "a service call" if job.kind == KIND_SERVICE else "a coding job"
            return {
                "status": "error",
                "error": (
                    f"scheduling {what} is not something the assistant may do. "
                    "Add it in configuration.yaml or from the console."
                ),
            }
        if len(self.jobs) >= MAX_JOBS and job.id not in self.jobs:
            return {"status": "error", "error": f"there are already {MAX_JOBS} scheduled jobs"}
        held = self.jobs.get(job.id)
        if held is not None and not held.editable:
            return {
                "status": "error",
                "error": f"{job.id!r} comes from configuration.yaml; edit it there",
            }

        upcoming = next_fire(job.when, self.now())
        if upcoming is None:
            return {"status": "error", "error": "that schedule never comes round"}
        if not job.when.recurring and upcoming <= self.now():
            return {"status": "error", "error": "that time has already passed"}
        job.next_at = upcoming.timestamp()

        self.jobs[job.id] = job
        await self.async_save()
        self.wake()
        return {"status": "ok", "job": job.as_dict()}

    async def async_remove(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(str(job_id or ""))
        if job is None:
            return {"status": "error", "error": f"no scheduled job {job_id!r}"}
        if not job.editable:
            return {
                "status": "error",
                "error": f"{job.id!r} comes from configuration.yaml; remove it there",
            }
        self.jobs.pop(job.id, None)
        await self.async_save()
        self.wake()
        return {"status": "ok", "removed": job.id}

    async def async_set_enabled(self, job_id: str, enabled: bool) -> dict[str, Any]:
        job = self.jobs.get(str(job_id or ""))
        if job is None:
            return {"status": "error", "error": f"no scheduled job {job_id!r}"}
        job.enabled = bool(enabled)
        if job.enabled and not job.next_at:
            upcoming = next_fire(job.when, self.now())
            job.next_at = upcoming.timestamp() if upcoming else None
        await self.async_save()
        self.wake()
        return {"status": "ok", "job": job.as_dict()}


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def get_manager(jarvis: "Jarvis") -> ScheduleManager | None:
    store = jarvis.data.get(DOMAIN)
    return store.get(DATA_MANAGER) if isinstance(store, dict) else None


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = config if isinstance(config, dict) else {}
    store = jarvis.data.setdefault(DOMAIN, {})
    manager = ScheduleManager(
        jarvis,
        store=Store(jarvis.config_dir, STORE_KEY),
        grace_seconds=float(cfg.get("grace_seconds") or DEFAULT_GRACE_SECONDS),
    )
    manager.add_from_config(cfg.get("jobs"))
    await manager.async_load()
    store[DATA_MANAGER] = manager
    manager.start()

    _register_services(jarvis, manager)
    _register_tools(jarvis, manager)
    jarvis.register_shutdown(manager.stop)

    _LOGGER.info("schedule ready: %d job(s)", len(manager.jobs))
    return True


def _register_services(jarvis: "Jarvis", manager: ScheduleManager) -> None:
    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        return {"jobs": manager.listing()}

    async def handle_add(call: ServiceCall) -> dict[str, Any]:
        # A service call is an authenticated caller — an automation, the
        # console, a script — so it may schedule a service call. The MODEL's
        # tool cannot; see `_register_tools`.
        return await manager.async_add(dict(call.data), allow_service=True)

    async def handle_remove(call: ServiceCall) -> dict[str, Any]:
        return await manager.async_remove(str(call.get("id") or ""))

    async def handle_enable(call: ServiceCall) -> dict[str, Any]:
        return await manager.async_set_enabled(
            str(call.get("id") or ""), bool(call.get("enabled", True))
        )

    for name, handler, description in (
        ("list", handle_list, "Every scheduled job, soonest first."),
        ("add", handle_add, "Schedule a reminder, a research run, a coding job or a service call."),
        ("remove", handle_remove, "Forget a scheduled job."),
        ("enable", handle_enable, "Turn a scheduled job on or off."),
    ):
        jarvis.services.register(
            DOMAIN, name, handler, supports_response=True, description=description
        )


def _register_tools(jarvis: "Jarvis", manager: ScheduleManager) -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("schedule: no LLM tool registry; the services still work")
        return

    from ...llm.tools import TIER_DIRECT, schema_object

    async def tool_schedule(args: dict[str, Any], context: Any = None) -> Any:
        payload = dict(args)
        kind = str(payload.get("kind") or KIND_NOTIFY).lower()
        if kind not in MODEL_KINDS:
            return {
                "status": "error",
                "error": (
                    "I can schedule a reminder or a research run. Scheduling a "
                    "service call has to be set up in the console."
                ),
            }
        payload["kind"] = kind
        payload["source"] = "conversation"
        # `allow_service=False` whatever the arguments said: this is the door
        # the model is holding, and it is narrower than the service's.
        result = await manager.async_add(payload, allow_service=False)
        if result.get("status") != "ok":
            return result
        job = result["job"]
        return {
            "status": "ok",
            "id": job["id"],
            "when": job["describes"],
            "message": (
                f"Scheduled: {job['title']} — {job['describes']}. Tell the user it "
                "is set, and when. It is on the Tasks page."
            ),
        }

    async def tool_list(args: dict[str, Any], context: Any = None) -> Any:
        return {
            "jobs": [
                {
                    "id": j["id"],
                    "title": j["title"],
                    "when": j["describes"],
                    "enabled": j["enabled"],
                    "kind": j["kind"],
                }
                for j in manager.listing()
            ]
        }

    async def tool_cancel(args: dict[str, Any], context: Any = None) -> Any:
        return await manager.async_remove(str(args.get("id") or ""))

    registry.register(
        name="schedule_task",
        description=(
            "Put something off until later: a reminder to say, or a research "
            "run to start. Give `at` an ISO timestamp for a one-off ('remind me "
            "at seven'), or `daily_at`/`days` for a repeat. Every firing shows "
            "up on the Tasks page. This cannot schedule actions on the house — "
            "those are set up in the console."
        ),
        parameters=schema_object(
            {
                "kind": {
                    "type": "string",
                    "enum": list(MODEL_KINDS),
                    "description": "'notify' to say something, 'research' to look something up",
                },
                "title": {"type": "string", "description": "a short name for it"},
                "message": {"type": "string", "description": "for a reminder: what to say"},
                "question": {"type": "string", "description": "for research: what to find out"},
                "at": {
                    "type": "string",
                    "description": "one-off: an ISO timestamp in the user's local time",
                },
                "daily_at": {"type": "string", "description": "repeat: 'HH:MM'"},
                "days": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "with daily_at, restrict to these days: mon..sun",
                },
                "every_minutes": {"type": "integer", "description": "repeat every N minutes"},
            },
            ["kind"],
        ),
        handler=_normalising(tool_schedule),
        tier=TIER_DIRECT,
    )
    registry.register(
        name="list_scheduled",
        description="What is scheduled, and when each next runs.",
        parameters=schema_object({}, []),
        handler=tool_list,
        tier=TIER_DIRECT,
    )
    registry.register(
        name="cancel_scheduled",
        description="Forget a scheduled job. Use list_scheduled to find its id.",
        parameters=schema_object(
            {"id": {"type": "string", "description": "the job's id"}}, ["id"]
        ),
        handler=tool_cancel,
        tier=TIER_DIRECT,
    )


def _normalising(handler: Any) -> Any:
    """Turn the tool's flat arguments into the `when` the manager reads.

    A flat schema on purpose: `{"at": "..."} `/`{"daily_at": "07:00"}` is what a
    model gets right, and a nested `{"when": {"mode": ..., "at": ...}}` is what
    it gets wrong — usually by inventing a mode.
    """

    async def call(args: dict[str, Any], context: Any = None) -> Any:
        data = dict(args or {})
        if data.get("every_minutes"):
            data["when"] = {"mode": "every", "minutes": data["every_minutes"]}
        elif data.get("daily_at"):
            data["when"] = {
                "mode": "weekly" if data.get("days") else "daily",
                "at": data["daily_at"],
                "days": data.get("days") or [],
            }
        elif data.get("at"):
            data["when"] = {"mode": "once", "at": data["at"]}
        return await handler(data, context)

    return call
