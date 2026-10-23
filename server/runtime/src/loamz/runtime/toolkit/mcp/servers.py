"""Tool servers an operator connects to the platform: their tools, ready for agents, with approval by default.

    servers = McpToolServers([McpServerSpec("trading", "http://solutions:8765/sse", token="…", safe_tools={"status", "explain"})])
    await servers.refresh()                       # connect, discover (best effort: a down server is reported, not fatal)
    servers.describe()                            # {"trading__status": "...", "trading__place_order": "..."}
    servers.tools({"trading__status"})            # the tool objects an agent may use

Rules this class enforces, because a tool server is code the platform does not control:
- the model sees `<server>__<tool>`, so two servers cannot collide;
- every tool carries risk HIGH (a person approves each call) unless the operator listed it in `safe_tools`: what a server says
  about its own tools is never trusted;
- agents get only the tools named for them, never "all of a server";
- a dropped connection is re-made once per call and again on the next `refresh()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from loamz.engine.agents.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.engine.agents.kernel.types.content import TextBlock
from loamz.engine.agents.toolkit.mcp.client import MCPClient
from loamz.engine.agents.toolkit.mcp.tool import MCPTool

logger = logging.getLogger(__name__)
NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class McpServerSpec:
    name: str  # lower-case letters, digits and underscores: it prefixes every tool
    url: str  # the server's SSE endpoint
    token: str = ""  # sent as `Authorization: Bearer <token>`
    safe_tools: frozenset[str] = field(
        default_factory=frozenset
    )  # server-side tool names that need no approval
    timeout: float = 30.0  # to connect
    call_timeout: float = 120.0  # for one tool call; a dead connection hangs rather than fails, so this also detects it

    def __post_init__(self) -> None:
        if not NAME.match(self.name):
            raise ValueError(
                f"A tool server name is lower-case letters, digits and underscores: {self.name!r}"
            )
        object.__setattr__(self, "safe_tools", frozenset(self.safe_tools))


def exposed(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{tool}")[:64]


class Link:
    """One server connection that re-makes itself."""

    def __init__(self, spec: McpServerSpec, factory: Callable[[], MCPClient]) -> None:
        self.spec, self.factory = spec, factory
        self.client: MCPClient | None = None
        self.lock = asyncio.Lock()

    async def connect(self) -> MCPClient:
        async with self.lock:
            if self.client is not None and self.client.is_connected:
                return self.client
            await self.drop()
            client = self.factory()
            headers = {"Authorization": f"Bearer {self.spec.token}"} if self.spec.token else None
            await client.connect_sse(self.spec.url, headers=headers, timeout=self.spec.timeout)
            self.client = client
            return client

    async def drop(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            with contextlib.suppress(Exception, asyncio.TimeoutError):
                await asyncio.wait_for(client.disconnect(), 5)

    async def call(self, tool: str, arguments: dict[str, object]) -> Any:
        async def once() -> Any:
            return await asyncio.wait_for(
                (await self.connect()).call_tool(tool, arguments), self.spec.call_timeout
            )

        try:
            return await once()
        except Exception:  # noqa: BLE001 - the connection may have dropped (or hung): reconnect once and retry
            await self.drop()
            return await once()

    async def close(self) -> None:
        async with self.lock:
            await self.drop()


class ServerTool(MCPTool):
    """A tool of a connected server, called through its re-connecting link."""

    risk = (
        ToolRisk.HIGH
    )  # deny by default: the operator vouches for a tool with `safe_tools`, the server never does
    idempotent = False

    def __init__(
        self,
        link: Link,
        remote: str,
        name: str,
        description: str,
        schema: dict[str, object],
        risk: ToolRisk,
    ) -> None:
        super().__init__(link.client, name, description, schema, risk=risk, remote_name=remote)  # type: ignore[arg-type]
        self.link = link

    async def execute(self, *, ctx: object = None, **kwargs: object) -> ToolExecutionResult:  # type: ignore[override]
        call_id = str(kwargs.pop("call_id", ""))
        try:
            return self.to_result(call_id, await self.link.call(self.remote_name, kwargs))
        except Exception as error:  # noqa: BLE001 - the model is told, the run goes on
            return ToolExecutionResult(
                call_id=call_id,
                content=[TextBlock(text=f"The {self.link.spec.name} tool server failed: {error}")],
                is_error=True,
            )


class McpToolServers:
    def __init__(
        self, specs: Collection[McpServerSpec], *, factory: Callable[[], MCPClient] = MCPClient
    ) -> None:
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError("Tool server names must be unique")
        self.links = {s.name: Link(s, factory) for s in specs}
        self.found: dict[str, ServerTool] = {}
        self.errors: dict[str, str] = {}
        self.task: asyncio.Task | None = None

    async def refresh(self) -> None:
        """Connect to every server and list its tools. A server that is down keeps its last known tools and is reported."""
        for name, link in self.links.items():
            try:
                client = await link.connect()
                listed = await client.list_tools()
            except Exception as error:  # noqa: BLE001 - one bad server must not take the others down
                self.errors[name] = f"{type(error).__name__}: {error}"
                logger.warning("tool server %s unavailable: %s", name, error)
                continue
            self.errors.pop(name, None)
            for stale in [k for k, t in self.found.items() if t.link is link]:
                del self.found[stale]
            for tool in listed:
                remote = str(tool["name"])
                risk = ToolRisk.SAFE if remote in link.spec.safe_tools else ToolRisk.HIGH
                key = exposed(name, remote)
                self.found[key] = ServerTool(
                    link, remote, key, str(tool.get("description", "")), tool["inputSchema"], risk
                )  # type: ignore[arg-type]

    def describe(self) -> dict[str, str]:
        return {name: tool.description for name, tool in self.found.items()}

    def tools(self, names: Collection[str]) -> list[ServerTool]:
        return [self.found[n] for n in names if n in self.found]

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "tools": sorted(k for k, t in self.found.items() if t.link is link),
                "error": self.errors.get(name),
            }
            for name, link in self.links.items()
        ]

    def start(self, every: float = 60.0) -> None:
        """Keep refreshing in the background, so a server that starts late or restarts is picked up."""

        async def loop() -> None:
            while True:
                await asyncio.sleep(every)
                await self.refresh()

        if self.task is None and self.links:
            self.task = asyncio.get_running_loop().create_task(loop())

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        for link in self.links.values():
            await link.close()
