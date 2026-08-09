"""The routing table test: every row of the policy, plus its two mirrors.

`routing.py` is the normative definition. What actually reaches the model at
runtime is (a) the `guidance` string `get_user_context` returns and (b) rule 4
of the shipped persona prompt. Those two drift silently — nothing crashes when
a prompt loses a rule — so they are asserted here against the same table.
"""

from pathlib import Path

import pytest

from routing import CHANNELS, Ctx, decide

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "jarvis-core"

CASES = [
    # driving beats everything
    (Ctx(driving=True, kind="task_done", location="away"), "speak"),
    (Ctx(driving=True, channel_requested="text"), "speak"),
    (Ctx(driving=True, conversing=True), "speak"),
    # away + status/task_done → text
    (Ctx(location="away", kind="status"), "text"),
    (Ctx(location="away", kind="task_done"), "text"),
    (Ctx(location="away", kind="task_done", awake=False), "text"),
    # text in → text out
    (Ctx(channel_requested="text", kind="reply"), "text"),
    (Ctx(channel_requested="text", kind="reply", location="away"), "text"),
    # task done, no active conversation
    (Ctx(kind="task_done", conversing=False, location="home", awake=True),
     "announce_notify"),
    (Ctx(kind="task_done", conversing=False, location="home", awake=False),
     "notify_silent"),
    # live voice conversation → speak
    (Ctx(kind="reply", conversing=True, channel_requested="voice"), "speak"),
    (Ctx(kind="task_done", conversing=True, channel_requested="voice"), "speak"),
    # unsure / unsolicited → least intrusive
    (Ctx(kind="status", channel_requested="none"), "notify_silent"),
    (Ctx(kind="reply", conversing=False, channel_requested="none"),
     "notify_silent"),
]


@pytest.mark.parametrize("ctx,expected", CASES)
def test_routing_table(ctx, expected):
    assert decide(ctx) == expected


def test_all_outputs_are_known_channels():
    for ctx, expected in CASES:
        assert expected in CHANNELS


def test_the_tool_guidance_mirrors_the_table():
    """`get_user_context` is what the model consults mid-turn.

    It returns a one-line `guidance` string. If that loses a rule the table
    still passes and the running assistant still gets it wrong, so the string
    itself is asserted — in the same priority order as `decide`.
    """
    source = (CORE / "jarvis/llm/tools.py").read_text()
    start = source.index('"guidance"')
    guidance = source[start : source.index("}", start)]

    for needle in ("driving: speak", "away: notify by text", "least intrusive"):
        assert needle in guidance, f"get_user_context lost routing rule: {needle}"
    # Priority order matters as much as the rules: driving beats away, and the
    # least-intrusive fallback is last.
    assert (
        guidance.index("driving: speak")
        < guidance.index("away: notify by text")
        < guidance.index("least intrusive")
    ), "the guidance no longer states the rules in priority order"


def test_prompt_states_the_same_rules():
    """Rule 4 of the shipped persona, which is loaded by `llm: persona_file:`."""
    prompt = (CORE / "config/prompts/jarvis.txt").read_text()
    for needle in (
        "driving: speak",
        "send a text",
        "least intrusive",
    ):
        assert needle in prompt, f"prompt lost routing rule: {needle}"
    # The away branch must stay tied to status/finished-task work, not to
    # every reply — otherwise an answered question goes to the phone.
    assert "status update or a finished task" in prompt
    assert "announce" in prompt
