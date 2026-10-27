"""loamz.runtime.toolkit — built-in tools for ReActAgent + protocol bridges.

Tools are grouped by domain:
  mcp/          — Model Context Protocol client/tool bridge
  web/          — search, surf, read_url, wikipedia
  communication/— email_sender, http_request
  compute/      — calculator
  utils/        — current_time, tool_search
  ai/           — image_generator
  (root)        — memory, human_input
  task_manager/ — Kanban board
  code/         — sandboxed code execution (CodeInterpreterTool)
  discovery     — the catalog scanner that finds every shipped tool
  chain/        — ToolChainTool (sandboxed code-mode tool chaining)
  skills/       — SKILL.md prompt-skill packages

Quick-start::

    from loamz.runtime.toolkit.compute.calculator import CalculatorTool
    from loamz.runtime.toolkit.web.search import WebSearchTool
    agent = ReActAgent("bot", model=llm, tools=[CalculatorTool(), WebSearchTool()])
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# A tool is imported when it is asked for, not when the package is: each one needs its own extra (``mcp``, ``web``,
# ``code``…), and using the calculator must not require the web-search stack to be installed.
_LAZY: dict[str, str] = {
    "MCPClient": "loamz.runtime.toolkit.mcp",
    "MCPTool": "loamz.runtime.toolkit.mcp",
    "CalculatorTool": "loamz.runtime.toolkit.compute.calculator",
    "CurrentTimeTool": "loamz.runtime.toolkit.utils.current_time",
    "WebSearchTool": "loamz.runtime.toolkit.web.search",
    "ReadUrlTool": "loamz.runtime.toolkit.web.read_url",
    "WikipediaTool": "loamz.runtime.toolkit.web.wikipedia",
    "WebSurferTool": "loamz.runtime.toolkit.web.surfer",
    "ToolChainTool": "loamz.runtime.toolkit.chain",
    "EmailSenderTool": "loamz.runtime.toolkit.communication.email_sender",
    "HttpRequestTool": "loamz.runtime.toolkit.communication.http_request",
    "AskHumanTool": "loamz.runtime.toolkit.human_input",
    "ImageGeneratorTool": "loamz.runtime.toolkit.ai.image_generator",
    "MemoryTool": "loamz.runtime.toolkit.memory",
    "TaskManagerTool": "loamz.runtime.toolkit.task_manager.tool",
    "ToolSearchTool": "loamz.runtime.toolkit.utils.tool_search",
}

if TYPE_CHECKING:
    from loamz.runtime.toolkit.ai.image_generator import ImageGeneratorTool
    from loamz.runtime.toolkit.chain import ToolChainTool
    from loamz.runtime.toolkit.communication.email_sender import EmailSenderTool
    from loamz.runtime.toolkit.communication.http_request import HttpRequestTool
    from loamz.runtime.toolkit.compute.calculator import CalculatorTool
    from loamz.runtime.toolkit.human_input import AskHumanTool
    from loamz.runtime.toolkit.mcp import MCPClient, MCPTool
    from loamz.runtime.toolkit.memory import MemoryTool
    from loamz.runtime.toolkit.task_manager.tool import TaskManagerTool
    from loamz.runtime.toolkit.utils.current_time import CurrentTimeTool
    from loamz.runtime.toolkit.utils.tool_search import ToolSearchTool
    from loamz.runtime.toolkit.web.read_url import ReadUrlTool
    from loamz.runtime.toolkit.web.search import WebSearchTool
    from loamz.runtime.toolkit.web.surfer import WebSurferTool
    from loamz.runtime.toolkit.web.wikipedia import WikipediaTool

# CodeInterpreterTool is intentionally NOT exported here.
# It executes arbitrary code in a sandboxed VM and requires explicit opt-in
# by the caller: from loamz.runtime.toolkit.code.tool import CodeInterpreterTool

__all__ = [
    "MCPClient",
    "MCPTool",
    "CalculatorTool",
    "CurrentTimeTool",
    "WebSearchTool",
    "ReadUrlTool",
    "WikipediaTool",
    "WebSurferTool",
    "ToolChainTool",
    "EmailSenderTool",
    "HttpRequestTool",
    "AskHumanTool",
    "ImageGeneratorTool",
    "MemoryTool",
    "TaskManagerTool",
    "ToolSearchTool",
]


def __getattr__(name: str) -> object:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
