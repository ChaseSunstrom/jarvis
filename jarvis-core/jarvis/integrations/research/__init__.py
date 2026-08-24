"""`research` integration — the first real worker on the task registry.

    research:
      max_queries: 4        # angles to search the question from
      max_sources: 8        # pages to actually read
      per_domain: 2         # from any one site
      model: ""             # override the conversation model for this work

One service and one tool:

  ``research.run``       question -> a task id, and a job that fills it in
  ``deep_research``      the same thing, callable by the model

## Why this exists, and what it is not

``run_background_task`` used to mint an id, fire an event nothing listened to,
and instruct the model to say *"Accepted. The result arrives later."* Nothing
ran. `jarvis/tasks.py` closed half of that hole by making the record real; this
closes the other half by being something that actually reports through it.

A research run is:

    plan      one model call -> several search queries, not one
    search    each query through `web.search` (private; no cloud fallback)
    choose    dedupe across queries, rank, cap per domain
    read      each chosen page through `web.fetch`
    note      one model call per page: what does THIS page say about it
    write     one model call over the notes, citing by number

Every one of those is a step on the task, so the progress bar is a fraction of
real work. The run is `open_ended` until the read list is settled — a
percentage before then would be a guess — and becomes determinate the moment
the total is known.

## The three honesty rules it inherits

**Untrusted in, untrusted stays.** Search results and page text arrive fenced
from the `web` integration and are never unfenced. The note-taking call is the
weakest context in the system — no persona, no tools — so its prompt repeats
that the page is data and that instructions inside it are to be reported, not
followed.

**No cloud fallback.** If `web` is not configured the run fails saying so. It
does not reach for an engine the operator did not choose.

**Cancel means something.** `api/common.py` says a task marked cancelled may
still be running if its worker does not check. This worker checks, between
every step, and stops.

## What it deliberately does not do

It does not remember what it found unless asked. The report is a synthesis of
attacker-authored pages, and writing that into long-term memory on the model's
own initiative is a path from "a page said so" to "Jarvis believes it". The
`remember` flag exists, defaults off, and tags what it stores.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...services import ServiceCall
from ...tasks import STATUS_DONE, STATUS_ERROR, STATUS_RUNNING
from .plan import (
    MAX_QUERIES,
    MAX_SOURCES,
    PER_DOMAIN,
    Note,
    Source,
    collect_sources,
    format_report,
    is_empty_note,
    note_prompt,
    one_line_result,
    parse_queries,
    plan_prompt,
    rank_sources,
    read_steps,
    search_steps,
    synthesis_prompt,
)

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...tasks import Task

_LOGGER = logging.getLogger(__name__)

DOMAIN = "research"
#: `web` is where searching and reading live, and `llm` is both the model and
#: the tool registry. Neither is optional: without either there is no run to
#: have, and a research integration that silently does nothing is the failure
#: this whole area was built to stop repeating.
DEPENDENCIES = ["llm", "web"]

KIND = "research"
DATA_CONFIG = "config"
DATA_RUNS = "runs"

MAX_QUESTION_CHARS = 400
#: How long one model call may take before the step is called failed. A note on
#: one page is a small ask; a minute is already generous and the alternative is
#: a task that sits at "reading page 4" for ever.
MODEL_TIMEOUT = 180.0


@dataclass
class ResearchConfig:
    max_queries: int = 4
    max_sources: int = 8
    per_domain: int = PER_DOMAIN
    #: Empty means "whatever the conversation agent uses". A smaller, faster
    #: model is often the right call here: note-taking over one page is an
    #: extraction job, and there are `max_sources` of them.
    model: str = ""
    #: Results per search. More than a handful mostly adds near-duplicates.
    search_limit: int = 8

    @classmethod
    def from_config(cls, config: Any) -> "ResearchConfig":
        data = config if isinstance(config, dict) else {}

        def _int(key: str, default: int, cap: int) -> int:
            try:
                value = int(data.get(key, default) or default)
            except (TypeError, ValueError):
                value = default
            return max(1, min(value, cap))

        return cls(
            max_queries=_int("max_queries", 4, MAX_QUERIES),
            max_sources=_int("max_sources", 8, MAX_SOURCES),
            per_domain=_int("per_domain", PER_DOMAIN, 5),
            model=str(data.get("model") or "").strip(),
            search_limit=_int("search_limit", 8, 20),
        )


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def _store(jarvis: "Jarvis") -> dict[str, Any]:
    return jarvis.data.setdefault(DOMAIN, {})


async def async_setup(jarvis: "Jarvis", config: Any = None) -> bool:
    cfg = ResearchConfig.from_config(config)
    store = _store(jarvis)
    store[DATA_CONFIG] = cfg
    store.setdefault(DATA_RUNS, {})

    _register_services(jarvis, cfg)
    _register_tools(jarvis)

    async def _shutdown() -> None:
        """Stop every run in flight, and let the registry tell the truth later.

        The tasks are left `running` in the store on purpose: `Task.restored()`
        turns those into errors on the next load, which is the honest record —
        the work did not finish and nothing is going to pick it up.
        """
        runs = list(store.get(DATA_RUNS, {}).values())
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    jarvis.register_shutdown(_shutdown)
    _LOGGER.info(
        "research ready: %d queries, %d sources, %d per domain",
        cfg.max_queries,
        cfg.max_sources,
        cfg.per_domain,
    )
    return True


def _register_services(jarvis: "Jarvis", cfg: ResearchConfig) -> None:
    async def handle_run(call: ServiceCall) -> dict[str, Any]:
        question = str(call.get("question") or call.get("query") or "")
        task = await async_start(
            jarvis,
            question,
            remember=bool(call.get("remember")),
            source=str(call.get("source") or "service"),
        )
        if task is None:
            return {
                "status": "error",
                "error": "research needs a question",
            }
        return {"status": "started", "task_id": task.id, "title": task.title}

    jarvis.services.register(
        DOMAIN,
        "run",
        handle_run,
        supports_response=True,
        description=(
            "Research a question across several web searches and pages. Runs in "
            "the background and reports through the task list."
        ),
        fields={
            "question": {"description": "What to find out.", "required": True},
            "remember": {
                "description": (
                    "Store the finished report as a durable note. Off by "
                    "default: the report is a synthesis of pages anyone can "
                    "write."
                )
            },
            "source": {"description": "Who asked, for the task's record."},
        },
    )


def _register_tools(jarvis: "Jarvis") -> None:
    registry = jarvis.data.get("llm_tools")
    if registry is None or not hasattr(registry, "register"):
        _LOGGER.debug("research: no LLM tool registry; the service still works")
        return

    from ...llm.tools import TIER_DIRECT, schema_object

    async def tool_research(args: dict[str, Any], context: Any = None) -> Any:
        task = await async_start(
            jarvis,
            str(args.get("question") or ""),
            remember=bool(args.get("remember")),
            source="conversation",
        )
        if task is None:
            return {"status": "error", "error": "I need a question to research."}
        # The wording matters. The old background-task tool told the model to
        # say the work was under way when nothing was running; this one may say
        # exactly that, because something is — and it says where to look, so
        # "later" is a place rather than a promise.
        return {
            "status": "started",
            "task_id": task.id,
            "steps": len(task.steps),
            "message": (
                "Research has started and is running now. Tell the user it is "
                "under way and will take a minute or two, and that its progress "
                "is on the Tasks page. Do not invent findings — you have none "
                "yet."
            ),
        }

    registry.register(
        name="deep_research",
        description=(
            "Research a question properly: several web searches from different "
            "angles, then read the best pages and write up what they say with "
            "citations. Takes a minute or two and runs in the background — it "
            "returns a task id immediately, NOT an answer. Use it when a "
            "question needs more than one search; use web_search for a quick "
            "look-up."
        ),
        parameters=schema_object(
            {
                "question": {
                    "type": "string",
                    "description": "the question to research, in full",
                },
                "remember": {
                    "type": "boolean",
                    "description": (
                        "store the report as a durable note — only when the "
                        "user asked for that"
                    ),
                },
            },
            ["question"],
        ),
        handler=tool_research,
        tier=TIER_DIRECT,
    )


# ---------------------------------------------------------------------------
# starting a run
# ---------------------------------------------------------------------------
async def async_start(
    jarvis: "Jarvis",
    question: str,
    *,
    remember: bool = False,
    source: str = "",
) -> "Task | None":
    """Record the task, start the worker, and return immediately."""
    question = " ".join(str(question or "").split())[:MAX_QUESTION_CHARS]
    if not question:
        return None

    registry = getattr(jarvis, "tasks", None)
    if registry is None:  # pragma: no cover - core always builds one
        _LOGGER.error("research: no task registry; refusing to run untracked work")
        return None

    task = await registry.async_add(
        question,
        kind=KIND,
        # One known step, and open-ended until the searches say how many pages
        # there are to read. A denominator before that point is a guess.
        steps=["plan the searches"],
        open_ended=True,
        source=source,
        detail="planning",
    )
    cfg = _store(jarvis).get(DATA_CONFIG) or ResearchConfig()
    runs = _store(jarvis).setdefault(DATA_RUNS, {})
    run = asyncio.ensure_future(_drive(jarvis, cfg, task.id, question, remember))
    runs[task.id] = run
    run.add_done_callback(lambda _f, tid=task.id: runs.pop(tid, None))
    return task


# ---------------------------------------------------------------------------
# the worker
# ---------------------------------------------------------------------------
class _Stopped(Exception):
    """The task was cancelled or forgotten from a client. Not an error."""


async def _drive(
    jarvis: "Jarvis", cfg: ResearchConfig, task_id: str, question: str, remember: bool
) -> None:
    registry = jarvis.tasks
    try:
        await _run(jarvis, cfg, task_id, question, remember)
    except _Stopped:
        _LOGGER.info("research %s stopped at the user's request", task_id)
    except asyncio.CancelledError:
        # Shutdown. Left `running` deliberately: `Task.restored()` marks it
        # errored on the next load, which is the honest record of work that did
        # not finish and that nothing will resume.
        raise
    except Exception as err:  # noqa: BLE001 - a worker must never take the loop down
        _LOGGER.exception("research %s failed", task_id)
        await registry.async_update(
            task_id, status=STATUS_ERROR, error=f"{type(err).__name__}: {err}"[:400]
        )


async def _run(
    jarvis: "Jarvis", cfg: ResearchConfig, task_id: str, question: str, remember: bool
) -> None:
    registry = jarvis.tasks
    await registry.async_update(task_id, status=STATUS_RUNNING)

    # --- plan -------------------------------------------------------------
    _check(jarvis, task_id)
    await registry.async_update(task_id, step=0, step_status=STATUS_RUNNING)
    planned = await _ask_model(jarvis, cfg, plan_prompt(question, cfg.max_queries))
    queries = parse_queries(planned, question=question, limit=cfg.max_queries)
    await registry.async_update(
        task_id,
        step=0,
        step_status=STATUS_DONE,
        step_detail=f"{len(queries)} search{'' if len(queries) == 1 else 'es'}",
        add_steps=search_steps(queries),
        detail="searching",
    )

    # --- search -----------------------------------------------------------
    per_query: list[tuple[str, list[dict]]] = []
    search_failures: list[str] = []
    for offset, query in enumerate(queries):
        _check(jarvis, task_id)
        index = 1 + offset
        await registry.async_update(task_id, step=index, step_status=STATUS_RUNNING)
        # The same tool events a chat turn fires, so a research run reads as
        # work happening rather than as a bar that moves every thirty seconds.
        started = time.monotonic()
        call_id = registry.tool_started(
            task_id, name="web_search", arguments={"query": query}, index=offset + 1,
            total=len(queries),
        )
        result = await _call(jarvis, "web", "search", {"query": query, "limit": cfg.search_limit})
        registry.tool_finished(
            task_id,
            name="web_search",
            call_id=call_id,
            ok=result.get("status") == "ok",
            error=str(result.get("error") or "")[:200],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        if result.get("status") != "ok":
            reason = str(result.get("error") or result.get("message") or "search failed")
            search_failures.append(reason)
            await registry.async_update(
                task_id, step=index, step_status=STATUS_ERROR, step_detail=reason[:200]
            )
            continue
        results = [r for r in result.get("results") or [] if isinstance(r, dict)]
        per_query.append((query, results))
        registry.output(
            task_id,
            f"{query} — {len(results)} result{'' if len(results) == 1 else 's'}\n"
            + "\n".join(f"  {r.get('url', '')}" for r in results[:5]),
            stream="note",
        )
        await registry.async_update(
            task_id,
            step=index,
            step_status=STATUS_DONE,
            step_detail=f"{len(results)} result{'' if len(results) == 1 else 's'}",
        )

    if not per_query:
        # Every search failed. Saying so beats a report written from nothing,
        # and the reason is the operator's actual next action — usually that
        # SEARXNG_URL is unset or the container is down.
        await registry.async_update(
            task_id,
            status=STATUS_ERROR,
            error=search_failures[0] if search_failures else "no search returned anything",
        )
        return

    sources = collect_sources(per_query)
    chosen = rank_sources(sources, limit=cfg.max_sources, per_domain=cfg.per_domain)
    if not chosen:
        await registry.async_update(
            task_id,
            status=STATUS_ERROR,
            error=f"nothing was found for {len(queries)} search"
            f"{'' if len(queries) == 1 else 'es'}",
        )
        return

    # The total is known now, so the bar can stop being indeterminate.
    read_from = 1 + len(queries)
    await registry.async_update(
        task_id,
        add_steps=read_steps(chosen),
        open_ended=False,
        detail=f"reading {len(chosen)} of {len(sources)} pages found",
    )

    # --- read -------------------------------------------------------------
    notes: list[Note] = []
    for offset, source in enumerate(chosen):
        _check(jarvis, task_id)
        index = read_from + offset
        await registry.async_update(task_id, step=index, step_status=STATUS_RUNNING)
        started = time.monotonic()
        call_id = registry.tool_started(
            task_id, name="web_fetch", arguments={"url": source.url},
            index=offset + 1, total=len(chosen),
        )
        note = await _read_one(jarvis, cfg, question, source)
        registry.tool_finished(
            task_id,
            name="web_fetch",
            call_id=call_id,
            ok=note.ok,
            error=(note.error or "")[:200],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        notes.append(note)
        if note.text:
            # Findings accumulate on screen instead of appearing all at once in
            # the report: this is the sentence the page contributed.
            registry.output(task_id, f"{source.url}\n  {note.text[:400]}", stream="note")
        await registry.async_update(
            task_id,
            step=index,
            step_status=STATUS_DONE if note.ok else STATUS_ERROR,
            step_detail=(note.error or "nothing relevant" if not note.text else "")[:200],
        )

    # --- write it up ------------------------------------------------------
    _check(jarvis, task_id)
    write_step = read_from + len(chosen)
    await registry.async_update(
        task_id, step=write_step, step_status=STATUS_RUNNING, detail="writing it up"
    )

    usable = [n for n in notes if n.ok and n.text]
    if usable:
        answer = await _ask_model(jarvis, cfg, synthesis_prompt(question, notes))
    else:
        # Nothing readable. A synthesis call here would be asked to write an
        # answer from an empty list, and would oblige — from its own training,
        # uncited, indistinguishable from a researched one.
        answer = ""
    report = format_report(question, answer, notes, queries=queries, found=len(sources))

    await registry.async_update(
        task_id,
        step=write_step,
        step_status=STATUS_DONE,
        status=STATUS_DONE if usable else STATUS_ERROR,
        result=report,
        detail=one_line_result(notes, len(sources)),
        error="" if usable else "no page could be read, so there is nothing to report",
    )

    if remember and usable:
        await _remember(jarvis, question, report)


def _check(jarvis: "Jarvis", task_id: str) -> None:
    """Stop if the task was cancelled or forgotten from a client.

    Called between every step. This is what makes the CANCEL button honest:
    `api/common.py` warns that a worker which does not check may keep running,
    and this is the worker that does.
    """
    task = jarvis.tasks.get(task_id)
    if task is None or task.finished:
        raise _Stopped(task_id)


async def _read_one(
    jarvis: "Jarvis", cfg: ResearchConfig, question: str, source: Source
) -> Note:
    """Fetch one page and take notes on it. Never raises."""
    fetched = await _call(jarvis, "web", "fetch", {"url": source.url})
    if fetched.get("status") != "ok":
        reason = str(fetched.get("error") or fetched.get("message") or "could not be read")
        return Note(source=source, ok=False, error=reason[:200])

    text = str(fetched.get("text") or "")
    if not text.strip():
        return Note(source=source, ok=False, error="the page had no text")

    try:
        said = await _ask_model(jarvis, cfg, note_prompt(question, source, text))
    except Exception as err:  # noqa: BLE001
        return Note(source=source, ok=False, error=f"could not read it: {err}"[:200])

    # `ok` is "the page was reached and read", which is a different fact from
    # "it had something to say". A page that genuinely does not address the
    # question is a real, reportable outcome — not a failure.
    return Note(source=source, ok=True, text="" if is_empty_note(said) else said.strip())


async def _remember(jarvis: "Jarvis", question: str, report: str) -> None:
    """Store the report, marked for what it is.

    `allow_untrusted` because the report IS derived from pages anyone can
    write. The tags are not decoration: they are how a person listing their
    notes can see which of them came from the open web.
    """
    try:
        await jarvis.services.async_call(
            "memory",
            "add",
            {
                "text": f"Research — {question}\n\n{report}",
                "tags": ["research", "from-the-web"],
                "source": "research",
                "allow_untrusted": True,
            },
            blocking=True,
        )
    except Exception:  # noqa: BLE001 - a finished report is not lost over this
        _LOGGER.warning("research: could not store the report as a note", exc_info=True)


# --- talking to the rest of the system -----------------------------------------

async def _call(
    jarvis: "Jarvis", domain: str, service: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Call a service, turning any failure into the shape callers already read."""
    try:
        result = await jarvis.services.async_call(
            domain, service, data, blocking=True, return_response=True
        )
    except Exception as err:  # noqa: BLE001
        return {"status": "error", "error": f"{domain}.{service} failed: {err}"[:200]}
    return result if isinstance(result, dict) else {"status": "error", "error": "no response"}


async def _ask_model(jarvis: "Jarvis", cfg: ResearchConfig, prompt: str) -> str:
    """One model call, no tools, reasoning off, reasoning stripped anyway.

    No tools on purpose: every call here is an extraction or a summary over text
    that is already in the prompt. A tool loop would give attacker-authored page
    text a dispatcher to talk to, which is the whole thing the fencing exists to
    prevent.
    """
    from ...llm.agent import ThinkStripper

    # The conversation agent's own client, not `jarvis.data["llm_client"]` —
    # that key holds the shared *httpx* client both the model and YAML tools
    # borrow, and reaching for it gets an object with no `chat` on it.
    agent = jarvis.data.get("llm")
    client = getattr(agent, "client", None)
    if client is None:
        raise RuntimeError("no model is configured, so there is nothing to research with")

    stream = client.chat(
        model=cfg.model or getattr(agent, "model", None) or None,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        think=False,
    )
    result = await asyncio.wait_for(stream, MODEL_TIMEOUT)
    stripper = ThinkStripper()
    return (stripper.feed(result.content or "") + stripper.flush()).strip()
