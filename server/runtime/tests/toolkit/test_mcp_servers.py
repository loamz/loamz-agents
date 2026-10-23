"""Tool servers against a real MCP server over SSE: naming, approval risk, auth, restart and a server that is down."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from loamz.engine.agents.kernel.tools import ToolRisk
from loamz.engine.agents.toolkit.mcp.servers import McpServerSpec, McpToolServers, exposed

TOKEN = "s3cret"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Needs:
    """ASGI middleware: the bearer token must match."""

    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            given = dict(scope["headers"]).get(b"authorization", b"").decode()
            if given != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"no"})
                return
        await self.app(scope, receive, send)


class Server:
    def __init__(self, port: int):
        self.port = port
        mcp = FastMCP("test", host="127.0.0.1", port=port)

        @mcp.tool()
        def status() -> str:
            """Where things stand."""
            return "all good"

        @mcp.tool()
        def place_order(symbol: str, qty: int) -> str:
            """Buy something."""
            return f"bought {qty} {symbol}"

        self.app = Needs(mcp.sse_app(), TOKEN)
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/sse"


@pytest.fixture
def server():
    running = Server(free_port()).start()
    yield running
    running.stop()


def spec(server, **options):
    return McpServerSpec(
        "desk", server.url, token=options.pop("token", TOKEN), timeout=3, call_timeout=4, **options
    )


async def test_tools_are_discovered_prefixed_and_need_approval_unless_vouched_for(server):
    servers = McpToolServers([spec(server, safe_tools={"status"})])
    await servers.refresh()
    try:
        assert set(servers.describe()) == {"desk__status", "desk__place_order"}
        by_name = {t.name: t for t in servers.tools(["desk__status", "desk__place_order", "nope"])}
        assert set(by_name) == {
            "desk__status",
            "desk__place_order",
        }  # an unknown name is simply absent
        assert (
            by_name["desk__status"].risk == ToolRisk.SAFE
            and by_name["desk__place_order"].risk == ToolRisk.HIGH
        )
        assert servers.status() == [
            {"name": "desk", "tools": ["desk__place_order", "desk__status"], "error": None}
        ]
    finally:
        await servers.close()


async def test_a_tool_runs_under_the_servers_own_name(server):
    servers = McpToolServers([spec(server)])
    await servers.refresh()
    try:
        tool = servers.found["desk__place_order"]
        result = await tool.execute(call_id="c1", symbol="AAPL", qty=3)
        assert (
            not result.is_error
            and result.content[0].text == "bought 3 AAPL"
            and result.call_id == "c1"
        )
        assert tool.input_schema["properties"]["qty"]["type"] == "integer"
    finally:
        await servers.close()


async def test_a_wrong_token_is_reported_and_no_tools_appear(server):
    servers = McpToolServers([spec(server, token="wrong")])
    await servers.refresh()
    try:
        assert servers.describe() == {} and servers.status()[0]["error"]
    finally:
        await servers.close()


async def test_a_server_that_is_down_is_reported_and_does_not_hide_the_others(server):
    dead = McpServerSpec("gone", f"http://127.0.0.1:{free_port()}/sse", timeout=1, call_timeout=1)
    servers = McpToolServers([dead, spec(server)])
    await servers.refresh()
    try:
        assert "desk__status" in servers.describe()
        statuses = {s["name"]: s for s in servers.status()}
        assert statuses["gone"]["error"] and statuses["desk"]["error"] is None
    finally:
        await servers.close()


async def test_a_restarted_server_is_reconnected_on_the_next_call():
    first = Server(free_port()).start()
    port = first.port
    servers = McpToolServers(
        [McpServerSpec("desk", first.url, token=TOKEN, timeout=3, call_timeout=3)]
    )
    await servers.refresh()
    tool = servers.found["desk__status"]
    assert (await tool.execute(call_id="1")).content[0].text == "all good"
    first.stop()
    await asyncio.sleep(0.2)
    second = Server(port).start()
    try:
        again = await tool.execute(call_id="2")
        assert not again.is_error and again.content[0].text == "all good"
    finally:
        await servers.close()
        second.stop()


def test_names_are_validated_and_exposed_names_are_safe_for_models():
    with pytest.raises(ValueError):
        McpServerSpec("Bad Name", "http://x/sse")
    with pytest.raises(ValueError, match="unique"):
        McpToolServers([McpServerSpec("a", "http://x/sse"), McpServerSpec("a", "http://y/sse")])
    assert exposed("desk", "place.order v2") == "desk__place_order_v2"


async def test_a_connection_opened_by_one_task_is_usable_and_closable_from_others(server):
    """The MCP SDK ties its cancellation scopes to the task that opened a connection. A tool server is opened by a startup
    task, refreshed by a background task and called from run tasks: none of them may be the one that owns the connection."""
    servers = McpToolServers([spec(server)])
    await asyncio.create_task(servers.refresh())  # a task that opens the connection, then ends
    tool = servers.found["desk__status"]
    assert (await asyncio.create_task(tool.execute(call_id="1"))).content[0].text == "all good"
    await asyncio.create_task(servers.refresh())  # another task refreshes
    assert (await asyncio.create_task(tool.execute(call_id="2"))).content[0].text == "all good"
    await asyncio.create_task(
        servers.close()
    )  # and yet another closes it, without cancelling anything else
    survivor = asyncio.create_task(asyncio.sleep(0.3, result="alive"))
    assert await survivor == "alive"


async def test_a_dead_server_fails_calls_quickly_and_then_at_once():
    """A tool server that dies must not park runs for the whole call timeout: that would starve every other conversation."""
    live = Server(free_port()).start()
    servers = McpToolServers(
        [McpServerSpec("desk", live.url, token=TOKEN, timeout=2, call_timeout=60)]
    )
    await servers.refresh()
    tool = servers.found["desk__status"]
    assert not (await tool.execute(call_id="1")).is_error
    live.stop()
    link = tool.link
    link.last_ok -= (
        link.IDLE_PING_AFTER + 1
    )  # the connection has been idle: the next call checks it first
    started = time.monotonic()
    failed = await tool.execute(call_id="2")
    assert failed.is_error and time.monotonic() - started < 20  # not 60 seconds
    started = time.monotonic()
    again = await tool.execute(call_id="3")
    assert (
        again.is_error
        and "retried shortly" in again.content[0].text
        and time.monotonic() - started < 1
    )
    await servers.close()


async def test_connection_failures_never_cancel_unrelated_tasks():
    """The live failure this guards against: a tool server dies while runs are calling it, and the worker's own loop and lease
    renewal stop, so no later conversation runs. A bystander task must keep ticking through every kind of failure."""
    first = Server(free_port()).start()
    port = first.port
    servers = McpToolServers(
        [McpServerSpec("desk", first.url, token=TOKEN, timeout=2, call_timeout=4)]
    )
    await servers.refresh()
    ticks = []

    async def bystander():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(bystander())
    tool = servers.found["desk__status"]
    link = tool.link
    try:
        calls = [asyncio.create_task(tool.execute(call_id=str(n))) for n in range(6)]
        first.stop()  # dies while calls are in flight
        results = await asyncio.gather(*calls)
        assert (
            len(results) == 6
        )  # every call came back, as a result or an error: none hung or vanished
        link.last_ok -= link.IDLE_PING_AFTER + 1
        link.down_until = 0
        second = Server(port).start()  # comes back
        again = await asyncio.create_task(tool.execute(call_id="after"))
        assert not again.is_error
        await asyncio.create_task(servers.refresh())
        await asyncio.create_task(servers.close())
        second.stop()
        before = len(ticks)
        await asyncio.sleep(0.4)
        assert not watcher.done() and len(ticks) > before + 3  # still alive and ticking
    finally:
        watcher.cancel()
