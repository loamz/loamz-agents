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
