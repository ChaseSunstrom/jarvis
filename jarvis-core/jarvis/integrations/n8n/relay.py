"""Driving n8n's AI builder as a background job, with a person in the loop.

## Why this is a background task and not a tool that loops

A tool cannot hold a conversation with a human. `ToolRegistry.call` sees a
Tier-3 tool, raises the approval, returns `approval_required` and the turn
ENDS. When somebody answers minutes later the answer goes to whoever called
the approve API. There is no round of the model's conversation in which the
answer exists, so a tool that tried to loop
`builder -> ask -> read answer -> builder` could never read anything.

So the relay is shaped like a coding job: the tool mints a task and returns a
sentence immediately, and a coroutine owns the conversation from then on.
`registry.hold_question` is what lets that coroutine ask — it raises the same
approval a tool would, and waits on a private future for the reply.

This also lights up `STATUS_BLOCKED`, which `tasks.py` has defined and
documented since the beginning and which nothing has ever set. The console
already draws it — a solid bar and "waiting for you" rather than a spinner.

## Three rules it does not get to bend

**Every relayed question is marked untrusted.** Unconditionally, not by
inference. The question text was composed by a different AI and is about to be
rendered verbatim on somebody's lock screen. `_is_tainted` exists to infer
exactly this from a turn; here the provenance is known statically, which is
better.

**The workflow goes through `clean_workflow`.** No second write path, ever.
Rule 2 of the integration — what Jarvis writes arrives switched off, with no
credentials — is enforced by rebuilding the payload from four keys, and a
relay that POSTed the builder's JSON straight through would be precisely the
way round it.

**The model never sees the transcript.** It is prose from another AI, and a
tool result is read by the model as instructions-adjacent text. The model gets
a task id, one sentence, and the list of credentials to connect. The
transcript lives on the task, for the console.

## Two unproven assumptions, and the guards for them

That the HTTP body ends when the graph interrupts, and that a synthetic
workflow id keys a resumable thread. If the first is wrong the stream would
hang — hence the idle timeout in `builder.py`. If the second is wrong the
builder re-asks the same question forever — hence `MAX_RESUMES`, which fails
the task with a sentence rather than looping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...tasks import STATUS_BLOCKED, STATUS_DONE, STATUS_ERROR, STATUS_RUNNING
from .builder import BuilderClient, BuilderError, BuilderUnavailable
from .client import N8nError

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis
    from ...tasks import Task

_LOGGER = logging.getLogger(__name__)

__all__ = ["RelayResult", "drive", "MAX_RESUMES", "answer_for"]

#: How many times the conversation may be resumed before Jarvis decides the
#: thread is not pairing. Six is more turns than a real build takes and far
#: fewer than a loop.
MAX_RESUMES = 6

#: How long a person has to answer one question before the build gives up.
#: Longer than an approval, because this is a job somebody started and walked
#: away from, and shorter than forever.
QUESTION_TTL = 1800.0

#: The transcript kept for the console. Bounded because it is text from
#: another AI and the task store is on disk.
MAX_TRANSCRIPT = 120
MAX_LINE_CHARS = 1000


@dataclass
class RelayResult:
    """What the build ended as."""

    ok: bool
    summary: str
    workflow: dict[str, Any] | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)


def answer_for(kind: str, question: dict[str, Any], said: str | None) -> Any:
    """The `resumeData` for one answered interrupt.

    Split out and pure because the three shapes are the part most likely to be
    wrong against a real n8n, and they should be readable and testable without
    a stream.

    `said is None` means the person denied it or it expired. For a plain
    question that is "skipped"; for a plan it is a rejection; for a web fetch
    it is **deny**, never anything else — an unanswered permission request is
    not permission.
    """
    if kind == "questions":
        row: dict[str, Any] = {
            "questionId": str(question.get("questionId") or question.get("id") or ""),
            "question": str(question.get("question") or question.get("text") or ""),
        }
        if said is None:
            row["skipped"] = True
            row["selectedOptions"] = []
            return [row]
        options = _options(question)
        if said in options:
            row["selectedOptions"] = [said]
        else:
            row["selectedOptions"] = []
            row["customText"] = said
        return [row]

    if kind == "plan":
        if said is None:
            return {"action": "reject"}
        lowered = said.strip().lower()
        if lowered.startswith(("build", "yes", "approve", "go ahead", "ok")):
            # n8n wants the mode alongside an approval; without it the graph
            # approves the plan and then waits for a mode that never comes.
            return {"action": "approve", "mode": "build"}
        if lowered.startswith(("stop", "no", "cancel", "reject")):
            return {"action": "reject"}
        return {"action": "modify", "feedback": said}

    if kind == "web_fetch_approval":
        request_id = str(question.get("requestId") or question.get("id") or "")
        url = str(question.get("url") or "")
        if said is None:
            # The security default. An unanswered permission request is a
            # refusal, and it is never widened to a domain or to everything.
            return {"requestId": request_id, "url": url, "action": "deny"}
        lowered = said.strip().lower()
        if lowered.startswith(("allow this", "allow once", "yes", "allow")):
            return {"requestId": request_id, "url": url, "action": "allow_once"}
        return {"requestId": request_id, "url": url, "action": "deny"}

    return {}


def _options(question: dict[str, Any]) -> list[str]:
    raw = question.get("options") or question.get("choices") or []
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("text") or item.get("value") or "")
        else:
            text = str(item)
        text = text.strip()
        if text and text not in out:
            out.append(text)
    return out


class _Conversation:
    """State for one build. A class only because six fields travelled together."""

    def __init__(self) -> None:
        self.transcript: list[dict[str, str]] = []
        self.candidate: dict[str, Any] | None = None
        self.pending: tuple[str, list[dict[str, Any]]] | None = None
        self.failure = ""

    def say(self, role: str, text: str) -> None:
        said = " ".join(str(text or "").split())[:MAX_LINE_CHARS]
        if not said:
            return
        self.transcript.append({"role": role, "text": said})
        del self.transcript[:-MAX_TRANSCRIPT]

    def take(self, message: dict[str, Any]) -> None:
        """One message from the builder. An unknown type is a no-op."""
        kind = str(message.get("type") or "")

        if kind == "message":
            self.say("builder", str(message.get("text") or ""))
        elif kind == "tool":
            title = str(
                message.get("displayTitle") or message.get("toolName") or "working"
            )
            self.say("tool", title)
        elif kind in ("workflow-updated", "workflow-name-updated"):
            parsed = _workflow_from(message)
            if parsed is not None:
                # LAST one wins. An early `workflow-updated` can carry only the
                # generated name, and keeping the first would write a stub.
                self.candidate = parsed
        elif kind == "questions":
            rows = message.get("questions")
            self.pending = ("questions", [q for q in rows if isinstance(q, dict)]) if isinstance(
                rows, list
            ) else None
        elif kind == "plan":
            self.pending = ("plan", [message])
        elif kind == "web_fetch_approval":
            self.pending = ("web_fetch_approval", [message])
        elif kind == "error":
            # Arrives on HTTP 200. A relay watching only the status code would
            # report success on a build that failed.
            self.failure = str(message.get("message") or message.get("text") or "the builder failed")
        elif kind == "messages-compacted":
            self.transcript.clear()


def _workflow_from(message: dict[str, Any]) -> dict[str, Any] | None:
    raw = message.get("codeSnippet") or message.get("workflow")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def drive(
    jarvis: "Jarvis",
    task_id: str,
    instruction: str,
    *,
    builder: BuilderClient,
    node_types: list[dict[str, Any]] | None = None,
) -> RelayResult:
    """Run one build to its end, asking a person whenever the builder does.

    Returns rather than raising, because the caller is a fire-and-forget task
    and the outcome belongs on the task record either way.
    """
    tasks = getattr(jarvis, "tasks", None)
    registry = jarvis.data.get("llm_tools")
    talk = _Conversation()

    async def progress(**kwargs: Any) -> None:
        if tasks is not None:
            await tasks.async_update(task_id, **kwargs)

    text = instruction
    resume: Any = None
    result: RelayResult

    for turn in range(MAX_RESUMES + 1):
        talk.pending = None
        try:
            async for message in builder.build(
                text, resume_data=resume, node_types=node_types if turn == 0 else None
            ):
                talk.take(message)
                if talk.transcript and talk.transcript[-1]["role"] == "tool":
                    await progress(step=0, step_detail=talk.transcript[-1]["text"])
        except BuilderUnavailable as err:
            return RelayResult(False, str(err), transcript=talk.transcript)
        except BuilderError as err:
            return RelayResult(False, str(err), transcript=talk.transcript)

        if talk.failure:
            return RelayResult(False, talk.failure, transcript=talk.transcript)

        if talk.pending is None:
            break

        kind, questions = talk.pending
        if registry is None or not hasattr(registry, "hold_question"):
            return RelayResult(
                False,
                "n8n's builder asked a question and this server has no way to "
                "put it in front of anybody.",
                transcript=talk.transcript,
            )

        answers: list[Any] = []
        await progress(status=STATUS_BLOCKED, detail="waiting for you")
        for question in questions:
            asked = _question_text(kind, question)
            talk.say("builder", asked)
            said = await registry.hold_question(
                asked,
                choices=_options(question) or _default_choices(kind),
                ttl=QUESTION_TTL,
                # Always. The words were written by another AI and are about
                # to be drawn on a consent surface.
                tainted=True,
                context=None,
            )
            talk.say("you", said if said is not None else "(no answer)")
            answers.append(answer_for(kind, question, said))

        await progress(status=STATUS_RUNNING, detail="building")
        resume = _merge(kind, answers)
        # The text becomes conversation history on n8n's side, so it says
        # something rather than being decoration.
        text = "Answered." if kind == "questions" else "Continuing."
    else:
        return RelayResult(
            False,
            "n8n's builder kept asking without moving forward — it did not "
            "carry the conversation forward across a resume. Stopping rather "
            "than looping.",
            transcript=talk.transcript,
        )

    if talk.candidate is None:
        return RelayResult(
            False,
            "n8n's builder finished without producing a workflow.",
            transcript=talk.transcript,
        )

    from . import async_create

    await progress(add_steps=["write it to n8n"], step=1, step_status=STATUS_RUNNING)
    try:
        # The one write path. Rebuilt from four keys, so the builder's own
        # `active: true` and any credential it guessed do not survive.
        created, why = await async_create(jarvis, talk.candidate)
    except N8nError as err:
        return RelayResult(False, str(err), transcript=talk.transcript)
    if created is None:
        return RelayResult(
            False,
            f"n8n's builder produced a workflow Jarvis would not write: {why}",
            transcript=talk.transcript,
        )

    result = RelayResult(True, _one_line(created), workflow=created, transcript=talk.transcript)
    return result


def _merge(kind: str, answers: list[Any]) -> Any:
    """One `resumeData` for however many questions were asked."""
    if kind == "questions":
        rows: list[Any] = []
        for answer in answers:
            rows.extend(answer if isinstance(answer, list) else [answer])
        return rows
    return answers[0] if answers else {}


def _question_text(kind: str, question: dict[str, Any]) -> str:
    if kind == "plan":
        plan = str(question.get("text") or question.get("plan") or "")
        return f"n8n's builder proposes: {plan}"[:400] if plan else (
            "n8n's builder has a plan. Build it?"
        )
    if kind == "web_fetch_approval":
        url = str(question.get("url") or "somewhere")
        return f"n8n's builder wants to fetch {url}. Allow it?"
    return str(question.get("question") or question.get("text") or "n8n's builder asked something.")


def _default_choices(kind: str) -> list[str]:
    if kind == "plan":
        return ["Build it", "Change something", "Stop"]
    if kind == "web_fetch_approval":
        return ["Allow once", "Deny"]
    return []


def _one_line(created: dict[str, Any]) -> str:
    """What the console and the task record say. Never the transcript."""
    needed = created.get("connections_needed") or []
    said = f"Created {created.get('name')!r} ({created.get('nodes', '?')} nodes). It is switched OFF."
    if needed:
        asked = ", ".join(
            f"{item['credential_type']} for {item['node']!r}" for item in needed
        )
        said += f" Connect {asked} in n8n, then switch it on."
    else:
        said += " Review it in n8n and switch it on there."
    return said


def synthetic_workflow_id() -> str:
    """A stable thread key for a build that has no workflow yet.

    n8n derives its conversation thread from `workflowContext.currentWorkflow.id`.
    With no id it invents a UUID per request and the second POST lands in a
    thread that has never heard the question. This is unverified against a
    live instance — `MAX_RESUMES` is the guard for it being wrong.
    """
    return f"jarvis-{uuid.uuid4().hex[:12]}"


async def run_in_background(
    jarvis: "Jarvis",
    task: "Task",
    instruction: str,
    *,
    builder: BuilderClient,
    node_types: list[dict[str, Any]] | None = None,
) -> None:
    """`drive`, with the task record kept honest whatever happens."""
    tasks = jarvis.tasks
    try:
        result = await drive(
            jarvis, task.id, instruction, builder=builder, node_types=node_types
        )
    except asyncio.CancelledError:
        await tasks.async_update(task.id, status=STATUS_ERROR, error="stopped")
        raise
    except Exception as err:  # pragma: no cover - a crash is still an outcome
        _LOGGER.exception("n8n builder relay blew up")
        await tasks.async_update(task.id, status=STATUS_ERROR, error=str(err)[:400])
        return

    store = jarvis.data.get("n8n")
    if isinstance(store, dict):
        transcripts = store.setdefault("transcripts", {})
        transcripts[task.id] = result.transcript
        # Bounded: these are on the heap for the life of the process.
        for stale in list(transcripts)[:-20]:
            transcripts.pop(stale, None)

    if result.ok:
        await tasks.async_update(task.id, status=STATUS_DONE, result=result.summary)
    else:
        await tasks.async_update(task.id, status=STATUS_ERROR, error=result.summary)
