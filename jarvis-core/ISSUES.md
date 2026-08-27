
## A false narrated-call alarm derailed a turn

severity: major
status: **fixed** (`jarvis-core/jarvis/llm/agent.py`, `narrated_tool_call`)
Regression: `task-background-plan`
Test: `jarvis-core/tests/test_narrated_tool_calls.py::test_a_turn_that_lists_its_tools_is_not_narrating_a_call`

Asked to go through every sensor in the house and write it up, Jarvis answered:

> "I thought about that but didn't manage to put an answer into words, Sir.
> Would you ask me again?"

and started no task. The log said the model had "described calling
`activate_scene` without calling it".

It had not. The detector required a call cue *anywhere* in the turn and a
registered tool name *anywhere* in the turn, and the model had written a
paragraph containing the word "call" and, further down, a list of what it could
do. The nudge told it to make a call it had never described; the corrected
round produced no text at all; the user got the canned apology instead of their
work.

Three rules now separate "this text scripts a call" from "this text mentions a
tool": the name must be written as a call (`name(`) or sit within 60 characters
of the cue; a turn naming several tools is enumerating its toolbox; and a cue
preceded by a modal ("I *can* call on `get_state`") is an offer, not a claim.

## The model narrates internal steps out loud

severity: minor
status: **open**
Regression: `house-garbled`

Replies sometimes report machinery the user did not ask about:

> "My apologies, sir — I've now read the house style, and I confess I still
> don't follow what you'd like me to do."

Reading a skill is an internal step. The preamble fix removes this whenever the
round that said it went on to call a tool, which is most of the time; here the
final round says it, so nothing catches it. It is a persona/prompt problem
rather than a wiring one — the system prompt tells the model to report outcomes
rather than services, and this is the same instruction being missed — and the
scenario now judges what matters (it must not claim to have acted on the house)
rather than the wording.
