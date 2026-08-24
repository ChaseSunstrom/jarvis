"""Durable conversation history — what the chat console lists and reopens.

`memory.ConversationStore` is the model's short-term memory: bounded turns, a
fifteen-minute TTL, gone when the process restarts. That is the right shape for
context, and the wrong shape for a person: "what did I ask it on Tuesday" is
not a question a fifteen-minute window can answer.

This is the other half. Every finished turn is appended here as well, the file
lands under ``<config>/.storage/conversations.json``, and the console's chat
mode reads it. The two stores answer different questions and neither is
derivable from the other:

* :class:`~jarvis.llm.memory.ConversationStore` — *what the model is told*.
  Small, hot, TTL'd, and never persisted.
* :class:`ConversationArchive` — *what the user can read*. Large, cold,
  durable, and never sent anywhere near a prompt except when a person
  explicitly reopens a conversation (see
  :meth:`~jarvis.llm.agent.ConversationAgent.resume`).

## What a turn keeps

Text, and the two things a chat surface renders around it: the tool calls the
turn made, and the reasoning it did. Both are already broadcast live over the
bus to any subscriber — this only means reopening a conversation shows the same
thing scrolling past it did, rather than a bare paragraph with the interesting
part missing.

## Bounds

Three of them, because this file grows for as long as the house runs and
nothing else prunes it: turns per conversation, conversations in total, and
characters per stored field. A tool argument is a value the *model* chose the
size of, so the last one is not optional — see `MAX_FIELD_CHARS`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store

_LOGGER = logging.getLogger(__name__)

#: Conversations kept on disk. Fifty turns each at the cap below is a few MB —
#: small enough to load synchronously at boot, large enough that nobody who
#: talks to their house daily loses a week.
DEFAULT_MAX_CONVERSATIONS = 200

#: Turns kept per conversation. Well past `ConversationStore`'s 20-turn context
#: window, because this one is read by a person scrolling back.
DEFAULT_MAX_TURNS = 200

#: The cap on any one stored string. Applies to message text, reasoning, tool
#: arguments and tool errors alike — every one of those is either model-written
#: or tool-written, and neither has a reason to respect a budget it cannot see.
MAX_FIELD_CHARS = 8000

#: The cap on a conversation's title, which is a UI row and not prose.
MAX_TITLE_CHARS = 80

#: Tool calls recorded per turn. A turn that made forty calls is a turn whose
#: transcript nobody reads; the first dozen say what it was doing.
MAX_TOOL_CALLS = 12


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    """One stored string, bounded and stripped of the control characters that
    would let it impersonate structure in a renderer."""
    text = "" if value is None else str(value)
    if not text:
        return ""
    # Tabs and newlines are meaningful in a chat transcript and are kept; the
    # rest of the C0 range is not, and a stray \x1b in a terminal-styled console
    # is an escape sequence rather than a character.
    text = "".join(ch for ch in text if ch in "\n\t" or ch >= " ")
    if len(text) > limit:
        return f"{text[: limit - 1]}…"
    return text


def _excerpt(text: str, needle: str, width: int = 160) -> str:
    """The matched line, with enough either side to recognise it."""
    where = text.lower().find(needle)
    if where < 0:
        return _clip(text, width)
    start = max(0, where - width // 3)
    piece = text[start : start + width].strip()
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


def _title_from(text: str) -> str:
    """A conversation's name, taken from the first thing that was said in it."""
    line = " ".join(str(text or "").split())
    if not line:
        return "New conversation"
    if len(line) > MAX_TITLE_CHARS:
        return f"{line[: MAX_TITLE_CHARS - 1]}…"
    return line


def summarise_tool_call(call: Any) -> dict[str, Any]:
    """One entry of `ConversationResult.tool_calls`, small enough to store.

    The *result* is deliberately reduced to its status. A tool result is the
    only field here that can hold something the user never chose to keep — a
    document body, a page of scraped text, the contents of a camera frame — and
    a transcript is exactly the wrong place for it to accumulate. What a person
    scrolling back wants is which tools ran and whether they worked; that is
    what this keeps.
    """
    if not isinstance(call, dict):
        return {"name": _clip(call, 120), "arguments": {}, "ok": True}
    result = call.get("result")
    status = result.get("status") if isinstance(result, dict) else None
    error = result.get("error") if isinstance(result, dict) else None
    arguments = call.get("arguments")
    return {
        "name": _clip(call.get("name"), 120) or "tool",
        "arguments": {
            _clip(key, 60): _clip(value, 300)
            for key, value in list((arguments or {}).items())[:12]
        }
        if isinstance(arguments, dict)
        else {},
        "status": _clip(status, 40) or None,
        "ok": status not in ("error", "denied"),
        "error": _clip(error, 400) or None,
    }


