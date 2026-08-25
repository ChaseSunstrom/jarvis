"""What Jarvis actually DID with a request, in one word.

Routing accuracy is the number this repository trusts least when it is
self-reported, so it is never asked of the model: a turn was "routed to
memory" if and only if a memory tool ran. This module is the one definition of
that mapping, read by the scenario suite (`runner.py`) and by the intelligence
eval (`evals/intelligence/run.py`) — two callers, one table, because a second
copy of it would drift and both would keep printing percentages.
"""

from __future__ import annotations

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
    # Two coding paths, and both are "coding": `start_coding_job` is Jarvis's
    # own agent (M19), `code_task`/`apply_code_task` are the orchestrator's
    # delegation to a bigger model. A turn routed to either did the same thing
    # from the user's side of the room.
    "start_coding_job": "coding",
    "code_task": "coding",
    "apply_code_task": "coding",
    "run_background_task": "task",
    # A fan-out is its own capability, and it takes precedence below: a lead
    # that delegated three lookups to researchers did subagent work, and
    # scoring it as "research" would hide the thing that was under test.
    "delegate_to_agents": "subagents",
    # Asking how a job is going is about the job, not about starting one.
    "task_status": "task",
    "cancel_task": "task",
}


def capability_of(task_kinds: list[str], calls: list[str], tools: list[str],
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
    for capability in ("subagents", "coding", "research", "memory", "notes"):
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


