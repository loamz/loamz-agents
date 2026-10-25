"""Agents use the operator's tool servers in chat: only the tools named for them, and a person approves risky calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.tools import ToolRisk
from loamz.engine.agents.testing.scripted import ScriptedModel, ToolCall
from loamz.engine.agents.toolkit.mcp.servers import McpToolServers
from loamz.engine.agents.toolkit.mcp.tool import MCPTool
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.bootstrap.settings import Settings
from loamz.server.agents.features.conversations.chat.model import ChatRequest, HitlResponse
from loamz.server.agents.features.conversations.chat.service import chat
from loamz.server.agents.features.conversations.threads.service import threads
from loamz.server.agents.features.directory.agents.model import AgentIn
from loamz.server.agents.features.directory.agents.service import agents

CALLER = Caller(tenant_id="acme", user_id="alex")
SCHEMA = {"type": "object", "properties": {"symbol": {"type": "string"}}}


class Desk:
    """A stand-in for a connected MCP server: records every call."""

    is_connected = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"{name} done")], isError=False
        )


@pytest.fixture
async def world(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "files")},
            chat={"max_concurrent_runs": 2},
        )
    )
    desk = Desk()
    servers = McpToolServers([])
    servers.found = {
        "desk__status": MCPTool(
            desk,
            "desk__status",
            "Where things stand.",
            SCHEMA,
            risk=ToolRisk.SAFE,
            remote_name="status",
        ),
        "desk__place_order": MCPTool(
            desk,
            "desk__place_order",
            "Buy something.",
            SCHEMA,
            risk=ToolRisk.HIGH,
            remote_name="place_order",
        ),
    }
    container.__dict__["tool_servers"] = servers
    await container.start({"all"})
    try:
        yield SimpleNamespace(container=container, desk=desk)
    finally:
        await container.aclose()


def use_model(container, *replies):
    model = ScriptedModel(*replies)
    container.model_client = lambda name=None: model
    return model


async def open_thread(container, allowed):
    agent = await container.get(agents).create(
        CALLER, AgentIn(name="Concierge", role="assistant", allowed_tools=allowed)
    )
    header = await container.get(threads).create_for("acme", "alex", agent_id=agent.id)
    return agent, header


async def converse(container, agent, header, text, decide=None):
    """Run a chat turn; `decide(pending)` is called for each approval the run asks for and returns an action."""
    service = container.get(chat)
    events: list[str] = []

    async def drain():
        request = ChatRequest(thread_id=header.id, agent_id=agent.id, prompt=text)
        await service.admit(CALLER, request)
        async for piece in service.chat(CALLER, request):
            events.append(piece)

    task = asyncio.create_task(drain())
    asked = []
    for _ in range(300):
        if task.done():
            break
        status = await service.hitl_status(CALLER, header.id)
        for pending in status.pending:
            if any(a["request_id"] == pending["request_id"] for a in asked):
                continue  # already decided; the run has not woken yet
            asked.append(pending)
            if decide:
                await service.respond_hitl(
                    CALLER, pending["request_id"], HitlResponse(action=decide(pending))
                )
        await asyncio.sleep(0.05)
    await asyncio.wait_for(task, 20)
    return "".join(events), asked


async def test_a_listed_tool_waits_for_approval_then_runs(world):
    use_model(
        world.container, ToolCall("desk__place_order", {"symbol": "AAPL"}), "Done: ordered AAPL."
    )
    agent, header = await open_thread(world.container, ["desk__place_order"])
    text, asked = await converse(
        world.container, agent, header, "buy AAPL", decide=lambda p: "approve"
    )
    assert [p["tool_name"] for p in asked] == ["desk__place_order"] and asked[0]["tool_args"] == {
        "symbol": "AAPL"
    }
    assert world.desk.calls == [
        ("place_order", {"symbol": "AAPL"})
    ]  # called by the server's own name
    assert "Done: ordered AAPL." in text and "run.completed" in text


async def test_a_denied_call_never_reaches_the_server(world):
    use_model(
        world.container,
        ToolCall("desk__place_order", {"symbol": "TSLA"}),
        "Understood, I did not order it.",
    )
    agent, header = await open_thread(world.container, ["desk__place_order"])
    text, asked = await converse(
        world.container, agent, header, "buy TSLA", decide=lambda p: "deny"
    )
    assert len(asked) == 1 and world.desk.calls == []
    assert "did not order it" in text


async def test_a_read_only_tool_runs_without_asking(world):
    use_model(world.container, ToolCall("desk__status", {}), "All good.")
    agent, header = await open_thread(world.container, ["desk__status"])
    text, asked = await converse(world.container, agent, header, "how are things?")
    assert asked == [] and world.desk.calls == [("status", {})] and "All good." in text


async def test_an_agent_gets_only_the_tools_named_for_it(world):
    model = use_model(world.container, "I have no tools.")
    agent, header = await open_thread(world.container, ["desk__status"])  # not place_order
    await converse(world.container, agent, header, "buy")
    unlimited, header2 = await open_thread(
        world.container, None
    )  # "everything" never includes a tool server's tools
    model2 = use_model(world.container, "Nothing to call.")
    await converse(world.container, unlimited, header2, "buy")
    assert world.desk.calls == [] and model is not None and model2 is not None


async def test_the_tool_names_can_be_validated_when_an_agent_is_created(world):
    service = world.container.get(agents)
    assert set(service.known_tools()) >= {"desk__status", "desk__place_order"}
    with pytest.raises(Exception, match="Unknown tool"):
        await service.create(CALLER, AgentIn(name="Bad", allowed_tools=["desk__missing"]))