@dataclass(slots=True)
class ArchivedTurn:
    """One exchange, as the console redraws it."""

    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    #: Only ever on an assistant turn.
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.thinking:
            out["thinking"] = self.thinking
        if self.tool_calls:
            out["tool_calls"] = self.tool_calls
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> "ArchivedTurn | None":
        if not isinstance(raw, dict):
            return None
        role = str(raw.get("role") or "")
        if role not in ("user", "assistant"):
            return None
        calls = raw.get("tool_calls")
        return cls(
            role=role,
            content=_clip(raw.get("content")),
            timestamp=float(raw.get("timestamp") or time.time()),
            thinking=_clip(raw.get("thinking")),
            tool_calls=[c for c in (calls or []) if isinstance(c, dict)][:MAX_TOOL_CALLS],
        )

    def as_message(self) -> dict[str, str]:
        """The shape a prompt wants. Reasoning and tool calls are left behind:
        replaying a model's own thoughts back to it is how a wrong turn becomes
        a conviction."""
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ArchivedConversation:
    id: str
    title: str = ""
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turns: list[ArchivedTurn] = field(default_factory=list)

    def add(self, turn: ArchivedTurn, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.turns.append(turn)
        self.last_active = turn.timestamp
        if not self.title and turn.role == "user":
            self.title = _title_from(turn.content)
        if max_turns > 0 and len(self.turns) > max_turns:
            del self.turns[: len(self.turns) - max_turns]

    def summary(self) -> dict[str, Any]:
        """A row in the conversation list — no message bodies."""
        preview = ""
        for turn in reversed(self.turns):
            if turn.content:
                preview = _clip(turn.content, 160)
                break
        return {
            "id": self.id,
            "title": self.title or "New conversation",
            "created": self.created,
            "last_active": self.last_active,
            "turns": len(self.turns),
            "preview": preview,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "last_active": self.last_active,
            "turns": [t.as_dict() for t in self.turns],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ArchivedConversation | None":
        if not isinstance(raw, dict):
            return None
        conversation_id = str(raw.get("id") or "").strip()
        if not conversation_id:
            return None
        turns = [ArchivedTurn.from_dict(t) for t in (raw.get("turns") or [])]
        kept = [t for t in turns if t is not None][:DEFAULT_MAX_TURNS]
        return cls(
            id=conversation_id,
            title=_clip(raw.get("title"), MAX_TITLE_CHARS),
            created=float(raw.get("created") or time.time()),
            last_active=float(raw.get("last_active") or time.time()),
            turns=kept,
        )


class ConversationArchive:
    """Every conversation that has happened, newest first, kept on disk.

    Saving is fire-and-forget through ``schedule_save``: recording a turn is
    called from the end of a conversation and must not be able to fail it, and
    must not add disk latency to a reply the user is waiting on. A crash
    between a turn and its flush loses that turn from the *history list* and
    nothing else — the live conversation never came from here.
    """

    def __init__(
        self,
        store: "Store | None" = None,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
        max_turns: int = DEFAULT_MAX_TURNS,
        scheduler: Any = None,
    ) -> None:
        self.store = store
        self.max_conversations = max_conversations
        self.max_turns = max_turns
        #: Something that takes a coroutine and runs it — `Jarvis.async_create_task`
        #: in production, `None` in tests, where `async_save` is awaited directly.
        self.scheduler = scheduler
        self._conversations: dict[str, ArchivedConversation] = {}
        self._dirty = False

    # --- lifecycle --------------------------------------------------------
    async def async_load(self) -> int:
        """Read the archive off disk. Returns how many conversations came back."""
        if self.store is None:
            return 0
        try:
            data = await self.store.load()
        except Exception:  # pragma: no cover - a broken file must not stop boot
            _LOGGER.exception("Could not read the conversation archive")
            return 0
        items = (data or {}).get("conversations") if isinstance(data, dict) else None
        for raw in items or []:
            conversation = ArchivedConversation.from_dict(raw)
            if conversation is not None:
                self._conversations[conversation.id] = conversation
        self._evict()
        return len(self._conversations)

    async def async_save(self) -> None:
        if self.store is None:
            self._dirty = False
            return
        payload = {
            "conversations": [
                c.as_dict() for c in self._sorted()[: self.max_conversations]
            ]
        }
        self._dirty = False
        try:
            await self.store.save(payload)
        except Exception:  # pragma: no cover - disk full, read-only mount
            _LOGGER.exception("Could not write the conversation archive")

    def schedule_save(self) -> None:
        """Queue a flush without waiting for it."""
        self._dirty = True
        if self.store is None or self.scheduler is None:
            return
        try:
            self.scheduler(self.async_save())
        except Exception:  # pragma: no cover - loop shutting down
            _LOGGER.debug("Could not schedule a conversation-archive save", exc_info=True)

    @property
    def dirty(self) -> bool:
        return self._dirty

    # --- writes -----------------------------------------------------------
    def record(
        self,
        conversation_id: str,
        user_text: str = "",
        assistant_text: str = "",
        tool_calls: Any = None,
        thinking: str = "",
        title: str = "",
    ) -> ArchivedConversation | None:
        """Append one finished exchange. Never raises.

        A turn with neither a question nor an answer is not recorded: an empty
        request and a turn that died before its first token both land here, and
        a history list full of blank rows is worse than one that is short.
        """
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return None
        user_text = _clip(user_text)
        assistant_text = _clip(assistant_text)
        if not user_text and not assistant_text:
            return None

        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            conversation = ArchivedConversation(id=conversation_id)
            self._conversations[conversation_id] = conversation
        if title and not conversation.title:
            conversation.title = _title_from(title)

        if user_text:
            conversation.add(ArchivedTurn("user", user_text), self.max_turns)
        if assistant_text or tool_calls:
            conversation.add(
                ArchivedTurn(
                    "assistant",
                    assistant_text,
                    thinking=_clip(thinking),
                    tool_calls=[
                        summarise_tool_call(c) for c in (tool_calls or [])[:MAX_TOOL_CALLS]
                    ],
                ),
                self.max_turns,
            )
        self._evict()
        self.schedule_save()
        return conversation

    def remove(self, conversation_id: str) -> bool:
        gone = self._conversations.pop(str(conversation_id or ""), None) is not None
        if gone:
            self.schedule_save()
        return gone

    def clear(self) -> None:
        if not self._conversations:
            return
        self._conversations.clear()
        self.schedule_save()

    def rename(self, conversation_id: str, title: str) -> bool:
        conversation = self._conversations.get(str(conversation_id or ""))
        if conversation is None:
            return False
        conversation.title = _title_from(title)
        self.schedule_save()
        return True

    # --- reads ------------------------------------------------------------
    def get(self, conversation_id: str) -> ArchivedConversation | None:
        return self._conversations.get(str(conversation_id or ""))

    def listing(self) -> list[dict[str, Any]]:
        """Every conversation as a summary row, most recent first."""
        return [c.summary() for c in self._sorted()]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Conversations containing `query`, newest first, with the line that matched.

        Plain substring matching over the archived turns, not an index. The
        archive is bounded (`max_conversations` × `max_turns`) and lives in
        memory already, so a search across all of it is a few milliseconds —
        and an FTS index here would be a second store to keep in step with the
        JSON file that is the actual record.

        The MATCH is what makes this useful rather than a list of ids: a person
        searching for "blue tin" wants to see the sentence, and the id it
        belongs to is what they click.
        """
        needle = " ".join(str(query or "").split()).lower()
        if not needle:
            return []
        out: list[dict[str, Any]] = []
        for conversation in sorted(
            self._conversations.values(), key=lambda c: c.last_active, reverse=True
        ):
            hits: list[dict[str, Any]] = []
            for turn in conversation.turns:
                if needle in turn.content.lower():
                    hits.append(
                        {
                            "role": turn.role,
                            "timestamp": turn.timestamp,
                            "excerpt": _excerpt(turn.content, needle),
                        }
                    )
                if len(hits) >= 3:
                    break
            if not hits:
                continue
            summary = conversation.summary()
            summary["matches"] = hits
            summary["match_count"] = len(hits)
            out.append(summary)
            if len(out) >= max(1, int(limit)):
                break
        return out

    def messages(self, conversation_id: str, limit: int = 0) -> list[dict[str, str]]:
        """A reopened conversation as prompt messages, oldest last.

        ``limit`` keeps only the most recent N turns, which is what a caller
        seeding a bounded context window wants.
        """
        conversation = self.get(conversation_id)
        if conversation is None:
            return []
        turns = conversation.turns[-limit:] if limit > 0 else conversation.turns
        return [t.as_message() for t in turns if t.content]

    def __len__(self) -> int:
        return len(self._conversations)

    def __contains__(self, conversation_id: object) -> bool:
        return isinstance(conversation_id, str) and conversation_id in self._conversations

    # --- housekeeping -----------------------------------------------------
    def _sorted(self) -> list[ArchivedConversation]:
        return sorted(
            self._conversations.values(), key=lambda c: c.last_active, reverse=True
        )

    def _evict(self) -> None:
        if self.max_conversations <= 0:
            return
        overflow = len(self._conversations) - self.max_conversations
        if overflow <= 0:
            return
        for conversation in self._sorted()[self.max_conversations :]:
            self._conversations.pop(conversation.id, None)
