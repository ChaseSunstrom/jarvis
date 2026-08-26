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
    # The sky (M58): four read-only questions answered from cached orbital
    # elements and an ephemeris. One capability, because a turn that asked
    # "what's up tonight" and got the moon AND the planets did one thing.
    # Cameras (M56): a look is vision, whichever camera and whichever wire.
    "look_at_camera": "vision",
    "describe_camera_change": "vision",
    "list_cameras": "vision",
    "next_pass": "sky",
    "overhead_now": "sky",
    "moon_phase": "sky",
    "planets_tonight": "sky",
    # Any sensor (M57): a reading looked up, compared, summarised or read
    # over a window. A reading the model answered from the house state in
    # its context is "answer" — correct, and not this capability, which is
    # about the tools a snapshot cannot stand in for (history, comparison).
    "sensor_readings": "sensors",
    "sensor_compare": "sensors",
    "sensor_history": "sensors",
    "sensor_summary": "sensors",
    # Anything online, locally (M59): watching a page or a feed, asking a
    # question of the web until it is yes, reading a page. Not "research":
    # research answers now; these come back later.
    "watch_page": "online",
    "watch_feed": "online",
    "watch_for": "online",
    "list_watches": "online",
    "cancel_watch": "online",
    "read_page": "online",
    "feed_latest": "online",
}


def capability_of(task_kinds: list[str], calls: list[str], tools: list[str],
                  reply: str) -> str:
    """What Jarvis actually did with the request, in one word.

    Read off the consequences rather than asked of the model: routing accuracy
    that a model self-reports is a model grading its own homework. Ordered by
    specificity — a coding job that also called `get_state` is still coding.
    """
    # Delegation FIRST, and before the task kinds. A lead that fanned work out
    # across backends starts research and coding children, so reading the child
    # kinds first labels a fan-out as whatever it delegated — which hides the
    # thing that actually happened. Same principle as "a coding job that also
    # called get_state is still coding": the outer act is the answer.
    if "delegate_to_agents" in tools or "delegation" in task_kinds:
        return "subagents"
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
    # A job started is the outer act, as a coding job that also called
    # get_state is still coding: "audit every sensor in the background" that
    # called run_background_task AND read a sensor did one thing, and it was
    # the task. The readers (M56–M59) come after it for that reason.
    if task_kinds or "run_background_task" in tools:
        return "task"
    for capability in ("sky", "vision", "sensors", "online"):
        if capability in chosen:
            return capability
    # Only calls that moved something in the HOUSE count as house control. Any
    # service call at all was too crude: a turn that read a skill and answered
    # was routed to "house" because something incidental had gone through the
    # service layer.
    if any(call.split(".", 1)[0] in HOUSE_DOMAINS for call in calls):
        return "house"
    if "use_skill" in tools:
        return "skills"
    return "answer"


