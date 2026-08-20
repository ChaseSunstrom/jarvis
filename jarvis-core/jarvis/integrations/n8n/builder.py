"""Talking to n8n's own AI workflow builder over its streaming endpoint.

## What this is

`POST /rest/ai/build` is what n8n's editor calls when somebody types "every
morning, email me yesterday's orders" into its AI panel. It answers with a
stream of JSON objects and it can INTERRUPT — stopping to ask a question,
propose a plan, or request permission to fetch a URL — and wait for an answer
before carrying on.

Jarvis can drive that conversation, which is worth doing because n8n's builder
knows n8n's node catalogue, its expression language and its own conventions
far better than a general model does.

## The wire format, precisely

Chunks are separated by a fixed multi-byte string, not by newlines:

    STREAM_SEPARATOR = '⧉⇋⇋➽⌑⧉§§\\n'

Two consequences that a naive reader gets wrong. First, a TCP chunk can split
that separator, and can split a UTF-8 codepoint inside it, so bytes have to go
through an INCREMENTAL decoder and the tail of a split has to be carried
forward. Second, **one chunk is not one message**: each parses to an object
with a `messages` array, and each element of that array has its own `type`.
There is no top-level `type` to switch on.

## What is verified and what is not

Verified by reading n8n 2.35.4: the separator, the licence gate in front of
the route, that `/rest` is cookie-authenticated and an API key cannot reach
it, and the interrupt payload shapes.

*Not* verified, because it needs a licensed instance to observe: that the HTTP
body ends promptly when the graph interrupts, and that a synthetic
`workflowContext.currentWorkflow.id` keys a thread that a later POST can
resume. Both are load-bearing, so neither is trusted: there is an idle timeout
rather than a wait for EOF, and the driver caps resumes rather than looping
when a resume silently fails to pair. `scripts/check-n8n.py --builder` is the
thing that settles them against a real box.

## An unknown chunk type is a no-op, never an error

n8n adds message types between versions, and some are emitted only by paths
Jarvis does not use. A relay that crashed on an unrecognised `type` would
break on an n8n upgrade for no reason at all.
"""

from __future__ import annotations

import codecs
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .capabilities import BUILDER_PATH, N8nCapabilities
from .session import N8nSession, SessionError

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "BuilderClient",
    "BuilderError",
    "BuilderUnavailable",
    "STREAM_SEPARATOR",
    "KNOWN_TYPES",
    "split_stream",
]

#: n8n's own constant, character for character. Do not "tidy" it.
STREAM_SEPARATOR = "⧉⇋⇋➽⌑⧉§§\n"

#: A stream that gets this far without a separator is not a stream Jarvis
#: should keep buffering. One megabyte is far past any real message.
MAX_STREAM_BUFFER = 1_048_576

#: Clipped client-side. The field becomes conversation history on n8n's side,
#: and a runaway prompt is a runaway bill on whatever model it is pointed at.
MAX_TEXT_CHARS = 5000

#: Everything this relay knows how to act on. Anything else is ignored — see
#: the module docstring. Kept as a set so `scripts/check-n8n.py` can diff a
#: real instance against it.
KNOWN_TYPES = frozenset(
    {
        "message",
        "tool",
        "workflow-updated",
        "workflow-name-updated",
        "questions",
        "plan",
        "web_fetch_approval",
        "error",
        "messages-compacted",
        "prompt-validation",
        "rate-limit",
    }
)

#: The interrupt types, i.e. the ones that stop the stream and need an answer.
INTERRUPTS = frozenset({"questions", "plan", "web_fetch_approval"})


class BuilderError(RuntimeError):
    """The builder failed, already phrased for a person."""


class BuilderUnavailable(BuilderError):
    """The builder is not on this instance at all. Distinct because the
    caller's answer is different: this one is "do it yourself instead"."""


