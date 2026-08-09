"""Companion: Jarvis reaching out to YOU, across devices.

Everything else in the system is request/response — you speak, Jarvis answers.
This integration is the other direction: something happens (a build fails on
the desktop, the washing machine finishes, an automation needs a decision) and
Jarvis finds you on whichever device you are actually at and says so — or asks
you a question and waits for the answer.

Services (all usable from automations, scripts and as LLM tools):
  companion.notify        say/notify something; fire-and-forget
  companion.ask           ask a question and RETURN the answer (blocking,
                          supports_response) — an automation can branch on it
  companion.presence      current per-device presence + the routing decision
  companion.set_muted     quiet a device
  companion.handoff       move an in-flight conversation to another device

Delivery is decided by jarvis.presence (driving -> speak, question -> a device
you can answer on, nothing reachable -> queue until one returns), with
escalation through the fallback devices when the first does not respond.

The transport is injected: this module never touches websockets. The API layer
registers a sender via `set_transport()`, which keeps the routing logic
testable and lets satellites/desktop/web all plug in the same way.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ...presence import (  # noqa: F401  (re-exported for convenience)
    NEEDS_ANSWER,
    NEEDS_SPEECH,
    NEEDS_VISUAL,
    Delivery,
    PresenceRegistry,
)

DOMAIN = "companion"
_LOGGER = logging.getLogger(__name__)

EVENT_MESSAGE_SENT = "companion_message_sent"
EVENT_MESSAGE_ANSWERED = "companion_message_answered"
EVENT_MESSAGE_EXPIRED = "companion_message_expired"

DEFAULT_ASK_TIMEOUT = 120.0
DEFAULT_NOTIFY_TIMEOUT = 30.0
MAX_QUEUE = 50

#: The three things a proactive message can be. Anything else is a `notify`:
#: `kind` chooses the presence need, the wire `kind`, and whether `send()`
#: blocks, so an unrecognised one must land on the quietest, non-blocking
#: option rather than being passed through to the devices verbatim.
VALID_KINDS = ("say", "ask", "notify")

# transport(device_id, payload) -> awaitable[bool delivered]
Transport = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class PendingMessage:
    message_id: str
    kind: str                    # say | ask | notify
    text: str
    options: list[str] = field(default_factory=list)
    conversation_id: str | None = None
    importance: str = "normal"
    created: float = field(default_factory=time.time)
    timeout: float = DEFAULT_ASK_TIMEOUT
    targets_tried: list[str] = field(default_factory=list)
    future: asyncio.Future | None = None

    def payload(self, mode: str) -> dict[str, Any]:
        return {
            "type": "jarvis_message",
            "message_id": self.message_id,
            "kind": self.kind,
            "mode": mode,
            "text": self.text,
            "options": list(self.options),
            "conversation_id": self.conversation_id,
            "importance": self.importance,
            "timeout_s": self.timeout,
        }


class CompanionManager:
    def __init__(self, jarvis: Any, presence: PresenceRegistry) -> None:
        self.jarvis = jarvis
        self.presence = presence
        self._transport: Transport | None = None
        self._pending: dict[str, PendingMessage] = {}
        self._queue: list[PendingMessage] = []

    # --- wiring -----------------------------------------------------------
    def set_transport(self, transport: Transport | None) -> None:
        """Called by the API layer once it can push to connected devices."""
        self._transport = transport
        if transport is not None:
            self.jarvis.async_create_task(self._drain_queue())

    # --- inbound ----------------------------------------------------------
    def on_device_answer(
        self, message_id: str, answer: str | None, status: str = "answered"
    ) -> bool:
        """A device reported back. Resolves the waiting `ask`."""
        message = self._pending.get(message_id)
        if message is None:
            return False
        if (
            message.kind == "ask"
            and status == "answered"
            and not str(answer or "").strip()
        ):
            # A question is answered by a person choosing something. An
            # `answered` carrying nothing is a device acknowledging *delivery*
            # — every device reports that for a plain notification — and
            # reading it as an answer would be the worst of both worlds: the
            # waiting `companion.ask` resolves with a reply nobody gave, and
            # escalation stops, so the question never reaches the human at all.
            # Downgrade to `dismissed`, which is what "not dealt with here"
            # means everywhere else in this protocol.
            _LOGGER.warning(
                "companion: %s reported 'answered' with no answer; treating it "
                "as dismissed so the question still reaches a human",
                message_id,
            )
            status = "dismissed"
        if status == "answered" and message.future and not message.future.done():
            message.future.set_result(answer)
            self._pending.pop(message_id, None)
            self.jarvis.bus.fire(
                EVENT_MESSAGE_ANSWERED,
                {"message_id": message_id, "answer": answer,
                 "conversation_id": message.conversation_id},
            )
            return True
        if status in ("dismissed", "timeout", "undeliverable"):
            # Let escalation try the next device rather than failing outright.
            return self._escalate(message, status)
        return False

    # --- outbound ---------------------------------------------------------
    async def send(
        self,
        text: str,
        kind: str = "notify",
        options: list[str] | None = None,
        importance: str = "normal",
        device_id: str | None = None,
        conversation_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        kind = kind if kind in VALID_KINDS else "notify"
        need = (
            NEEDS_ANSWER if kind == "ask"
            else NEEDS_SPEECH if kind == "say"
            else NEEDS_VISUAL
        )
        message = PendingMessage(
            message_id=uuid.uuid4().hex[:12],
            kind=kind,
            text=text,
            options=list(options or []),
            conversation_id=conversation_id,
            importance=importance,
            timeout=timeout or (DEFAULT_ASK_TIMEOUT if kind == "ask" else DEFAULT_NOTIFY_TIMEOUT),
        )
        delivery = self.presence.route(need, importance, device_id)

        if delivery.device_id is None or self._transport is None:
            self._enqueue(message)
            return {
                "status": "queued",
                "message_id": message.message_id,
                "reason": delivery.reason if self._transport else "no transport",
            }

        if kind == "ask":
            message.future = asyncio.get_running_loop().create_future()
        self._pending[message.message_id] = message

        delivered = await self._deliver(message, delivery)
        if not delivered:
            self._pending.pop(message.message_id, None)
            self._enqueue(message)
            return {"status": "queued", "message_id": message.message_id,
                    "reason": "delivery failed"}

        result: dict[str, Any] = {
            "status": "delivered",
            "message_id": message.message_id,
            "device_id": delivery.device_id,
            "mode": delivery.mode,
            "reason": delivery.reason,
        }
        if kind != "ask":
            self._pending.pop(message.message_id, None)
            return result

        # Blocking ask: wait for the answer, escalating through fallbacks.
        # NOTE: shield the future — wait_for cancels what it waits on, which
        # would poison the escalation retry (and any later answer) with a
        # CancelledError instead of just timing out.
        try:
            answer = await asyncio.wait_for(
                asyncio.shield(message.future), message.timeout
            )
            result.update(status="answered", answer=answer)
        except asyncio.TimeoutError:
            if self._escalate(message, "timeout"):
                try:
                    answer = await asyncio.wait_for(
                        asyncio.shield(message.future), message.timeout
                    )
                    result.update(status="answered", answer=answer)
                    return result
                except asyncio.TimeoutError:
                    pass
            self._pending.pop(message.message_id, None)
            if not message.future.done():
                message.future.cancel()
            self.jarvis.bus.fire(EVENT_MESSAGE_EXPIRED, {"message_id": message.message_id})
            result.update(status="timeout", answer=None)
        return result

    async def _deliver(self, message: PendingMessage, delivery: Delivery) -> bool:
        assert delivery.device_id is not None
        message.targets_tried.append(delivery.device_id)
        try:
            ok = await self._transport(delivery.device_id, message.payload(delivery.mode))  # type: ignore[misc]
        except Exception:
            _LOGGER.exception("companion transport failed for %s", delivery.device_id)
            return False
        if ok:
            self.jarvis.bus.fire(
                EVENT_MESSAGE_SENT,
                {"message_id": message.message_id, "device_id": delivery.device_id,
                 "mode": delivery.mode, "kind": message.kind},
            )
        return bool(ok)

    def _escalate(self, message: PendingMessage, why: str) -> bool:
        """Try the next-best device that hasn't been tried."""
        need = NEEDS_ANSWER if message.kind == "ask" else NEEDS_VISUAL
        for device in self.presence.rank(need):
            if device.device_id in message.targets_tried:
                continue
            delivery = Delivery(device.device_id, "ask" if message.kind == "ask" else "notify",
                                f"escalated after {why}")
            self.jarvis.async_create_task(self._deliver(message, delivery))
            return True
        return False

    def _enqueue(self, message: PendingMessage) -> None:
        self._queue.append(message)
        del self._queue[:-MAX_QUEUE]  # keep the newest

    async def _drain_queue(self) -> None:
        """Deliver anything that piled up while no device was reachable."""
        pending, self._queue = list(self._queue), []
        for message in pending:
            if message.kind == "ask" and (
                message.future is None or message.future.done()
            ):
                # A question that was queued never got a future — `send()`
                # returned `queued` and the automation moved on. Putting it on
                # a screen now asks the user something whose answer has nowhere
                # to go: they choose, and nothing happens. Drop it and say so.
                _LOGGER.info(
                    "companion: dropping queued question %s; nobody is waiting "
                    "for the answer any more",
                    message.message_id,
                )
                self.jarvis.bus.fire(
                    EVENT_MESSAGE_EXPIRED,
                    {"message_id": message.message_id, "reason": "nobody waiting"},
                )
                continue
            need = NEEDS_ANSWER if message.kind == "ask" else NEEDS_VISUAL
            delivery = self.presence.route(need, message.importance)
            if delivery.device_id is None or self._transport is None:
                self._queue.append(message)
                continue
            await self._deliver(message, delivery)

    @property
    def queued(self) -> int:
        return len(self._queue)


