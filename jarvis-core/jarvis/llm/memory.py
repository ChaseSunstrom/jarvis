"""Bounded, in-memory conversation history.

A conversation is a short list of user/assistant turns kept for a while so
follow-ups ("and the other one?") make sense. It is deliberately bounded on
both axes — turns per conversation and conversations in total — because this
runs on a home server next to a 8B model, not a datacentre.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_TURNS = 20
DEFAULT_TTL = 900.0  # 15 minutes of silence ends a conversation
DEFAULT_MAX_CONVERSATIONS = 50


@dataclass(slots=True)
class Turn:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    #: Who the voice gate recognised saying a user turn (M100), "" for a typed
    #: or unverified one. Never on an assistant turn. What lets memory file a
    #: fact under the person who said it rather than under "the user".
    speaker: str = ""

    def as_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.speaker:
            out["speaker"] = self.speaker
        return out


@dataclass(slots=True)
class Conversation:
    id: str
    turns: list[Turn] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    max_turns: int = DEFAULT_MAX_TURNS

    def add(self, role: str, content: str, speaker: str = "") -> Turn:
        turn = Turn(role, content, speaker=str(speaker or "") if role == "user" else "")
        self.turns.append(turn)
        self.last_active = turn.timestamp
        if self.max_turns > 0 and len(self.turns) > self.max_turns:
            del self.turns[: len(self.turns) - self.max_turns]
        return turn

    def messages(self) -> list[dict[str, str]]:
        """History as chat messages, ready to splice under the system prompt."""
        return [turn.as_message() for turn in self.turns]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created": self.created,
            "last_active": self.last_active,
            "turns": [t.as_dict() for t in self.turns],
        }


class ConversationStore:
    """TTL + max-turns bounded store of conversations, keyed by id."""

    def __init__(
        self,
        max_turns: int = DEFAULT_MAX_TURNS,
        ttl: float = DEFAULT_TTL,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
    ) -> None:
        self.max_turns = max_turns
        self.ttl = ttl
        self.max_conversations = max_conversations
        self._conversations: dict[str, Conversation] = {}

    # --- reads ------------------------------------------------------------
    def get(self, conversation_id: str | None) -> Conversation | None:
        if not conversation_id:
            return None
        self.purge()
        return self._conversations.get(conversation_id)

    def messages(self, conversation_id: str | None) -> list[dict[str, str]]:
        conversation = self.get(conversation_id)
        return conversation.messages() if conversation else []

    def __len__(self) -> int:
        return len(self._conversations)

    def __contains__(self, conversation_id: object) -> bool:
        return isinstance(conversation_id, str) and conversation_id in self._conversations

    @property
    def ids(self) -> list[str]:
        return list(self._conversations)

    # --- writes -----------------------------------------------------------
    def get_or_create(self, conversation_id: str | None = None) -> Conversation:
        self.purge()
        if conversation_id:
            existing = self._conversations.get(conversation_id)
            if existing is not None:
                existing.last_active = time.time()
                return existing
        else:
            conversation_id = uuid.uuid4().hex
        conversation = Conversation(id=conversation_id, max_turns=self.max_turns)
        self._conversations[conversation_id] = conversation
        self._evict()
        return conversation

    def add(self, conversation_id: str | None, role: str, content: str) -> Conversation:
        conversation = self.get_or_create(conversation_id)
        conversation.add(role, content)
        return conversation

    def remove(self, conversation_id: str) -> bool:
        return self._conversations.pop(conversation_id, None) is not None

    def clear(self) -> None:
        self._conversations.clear()

    # --- housekeeping -----------------------------------------------------
    def purge(self, now: float | None = None) -> int:
        """Drop conversations that have been quiet for longer than the TTL."""
        if self.ttl <= 0:
            return 0
        cutoff = (time.time() if now is None else now) - self.ttl
        stale = [cid for cid, c in self._conversations.items() if c.last_active < cutoff]
        for cid in stale:
            del self._conversations[cid]
        return len(stale)

    def _evict(self) -> None:
        if self.max_conversations <= 0:
            return
        overflow = len(self._conversations) - self.max_conversations
        if overflow <= 0:
            return
        oldest = sorted(self._conversations.values(), key=lambda c: c.last_active)
        for conversation in oldest[:overflow]:
            self._conversations.pop(conversation.id, None)
