"""Tool calls a model wrote into its text, and how to get them back.

## The failure this exists for

A tool call is supposed to arrive in the structured field — `message.tool_calls`
on Ollama's wire, `delta.tool_calls` on the OpenAI-compatible one. Both parsers
read that field and nothing else, which is correct and was, for a long time,
the whole story.

It is not the whole story once you point Jarvis at your own model server. Qwen3,
Hermes, Mistral and Llama 3 all express a tool call as **text in a specific
format**, and turning that text into the structured field is the *serving
layer's* job:

    vLLM        --enable-auto-tool-choice --tool-call-parser hermes
    llama.cpp   --jinja  (plus a template that knows the format)
    Ollama      a model template with a `.ToolCalls` section

Miss that flag and the model does everything right, the server hands back
`tool_calls: null` with `<tool_call>{"name": ...}</tool_call>` sitting in the
content, and jarvis-core throws it away. What the user sees is an assistant
that says it will do something and then does not — with no error anywhere,
because from the server's point of view nothing failed.

Reported exactly that way: a 27B model, plenty capable, reasoning "let me call
list_code_repositories" and then producing nothing.

## Why parsing this is not a new trust boundary

An earlier version of this author's reasoning said recovering a call from text
would be "executing something a model wrote as prose, in a format nothing
validated" — and declined to do it. That argument does not survive contact:
`content` and `tool_calls` come from the *same model in the same response*. A
model that can put a name in one can put it in the other. Reading the second
field adds no capability the first did not already have.

What WOULD be a new surface is executing a name that is not a real tool, or
one the model was not offered this round. So the recovery is bounded by the
offered set — see `recover`, which takes it and refuses everything else. Text
in a web page that happens to look like a tool call still cannot name a tool
the turn was not already allowed to call.

## Formats

    Hermes / Qwen      <tool_call>{"name": "x", "arguments": {...}}</tool_call>
    Llama 3.1          <|python_tag|>{"name": "x", "parameters": {...}}
    Fenced JSON        ```json\\n{"name": "x", "arguments": {...}}\\n```
    Bare JSON          a lone {"name": ..., "arguments": ...} object

All four are tried; the first that yields a call for an offered tool wins. The
format is reported so the log can tell the operator which flag their server is
missing, which is the actual fix — this module is a safety net, not a
replacement for configuring the server properly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

__all__ = ["Recovered", "recover", "strip_tool_call_markup"]

#: `<tool_call> … </tool_call>`, tolerating whitespace and a missing close tag
#: at the end of a truncated stream.
_HERMES_RE = re.compile(
    r"<tool_call>\s*(?P<body>.*?)\s*(?:</tool_call>|$)", re.DOTALL | re.IGNORECASE
)
#: Llama 3.1's tag. No closing marker — the JSON object ends it.
_PYTHON_TAG = "<|python_tag|>"
#: A fenced block, optionally labelled json.
_FENCE_RE = re.compile(r"```(?:json|tool_call)?\s*(?P<body>\{.*?\})\s*```", re.DOTALL)

#: Everything this module knows how to strip out of visible text.
_MARKUP_RE = re.compile(
    r"<tool_call>.*?(?:</tool_call>|$)|<\|python_tag\|>|</?tool_call>",
    re.DOTALL | re.IGNORECASE,
)

MAX_SCAN_CHARS = 200_000
MAX_RECOVERED = 8


@dataclass
class Recovered:
    """What was found in the text, and where it came from."""

    #: `(name, arguments)` per call, in the order they appeared.
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: Which shape matched, for the log line that tells an operator what to fix.
    fmt: str = ""
    #: The text with the call markup taken out, so a surface can show whatever
    #: prose surrounded it without the machinery.
    text: str = ""

    def __bool__(self) -> bool:
        return bool(self.calls)


def _as_arguments(blob: Any) -> dict[str, Any] | None:
    """`arguments` in any of the spellings the formats use."""
    if not isinstance(blob, dict):
        return None
    for key in ("arguments", "parameters", "args", "input"):
        value = blob.get(key)
        if isinstance(value, dict):
            return value
        # Some servers double-encode the arguments as a JSON string.
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    # A call with no arguments is legitimate — `list_code_repositories` takes
    # none — so an object carrying only a name is a call with `{}`.
    return {}


def _named(blob: Any) -> str:
    if not isinstance(blob, dict):
        return ""
    for key in ("name", "tool", "function", "tool_name", "recipient_name"):
        value = blob.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # OpenAI nests it: {"function": {"name": ...}}
        if isinstance(value, dict):
            inner = value.get("name")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def _json_objects(text: str) -> Iterable[dict[str, Any]]:
    """Every top-level JSON object in `text`, brace-matched.

    A regex cannot do this — a nested object closes the outer one — so this
    walks the string tracking depth and string state. Bounded by
    `MAX_SCAN_CHARS` so a pathological reply cannot spin here.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text[:MAX_SCAN_CHARS]):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    yield parsed
                start = -1
            elif depth < 0:
                depth = 0


