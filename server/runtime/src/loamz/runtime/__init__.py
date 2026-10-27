"""Loamz Agents: durable agents, tools and an agent server, built on Loamz Data.

    from loamz.runtime import ReActAgent, Runtime, ToolRisk, connect, create_model_client, tool

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def shout(text: str) -> str:
        "Upper-case the text."
        return text.upper()

    store = await connect("./.loamz/agents.db")            # SQLite; AgentStore(postgres(dsn)) for many machines
    agent = ReActAgent("bot", model=create_model_client("gpt-4o-mini", api_keys=keys), tools=[shout])
    async with Runtime(store, owns_store=True) as runtime:
        print((await runtime.run(agent, "Say hi loudly")).output)

The names here are the ones nearly every program wants; everything else is imported from where it lives
(``loamz.runtime.kernel.*``, ``loamz.runtime.stores``, ``loamz.runtime.providers``, ``loamz.runtime.toolkit``).
Importing ``loamz.runtime`` imports nothing heavy: each name loads on first use.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

# A library installs no handler of its own: the application decides where records go (``bootstrap.logging``).
logging.getLogger("loamz.runtime").addHandler(logging.NullHandler())

_LAZY: dict[str, str] = {
    # agents
    "ReActAgent": "loamz.runtime.kernel.agents",
    "OrchestratorAgent": "loamz.runtime.kernel.agents",
    "SubAgentConfig": "loamz.runtime.kernel.agents",
    "UserProxyAgent": "loamz.runtime.kernel.agents",
    "ChannelMemberAgent": "loamz.runtime.kernel.agents",
    "SequentialFlow": "loamz.runtime.kernel.agents",
    "ParallelFlow": "loamz.runtime.kernel.agents",
    "ConditionalFlow": "loamz.runtime.kernel.agents",
    # running them
    "Runtime": "loamz.runtime.kernel.runtime",
    "RunOutcome": "loamz.runtime.kernel.runtime",
    "AgentStore": "loamz.runtime.stores",
    # context and middleware
    "ContextConfig": "loamz.runtime.kernel.context",
    "CompactionPipeline": "loamz.runtime.kernel.context",
    "SlidingWindowCompaction": "loamz.runtime.kernel.context",
    "Middleware": "loamz.runtime.kernel.middleware",
    "MiddlewarePipeline": "loamz.runtime.kernel.middleware",
    # tools
    "Tool": "loamz.runtime.kernel.tools",
    "ToolRisk": "loamz.runtime.kernel.tools",
    "Toolbox": "loamz.runtime.kernel.tools",
    "ToolExecutionResult": "loamz.runtime.kernel.tools",
    "tool": "loamz.runtime.kernel.tools",
    "ApprovalDecision": "loamz.runtime.kernel.tools",
    "AutoApprove": "loamz.runtime.kernel.tools",
    "DurableApproval": "loamz.runtime.kernel.tools",
    "Skill": "loamz.runtime.kernel.tools",
    # models and content
    "ChatModel": "loamz.runtime.kernel.models",
    "GenerationOptions": "loamz.runtime.kernel.models",
    "ReasoningEffort": "loamz.runtime.kernel.models",
    "ChatMessage": "loamz.runtime.kernel.types",
    "TextBlock": "loamz.runtime.kernel.types",
    "create_model_client": "loamz.runtime.providers.llm",
    "LLMFactory": "loamz.runtime.providers.llm",
}

if TYPE_CHECKING:
    from loamz.runtime.kernel.agents import (
        ChannelMemberAgent,
        ConditionalFlow,
        OrchestratorAgent,
        ParallelFlow,
        ReActAgent,
        SequentialFlow,
        SubAgentConfig,
        UserProxyAgent,
    )
    from loamz.runtime.kernel.context import (
        CompactionPipeline,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from loamz.runtime.kernel.middleware import Middleware, MiddlewarePipeline
    from loamz.runtime.kernel.models import ChatModel, GenerationOptions, ReasoningEffort
    from loamz.runtime.kernel.runtime import RunOutcome, Runtime
    from loamz.runtime.kernel.tools import (
        ApprovalDecision,
        AutoApprove,
        DurableApproval,
        Skill,
        Tool,
        Toolbox,
        ToolExecutionResult,
        ToolRisk,
        tool,
    )
    from loamz.runtime.kernel.types import ChatMessage, TextBlock
    from loamz.runtime.providers.llm import LLMFactory, create_model_client
    from loamz.runtime.stores import AgentStore


async def connect(path: str | Path = "./.loamz/agents.db") -> AgentStore:
    """Open (creating it if there is none) the agent store in the SQLite file ``path``."""
    from loamz.runtime.stores import AgentStore

    store = AgentStore.at(path)
    await store.start()
    return store


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'loamz.runtime' has no attribute {name!r}")


__all__ = [
    "AgentStore",
    "ApprovalDecision",
    "AutoApprove",
    "ChannelMemberAgent",
    "ChatMessage",
    "ChatModel",
    "CompactionPipeline",
    "ConditionalFlow",
    "ContextConfig",
    "DurableApproval",
    "GenerationOptions",
    "LLMFactory",
    "Middleware",
    "MiddlewarePipeline",
    "OrchestratorAgent",
    "ParallelFlow",
    "ReActAgent",
    "ReasoningEffort",
    "RunOutcome",
    "Runtime",
    "SequentialFlow",
    "Skill",
    "SlidingWindowCompaction",
    "SubAgentConfig",
    "TextBlock",
    "Tool",
    "ToolExecutionResult",
    "ToolRisk",
    "Toolbox",
    "UserProxyAgent",
    "connect",
    "create_model_client",
    "tool",
]