async def async_setup(jarvis: Any, config: Any) -> bool:
    presence: PresenceRegistry = jarvis.data.setdefault("presence", PresenceRegistry())
    manager = CompanionManager(jarvis, presence)
    jarvis.data["companion"] = manager

    async def notify(call: Any) -> dict[str, Any]:
        # `notify` is the fire-and-forget door. `kind: ask` through here would
        # make it block for the whole ask timeout and return an answer the
        # caller never asked for — `companion.ask` is the service for that, and
        # it is the one that creates a future to receive the reply.
        kind = call.get("kind", "notify")
        return await manager.send(
            text=call.get("message", ""),
            kind=kind if kind in ("say", "notify") else "notify",
            importance=call.get("importance", "normal"),
            device_id=call.get("device_id"),
            conversation_id=call.get("conversation_id"),
        )

    async def ask(call: Any) -> dict[str, Any]:
        return await manager.send(
            text=call.get("question", ""),
            kind="ask",
            options=call.get("options") or [],
            importance=call.get("importance", "normal"),
            device_id=call.get("device_id"),
            conversation_id=call.get("conversation_id"),
            timeout=float(call.get("timeout", DEFAULT_ASK_TIMEOUT)),
        )

    async def presence_report(call: Any) -> dict[str, Any]:
        need = call.get("need", NEEDS_VISUAL)
        return {
            "devices": [d.as_dict() for d in presence.all()],
            "route": presence.route(need).as_dict(),
            "queued": manager.queued,
        }

    async def set_muted(call: Any) -> dict[str, Any]:
        device_id = call.get("device_id")
        muted = bool(call.get("muted", True))
        updated = presence.update(device_id, muted=muted)
        return {"ok": updated is not None, "device_id": device_id, "muted": muted}

    async def handoff(call: Any) -> dict[str, Any]:
        """Continue an existing conversation on another device."""
        return await manager.send(
            text=call.get("message", "Continuing here, Sir."),
            kind=call.get("kind", "say"),
            device_id=call.get("device_id"),
            conversation_id=call.get("conversation_id"),
            importance="normal",
        )

    jarvis.services.register(
        DOMAIN, "notify", notify, supports_response=True,
        description="Tell the user something on whichever device they are at.",
        fields={
            "message": {"description": "what to say", "required": True},
            "kind": {"description": "say (aloud) | notify (quiet)", "required": False},
            "importance": {"description": "low | normal | high | critical", "required": False},
            "device_id": {"description": "force a specific device", "required": False},
        },
    )
    jarvis.services.register(
        DOMAIN, "ask", ask, supports_response=True,
        description=(
            "Ask the user a question on whichever device they are at and WAIT for "
            "the answer. Returns {status, answer}."
        ),
        fields={
            "question": {"description": "the question", "required": True},
            "options": {"description": "optional list of allowed answers", "required": False},
            "timeout": {"description": "seconds to wait (default 120)", "required": False},
            "device_id": {"description": "force a specific device", "required": False},
        },
    )
    jarvis.services.register(
        DOMAIN, "presence", presence_report, supports_response=True,
        description="Where is the user, and where would a message land right now?",
    )
    jarvis.services.register(DOMAIN, "set_muted", set_muted, supports_response=True)
    jarvis.services.register(DOMAIN, "handoff", handoff, supports_response=True)

    _LOGGER.info("companion ready: cross-device notify/ask/handoff")
    return True