def _collect(text: str, offered: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """Calls for OFFERED tools out of a chunk of JSON-ish text."""
    found: list[tuple[str, dict[str, Any]]] = []
    for blob in _json_objects(text):
        name = _named(blob)
        if name not in offered:
            continue
        arguments = _as_arguments(blob)
        if arguments is None:
            continue
        found.append((name, arguments))
        if len(found) >= MAX_RECOVERED:
            break
    return found


def recover(content: str, thinking: str, offered: Iterable[str]) -> Recovered:
    """Pull tool calls out of a turn's text, bounded by what it was offered.

    `offered` is the decisive argument. Without it this would execute any
    JSON object in the reply that happens to carry a `name`, which is a real
    hazard once a turn has read a web page. With it, the worst a hostile page
    can do is name a tool the model was already allowed to call this round —
    which it could have called anyway.

    Reasoning is searched as well as content, and after it: a model that
    reasons about a call and then makes it properly must not have the
    reasoning's draft picked up in preference to the real one.
    """
    names = {str(name) for name in offered if str(name)}
    if not names:
        return Recovered(text=content)

    for label, chunk in (("content", content or ""), ("reasoning", thinking or "")):
        if not chunk.strip():
            continue

        # Hermes / Qwen first: it is the most explicit, so a match here is the
        # least ambiguous thing in the reply.
        bodies = [m.group("body") for m in _HERMES_RE.finditer(chunk)]
        calls = _collect("\n".join(bodies), names) if bodies else []
        if calls:
            return Recovered(calls, f"hermes:{label}", strip_tool_call_markup(content))

        if _PYTHON_TAG in chunk:
            calls = _collect(chunk.split(_PYTHON_TAG, 1)[1], names)
            if calls:
                return Recovered(
                    calls, f"python_tag:{label}", strip_tool_call_markup(content)
                )

        fenced = [m.group("body") for m in _FENCE_RE.finditer(chunk)]
        calls = _collect("\n".join(fenced), names) if fenced else []
        if calls:
            return Recovered(calls, f"fenced:{label}", strip_tool_call_markup(content))

        calls = _collect(chunk, names)
        if calls:
            # The bare form has no markup to strip, so the object itself is
            # taken out of the text a person sees — with the prose around it
            # kept, as for the tagged forms.
            return Recovered(
                calls, f"bare:{label}", strip_tool_call_markup(without_bare_calls(content, names))
            )

    return Recovered(text=content)


def without_bare_calls(text: str, offered: Iterable[str]) -> str:
    """`text` with every bare JSON object that calls an OFFERED tool cut out.

    The same brace walk `_json_objects` does, keeping the spans. Objects that
    name no offered tool stay: a reply that quotes some JSON is still prose.
    """
    names = {str(name) for name in offered if str(name)}
    if not text or not names:
        return text or ""
    pieces: list[str] = []
    kept_upto = 0
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text[:MAX_SCAN_CHARS]):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict) and _named(parsed) in names and _as_arguments(parsed) is not None:
                    pieces.append(text[kept_upto:start])
                    kept_upto = index + 1
                start = -1
            elif depth < 0:
                depth = 0
    pieces.append(text[kept_upto:])
    return "".join(pieces)


def strip_tool_call_markup(text: str) -> str:
    """Take the call machinery out of text meant for a human.

    A surface that showed `<tool_call>{"name": …}</tool_call>` would be reading
    the wire out loud. Only the markup goes; prose around it survives, because
    a model often says something useful in the same breath.
    """
    if not text:
        return ""
    return _MARKUP_RE.sub("", text).strip()


def toolcall_schema(offered: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """A JSON schema for exactly one call to one of the tools offered (M60).

    Used for the corrective retry after a model has *described* a call
    instead of making one — the failure `narrated_tool_call` catches, and the
    one a small model makes most. Handed to the server as `response_format`
    (llama.cpp turns it into a grammar; vLLM into guided decoding), it makes
    the reply a call by construction: a name from the list, arguments shaped
    by that tool's own parameters. It is not used on ordinary rounds, where
    the model must be free to answer in words.
    """
    branches: list[dict[str, Any]] = []
    for tool in offered:
        function = tool.get("function") if isinstance(tool, dict) else None
        spec = function if isinstance(function, dict) else tool
        name = str(spec.get("name") or "")
        if not name:
            continue
        params = spec.get("parameters")
        if not isinstance(params, dict) or params.get("type") != "object":
            params = {"type": "object"}
        branches.append({
            "type": "object",
            "properties": {"name": {"const": name}, "arguments": params},
            "required": ["name", "arguments"],
        })
    if not branches:
        return {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name", "arguments"]}
    return {"oneOf": branches} if len(branches) > 1 else branches[0]
