"""The response-routing table, as a pure function.

This is the single normative definition of the routing policy. Three places
mirror it and are kept honest by test_routing.py:

  * jarvis-core/jarvis/llm/tools.py — the `guidance` string `get_user_context`
    hands the model
  * jarvis-core/config/prompts/jarvis.txt — rule 4
  * evals/persona_prompts.yaml routing cases

The Home Assistant script that used to be the third mirror
(`ha-config/packages/jarvis/jarvis_context.yaml`) went with the rest of the
HA generation; see docs/removed.md.

Channels, most → least intrusive:
  speak            — TTS on the active audio device, nothing persisted
  announce_notify  — announce aloud at home AND leave a notification trail
  text             — normal mobile notification
  notify_silent    — low-priority silent notification (the safe default)
"""

from __future__ import annotations

from dataclasses import dataclass

CHANNELS = ("speak", "announce_notify", "text", "notify_silent")


@dataclass(frozen=True)
class Ctx:
    driving: bool = False
    location: str = "home"          # home | away
    awake: bool = True
    conversing: bool = False        # is a voice conversation active right now
    channel_requested: str = "voice"  # voice | text | none (unsolicited)
    kind: str = "reply"             # reply | status | task_done


def decide(ctx: Ctx) -> str:
    # 1. Driving: speak, keep it short, never notify (eyes stay on the road).
    if ctx.driving:
        return "speak"
    # 2. Away + status/finished-task: text, never announce to an empty house.
    if ctx.location == "away" and ctx.kind in ("status", "task_done"):
        return "text"
    # 3. Asked by text → answer by text.
    if ctx.channel_requested == "text":
        return "text"
    # 4. Long task finished outside an active conversation:
    #    announce aloud only if home and awake, always leave a trail.
    if ctx.kind == "task_done" and not ctx.conversing:
        if ctx.location == "home" and ctx.awake:
            return "announce_notify"
        return "notify_silent"
    # 5. Active voice conversation → speak.
    if ctx.conversing and ctx.channel_requested == "voice":
        return "speak"
    # 6. Unsure → least intrusive.
    return "notify_silent"
