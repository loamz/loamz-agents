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
import time
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

    IDLE_PING_AFTER = (
        20.0  # seconds without traffic before a call first checks the connection is alive
    )
    PING_TIMEOUT = 5.0
    COOL_DOWN = 15.0  # after a failure, calls fail at once for this long instead of each waiting out a timeout

    def __init__(self, spec: McpServerSpec, factory: Callable[[], MCPClient]) -> None:
        self.spec, self.factory = spec, factory
        self.client: MCPClient | None = None
        self.lock = asyncio.Lock()
        self.last_ok = 0.0
        self.down_until = 0.0
        self.owner: asyncio.Task | None = None
        self.stop: asyncio.Event | None = None

    async def connect(self) -> MCPClient:
        """A connected client. The connection is opened, held and closed by one dedicated task, never by the caller: the MCP
        SDK ties cancellation scopes to the task that opens a connection, and tearing one down from a different task (a run, a
        refresh) cancels unrelated tasks, including the worker's own loop."""
        async with self.lock:
            if (
                self.client is not None
                and self.client.is_connected
                and self.owner
                and not self.owner.done()
            ):
                return self.client
            await self.shutdown()
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            self.stop = asyncio.Event()
            self.owner = loop.create_task(self.own(ready, self.stop), name=f"mcp-{self.spec.name}")
            await ready  # raises if the connection could not be made
            assert self.client is not None
            self.last_ok = time.monotonic()
            return self.client

    async def own(self, ready: asyncio.Future[None], stop: asyncio.Event) -> None:
        client = self.factory()
        headers = {"Authorization": f"Bearer {self.spec.token}"} if self.spec.token else None
        try:
            await client.connect_sse(self.spec.url, headers=headers, timeout=self.spec.timeout)
        except BaseException as error:  # noqa: BLE001 - reported to the caller waiting in connect()
            if not ready.done():
                ready.set_exception(
                    error if isinstance(error, Exception) else RuntimeError("connection cancelled")
                )
            return
        self.client = client
        ready.set_result(None)
        try:
            await stop.wait()
        finally:
            with contextlib.suppress(BaseException):
                await client.disconnect()  # the same task that connected

    async def shutdown(self) -> None:
        owner, stop, self.owner, self.client = self.owner, self.stop, None, None
        if owner is None:
            return
        if stop is not None:
            stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(owner), 5)
        except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001 - a connection that will not close is abandoned
            owner.cancel()
            with contextlib.suppress(BaseException):
                await owner

    async def drop(self) -> None:
        await self.shutdown()

    async def healthy(self) -> MCPClient:
        """A connected client. A connection idle for a while is pinged first: a peer that died silently would otherwise only be
        noticed when a call waited out its whole timeout, and a stuck call holds one of the platform's run slots."""
        client = await self.connect()
        if time.monotonic() - self.last_ok > self.IDLE_PING_AFTER and client.session is not None:
            try:
                await asyncio.wait_for(client.session.send_ping(), self.PING_TIMEOUT)
            except Exception:  # noqa: BLE001 - dead or unresponsive: make a new connection
                await self.drop()
                client = await self.connect()
        return client

    async def call(self, tool: str, arguments: dict[str, object]) -> Any:
        if time.monotonic() < self.down_until:
            raise RuntimeError(
                f"the {self.spec.name} tool server is unavailable; it will be retried shortly"
            )

        async def once() -> Any:
            client = await self.healthy()
            result = await asyncio.wait_for(
                client.call_tool(tool, arguments), self.spec.call_timeout
            )
            self.last_ok = time.monotonic()
            return result

        try:
            return await once()
        except Exception:  # noqa: BLE001 - the connection may have dropped (or hung): reconnect once and retry
            await self.drop()
            try:
                return await once()
            except Exception:
                self.down_until = time.monotonic() + self.COOL_DOWN
                raise

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
