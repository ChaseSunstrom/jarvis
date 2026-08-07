"""P3 gate: the routing table test. Every row from the plan's §3b policy."""

import re
from pathlib import Path

import pytest

from routing import CHANNELS, Ctx, decide

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


def test_ha_script_mirrors_the_table():
    """jarvis_report must branch in the same priority order:
    driving → away → home+awake → default silent."""
    text = (
        Path(__file__).resolve().parents[1]
        / "ha-config/packages/jarvis/jarvis_context.yaml"
    ).read_text()
    block = text[text.index("jarvis_report") :]
    order = [
        m
        for m in re.findall(
            r"ctx\.driving|ctx\.location == 'away'|ctx\.location == 'home' and ctx\.awake",
            block,
        )
    ]
    assert order == [
        "ctx.driving",
        "ctx.location == 'away'",
        "ctx.location == 'home' and ctx.awake",
    ]
    assert "default" in block  # least-intrusive fallback exists


def test_prompt_states_the_same_rules():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "ha-config/prompts/jarvis_system_prompt.txt"
    ).read_text()
    for needle in (
        "driving: speak",
        "away + status/finished-task: send a text",
        "least intrusive",
    ):
        assert needle in prompt, f"prompt lost routing rule: {needle}"
