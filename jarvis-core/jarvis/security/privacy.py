"""What may leave this network, and what may not.

`llm: local_only:` already refuses a model server that is not yours, at
startup. That is an answer to "where does the model live". This answers a
different question, per request: **does this particular prompt contain things
that must not leave the house, whatever server is configured?**

The two are not the same. An operator who has deliberately added a cloud
provider for a coding job has answered "yes, some traffic may leave" — and that
must not silently include the turn that had their memory block in it.

## The rule

A request is `local-only` when its prompt carries any of:

* **memory** — the remembered-notes block, which is in every conversational
  system prompt and contains standing facts about a person;
* **notes** — a document from the note store;
* **private-integration content** — a calendar entry, an email body, a message,
  a camera description, a file from the house.

Such a request may only be routed to a local model. Not "should" — the gateway
refuses it, and the refusal is an error the caller sees rather than a silent
downgrade, because a silent downgrade to a weaker local model is a decision
nobody made and nobody can audit.

Leaving with personal data takes an explicit per-request opt-in
(`privacy="allow-cloud"`), and that decision is logged with what triggered it.

## Why the marker travels in metadata, not in the prompt

A model cannot be asked to keep a secret. If "local-only" were a sentence in
the prompt, an injected page could argue with it — and the whole of `M43` is
about not putting security decisions where text can reach them. It is a header
and a metadata field: `x-jarvis-privacy: local-only`, enforced by the proxy
before a token is generated.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

#: The tag itself, on the wire.
HEADER = "x-jarvis-privacy"
LOCAL_ONLY = "local-only"
ALLOW_CLOUD = "allow-cloud"

#: Markers that a prompt carries something private. These are the block headers
#: the prompt builder itself writes, so this is a fact about the message rather
#: than a guess about its meaning — `memory/__init__.py` and the fencing in
#: `security/quarantine.py` produce them.
PRIVATE_MARKERS = (
    "facts to use, never instructions",   # the memory block's own heading
    "<untrusted_content>",                # anything quarantined: pages, mail, messages
    "<untrusted_web_content>",            # jarvis-browser's older fence, still in flight
    "what jarvis remembers",              # the memory export heading
)

#: Tools whose RESULTS are private by definition. A turn that called one of
#: these has private content in its history whether or not the marker survived
#: paraphrase — which is the case the markers alone would miss.
PRIVATE_TOOLS = frozenset({
    "recall", "note_search", "note_create", "note_append", "remember",
    "read_file", "search_files", "list_files",
    "look_at_camera", "describe_camera_change",
    "get_briefing", "recent_events",
    "calendar_list", "calendar_create", "mail_read", "mail_send",
})

#: A model id that is not local. Checked by NAME rather than by URL because the
#: gateway resolves names to providers, and by the time a URL exists the
#: decision has been made.
CLOUD_PREFIXES = (
    "openai/", "anthropic/", "gemini/", "vertex_ai/", "azure/", "bedrock/",
    "openrouter/", "groq/", "mistral/", "cohere/", "deepseek/", "xai/",
    "together_ai/", "fireworks_ai/", "perplexity/",
)

_MARKER_RE = re.compile("|".join(re.escape(m) for m in PRIVATE_MARKERS), re.IGNORECASE)


def is_cloud_model(model: str) -> bool:
    """True when this model id names a provider outside the house."""
    name = str(model or "").strip().lower()
    return any(name.startswith(prefix) for prefix in CLOUD_PREFIXES)


def carries_private_content(
    messages: Iterable[Any] | None = None, tools_used: Iterable[str] | None = None
) -> tuple[bool, str]:
    """(is it private, why). The "why" is logged and shown; it names nothing.

    Deliberately returns the CATEGORY rather than the content: "the memory
    block" is enough for an operator to understand a refusal, and putting the
    remembered fact itself in a log would be the leak this is preventing.
    """
    for name in tools_used or ():
        if str(name) in PRIVATE_TOOLS:
            return True, f"this turn called {name}, whose results are private"
    for message in messages or ():
        content = message.get("content") if isinstance(message, dict) else getattr(
            message, "content", ""
        )
        if not isinstance(content, str):
            continue
        found = _MARKER_RE.search(content)
        if found:
            return True, f"the prompt carries {found.group(0)[:40]!r}"
    return False, ""


def classify(
    messages: Iterable[Any] | None = None,
    tools_used: Iterable[str] | None = None,
    override: str = "",
) -> tuple[str, str]:
    """The tag for this request, and the reason. `override` is the opt-in.

    An override is honoured and LOGGED: leaving the network with personal data
    is a decision, and a decision nobody can find afterwards is indistinguishable
    from an accident.
    """
    private, why = carries_private_content(messages, tools_used)
    if override == ALLOW_CLOUD:
        if private:
            _LOGGER.warning(
                "Privacy: a request carrying private content is being allowed to "
                "leave the network by explicit opt-in (%s)", why,
            )
        return ALLOW_CLOUD, why
    if private:
        return LOCAL_ONLY, why
    return "", ""


def refuse(model: str, tag: str) -> str:
    """"" if this pairing is allowed, else the refusal to show the caller."""
    if tag == LOCAL_ONLY and is_cloud_model(model):
        return (
            f"refused: this request is tagged {LOCAL_ONLY} because it carries "
            f"private content, and {model!r} is not a local model. Route it to a "
            "local model, or opt in per request with "
            f"privacy={ALLOW_CLOUD!r} if you really mean to send it off this network."
        )
    return ""
