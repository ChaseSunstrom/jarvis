"""The Jarvis brain: a local Ollama client, a tool registry and an agent.

Nothing in here talks to a cloud service. `ollama.OllamaClient` speaks to a
local Ollama over HTTP, `tools.ToolRegistry` exposes the house (and any
YAML-declared HTTP tools) as callable functions, and `agent.ConversationAgent`
runs the tool-calling loop and streams the answer back.

The safety gate lives in :mod:`jarvis.llm.tools`, outside the model: tier-3
tools and anything touching a gated domain (lock, notify) never execute from
a model turn. They return ``approval_required`` and wait for
``ToolRegistry.approve_request``. No persona text or prompt injection can
reach around that, because the model never gets to make the decision.
"""

from __future__ import annotations

from .agent import ConversationAgent, ConversationResult
from .memory import Conversation, ConversationStore, Turn
from .ollama import ChatResult, ChatStream, OllamaClient, OllamaError, ToolCall
from .tools import (
    EVENT_APPROVAL_REQUIRED,
    EVENT_APPROVAL_RESOLVED,
    EVENT_BACKGROUND_TASK,
    Exposure,
    Tool,
    ToolRegistry,
    build_yaml_tools,
    register_builtin_tools,
)

__all__ = [
    "ChatResult",
    "ChatStream",
    "Conversation",
    "ConversationAgent",
    "ConversationResult",
    "ConversationStore",
    "EVENT_APPROVAL_REQUIRED",
    "EVENT_APPROVAL_RESOLVED",
    "EVENT_BACKGROUND_TASK",
    "Exposure",
    "OllamaClient",
    "OllamaError",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "Turn",
    "build_yaml_tools",
    "register_builtin_tools",
]
