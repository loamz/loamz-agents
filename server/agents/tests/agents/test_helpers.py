"""A chat agent can start read-only helpers side by side and gets one short report from each."""

from __future__ import annotations

import asyncio

import pytest

from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.models.protocols import FinishReason, LLMResponse
from loamz.runtime.kernel.types.content import Role, TextBlock, ToolResultBlock, ToolUseBlock
from loamz.runtime.kernel.types.stream import CompletionEvent, TextDelta
from loamz.runtime.kernel.types.usage import Usage
from loamz.runtime.testing.scripted import ScriptedModel
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.features.conversations.chat.helpers import (
    HELPERS_PER_DAY,
    MAX_HELPERS,
    StartHelpersTool,
)
from loamz.server.agents.features.conversations.chat.model import ChatRequest
from loamz.server.agents.features.conversations.chat.service import chat
from loamz.server.agents.features.conversations.threads.service import threads
from loamz.server.agents.features.directory.agents.model import AgentIn
from loamz.server.agents.features.directory.agents.service import agents

ALEX = Caller(tenant_id="acme", user_id="alex", name="Alex")


class Router:
    """The parent asks for two helpers; each helper answers about its own job; the parent then writes the final answer from their reports."""

    capabilities = ScriptedModel().capabilities
    model = "router"

    def __init__(self) -> None:
        self.tools_seen: dict[str, set[str]] = {}
        self.running = 0
        self.most_at_once = 0

    def _blocks(self, messages, options):
        first = next(m.text for m in messages if m.role == Role.USER)
        names = {getattr(t, "name", "") for t in (options.tools or [])}
        if first.startswith("Research"):
            self.tools_seen["parent"] = names
            if any(isinstance(b, ToolResultBlock) for m in messages for b in m.content):
                reports = "".join(
                    b.text for m in messages for b in m.content if isinstance(b, ToolResultBlock)
                )
                return [TextBlock(text="Both are back. " + reports.replace("\n", " "))]
            return [
                ToolUseBlock(
                    call_id="h1",
                    tool_name="start_helpers",
                    arguments={
                        "jobs": [
                            {"name": "Weather", "job": "Find the weather in Lisbon."},
                            {"name": "Flights", "job": "Find flights to Lisbon."},
                        ]
                    },
                )
            ]
        self.tools_seen[first] = names
        return [TextBlock(text=f"Report: {first.split('.')[0].removeprefix('Find ')} is fine.")]

    async def generate(self, messages, *, options, ctx=None):
        return LLMResponse(
            content=self._blocks(messages, options), usage=Usage(), finish_reason=FinishReason.STOP
        )

    async def generate_stream(self, messages, *, options, ctx=None):
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            await asyncio.sleep(0.2)  # long enough for the helpers to overlap
            blocks = self._blocks(messages, options)
            for b in blocks:
                if isinstance(b, TextBlock):
                    yield TextDelta(text=b.text)
            yield CompletionEvent(content=blocks, usage=Usage(), finish_reason=FinishReason.STOP)
        finally:
            self.running -= 1


@pytest.fixture
async def world(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "f")},
            llm={"openai_api_key": "test"},
            chat={"max_concurrent_runs": 8},
        )
    )
    await container.start({"all"})
    yield container
    await container.aclose()


async def test_helpers_run_side_by_side_read_only_and_report_back(world):
    container = world
    router = Router()
    container.model_client = lambda name=None: router
    container.chat_model = lambda name=None: router
    agent = await container.get(agents).create(ALEX, AgentIn(name="Planner", role="plans trips"))
    header = await container.get(threads).create_for("acme", "alex", agent_id=agent.id)
    request = ChatRequest(thread_id=header.id, agent_id=agent.id, prompt="Research Lisbon for me.")
    service = container.get(chat)
    await service.admit(ALEX, request)
    async for _ in service.chat(ALEX, request):
        pass
    nodes = await container.agent_store.threads.session_nodes(header.id)
    final = nodes[-1].payload.text
    assert (
        "Weather" in final
        and "Flights" in final
        and "Report: the weather in Lisbon is fine." in final
    )
    assert router.most_at_once >= 2  # the two helpers really ran together
    # The agent that started them has the helpers tool; a helper has neither it nor anything that changes things.
    assert (
        "start_helpers" in router.tools_seen["parent"]
        and "change_schedules" in router.tools_seen["parent"]
    )
    helper_tools = [names for key, names in router.tools_seen.items() if key != "parent"]
    assert len(helper_tools) == 2
    for names in helper_tools:
        assert (
            "start_helpers" not in names
            and "change_schedules" not in names
            and "notes" not in names
        )
    # Their work stays out of the person's list of conversations, but is kept.
    listed = await container.get(threads).list(ALEX, archived=False, page_size=50, page_token=None)
    assert all(not t.name.startswith("Helper:") for t in listed.items)


async def test_there_is_a_limit_on_helpers_at_once_and_a_day(world):
    container = world
    agent = await container.get(agents).create(ALEX, AgentIn(name="Planner"))
    tool = StartHelpersTool(container, ALEX, agent.id)
    assert (await tool.execute(jobs=[])).is_error
    # Only the first MAX_HELPERS jobs are taken, and a person's day has a cap on how many helpers they may start.
    assert (
        len(tool.input_schema["properties"]["jobs"]["items"]["required"]) == 2 and MAX_HELPERS == 4
    )
    key = "helpers:acme:alex"
    await container.counters.add_day(key, limit=HELPERS_PER_DAY, count=HELPERS_PER_DAY)
    refused = await tool.execute(jobs=[{"name": "x", "job": "do it"}])
    assert refused.is_error and "limit" in refused.content[0].text
