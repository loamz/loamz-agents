"""Loamz's own tools, by their Marketplace id: what an agent is given when its abilities (or the person's installs) name one."""

from __future__ import annotations

from collections.abc import Callable

from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.chat.lookups import (
    CurrencyTool,
    PackagesTool,
    PapersTool,
    WeatherTool,
)
from loamz.server.agents.features.conversations.chat.webtools import ReadPageTool

Choices = dict[str, str]


class Asking:
    """A tool as the person set it: `allow` runs without asking, `ask` waits for a yes each time. A blocked tool is never built."""

    def __init__(self, tool: object, risk: ToolRisk) -> None:
        self._tool = tool
        self.risk = risk

    def __getattr__(self, name: str) -> object:
        return getattr(self._tool, name)

    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        return await self._tool.execute(**kwargs)  # type: ignore[attr-defined,no-any-return]


def _web_search(container: Container) -> object:
    from loamz.runtime.toolkit.web.search import WebSearchTool

    return WebSearchTool()


def _wikipedia(container: Container) -> object:
    from loamz.runtime.toolkit.web.wikipedia import WikipediaTool

    return WikipediaTool()


def _images(container: Container) -> object:
    from loamz.runtime.toolkit.ai.image_generator import ImageGeneratorTool

    return ImageGeneratorTool(api_key=container.settings.llm.openai_api_key or None)


def _loamz_data(container: Container) -> list[object]:
    """Search, describe and query the tables this person's teams may read; nothing without a data service."""
    return list(container.data_tool_set.values())


BUILDERS: dict[str, Callable[[Container], object | list[object]]] = {
    "loamz-data": _loamz_data,
    "weather": lambda container: WeatherTool(),
    "currency": lambda container: CurrencyTool(),
    "papers": lambda container: PapersTool(),
    "packages": lambda container: PackagesTool(),
    "web-search": _web_search,
    "read-page": lambda container: ReadPageTool(),
    "wikipedia": _wikipedia,
    "image-generation": _images,
}
"""Marketplace tool id -> how to build it. Files, the code interpreter and memory are built elsewhere: every agent has them."""


def current_time() -> object:
    """The date and time tool: every agent has it, so it never says today is a date from its training."""
    from loamz.runtime.toolkit.utils.current_time import CurrentTimeTool

    return CurrentTimeTool()


def tools_for(container: Container, choices: dict[str, Choices]) -> list[object]:
    """The built tools for `{tool id: {group: allow|ask|block}}`. A tool whose every group is blocked is left out; one that can change or
    create things (any group other than read) asks each time unless the person allowed it."""
    out: list[object] = []
    for tool_id, groups in choices.items():
        build = BUILDERS.get(tool_id)
        if build is None or (groups and all(c == "block" for c in groups.values())):
            continue
        acting = [c for g, c in groups.items() if g != "read"]
        risk = ToolRisk.SAFE if not acting or all(c == "allow" for c in acting) else ToolRisk.HIGH
        built = build(container)
        for tool in built if isinstance(built, list) else [built]:
            out.append(tool if risk == getattr(tool, "risk", None) else Asking(tool, risk))
    return out