def split_stream(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Complete objects out of a buffer, and the incomplete tail.

    Split out from the reader so the nasty case — a separator torn in half by
    a TCP boundary — is testable without a socket.
    """
    parts = buffer.split(STREAM_SEPARATOR)
    tail = parts.pop()  # always incomplete, even when it is ""
    out: list[dict[str, Any]] = []
    for part in parts:
        said = part.strip()
        if not said:
            continue
        try:
            parsed = json.loads(said)
        except ValueError:
            _LOGGER.debug("n8n builder sent something that is not JSON: %.120s", said)
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out, tail


def messages_of(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """The messages inside one chunk.

    One chunk is not one message, and there is no top-level `type` — the
    mistake that makes a relay silently drop half of what it is told.
    """
    raw = chunk.get("messages")
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    # Some paths send a bare message. Accepting it costs nothing and a version
    # that does this would otherwise look like total silence.
    return [chunk] if chunk.get("type") else []


class BuilderClient:
    """One conversation with n8n's builder.

    Holds the thread key, so a resume goes back to the same conversation
    rather than starting a fresh one that has never heard the question.
    """

    def __init__(
        self,
        session: N8nSession,
        *,
        capabilities: N8nCapabilities | None = None,
        workflow_id: str = "",
        idle_timeout: float = 90.0,
    ) -> None:
        self.session = session
        self.capabilities = capabilities
        #: n8n derives its thread key from this. It must be the SAME string on
        #: every POST of one conversation or the resume lands in a thread that
        #: has never heard the question.
        self.workflow_id = str(workflow_id or "")
        self.idle_timeout = float(idle_timeout)
        self._message_number = 0

    async def build(
        self,
        text: str,
        *,
        resume_data: Any = None,
        node_types: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send one turn and yield each message the builder streams back.

        `resume_data` carries the answer to whatever the last turn interrupted
        for. It must be an object or a list — n8n's DTO rejects a bare scalar
        at the boundary, which comes back as a validation error rather than as
        anything useful.
        """
        self._message_number += 1
        payload: dict[str, Any] = {
            "id": f"jarvis-{self._message_number}",
            "role": "user",
            "type": "message",
            "text": str(text or "")[:MAX_TEXT_CHARS],
            "workflowContext": {"currentWorkflow": {"id": self.workflow_id}},
        }
        if resume_data is not None:
            if not isinstance(resume_data, (dict, list)):
                raise BuilderError(
                    "resumeData has to be an object or a list; n8n rejects a "
                    "bare value at its DTO boundary."
                )
            payload["resumeData"] = resume_data
        if node_types:
            payload["workflowContext"]["nodeTypes"] = node_types

        async for message in self._stream(payload):
            yield message

    async def _stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        problem = self.session.why_not
        if problem:
            raise BuilderUnavailable(problem)

        if not self.session.has_token:
            try:
                await self.session.login()
            except SessionError as err:
                raise BuilderUnavailable(str(err)) from None

        target = f"{self.session.url}{self.session.rest_path}/{BUILDER_PATH}"
        headers = {
            **self.session._headers(with_cookie=True),
            "Content-Type": "application/json",
            "Accept": "application/json-lines, application/json",
        }
        # A read timeout rather than EOF is what ends a paused stream: whether
        # n8n closes the body at an interrupt is unverified, and a relay that
        # waited for a close that never comes would hang for the life of the
        # process.
        timeout = httpx.Timeout(self.session.timeout, read=self.idle_timeout)

        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        try:
            async with httpx.AsyncClient(
                timeout=timeout, transport=self.session._transport, cookies=None
            ) as client:
                async with client.stream(
                    "POST", target, json=payload, headers=headers
                ) as response:
                    refusal = await self._refusal(response)
                    if refusal is not None:
                        raise refusal
                    async for raw in response.aiter_bytes():
                        buffer += decoder.decode(raw)
                        if len(buffer) > MAX_STREAM_BUFFER:
                            raise BuilderError(
                                "n8n's builder sent more than a megabyte without "
                                "a message boundary. Stopping rather than "
                                "buffering the rest."
                            )
                        chunks, buffer = split_stream(buffer)
                        for chunk in chunks:
                            for message in messages_of(chunk):
                                yield message
        except httpx.ReadTimeout:
            # Not an error. This is the expected end of a paused conversation
            # if n8n holds the body open at an interrupt.
            _LOGGER.debug("n8n builder went quiet; treating it as the end of the turn")
        except httpx.TimeoutException:
            raise BuilderError(
                f"n8n at {self.session.url} did not answer the builder within "
                f"{self.session.timeout:.0f}s."
            ) from None
        except httpx.HTTPError as err:
            raise BuilderError(
                f"could not reach n8n's builder: {self.session.scrub(err)}"
            ) from None

        # Anything left in the buffer was never terminated. n8n's own client
        # tolerates this, so a final unterminated object is still a message.
        buffer += decoder.decode(b"", final=True)
        if buffer.strip():
            leftovers, _ = split_stream(buffer + STREAM_SEPARATOR)
            for chunk in leftovers:
                for message in messages_of(chunk):
                    yield message

    async def _refusal(self, response: httpx.Response) -> BuilderError | None:
        """Turn a refusing status into the right kind of error, or None.

        The 403 is the one that matters: it is the licence middleware, it is
        the only authority on whether the feature exists, and it is recorded
        so nothing asks again.
        """
        if response.status_code < 400:
            return None
        await response.aread()
        body = self.session.scrub(response.text)[:400]
        if self.capabilities is not None:
            self.capabilities.note_refusal(response.status_code, body)
        if response.status_code == 403:
            said = (
                self.capabilities.builder.detail
                if self.capabilities is not None
                else f"n8n refused the builder with 403: {body}"
            )
            return BuilderUnavailable(said)
        if response.status_code == 404:
            return BuilderUnavailable(
                "This n8n has no /rest/ai/build. It predates the AI builder."
            )
        if response.status_code == 401:
            return BuilderUnavailable(
                "n8n refused the session on the builder route. The login "
                "works but this account may not be allowed to use it."
            )
        return BuilderError(
            f"n8n answered {response.status_code} for the builder: {body}"
        )
