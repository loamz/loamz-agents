"""Agents use the operator's tool servers in chat: only the tools named for them, and a person approves risky calls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolRisk
from loamz.runtime.testing.scripted import ScriptedModel, ToolCall
from loamz.runtime.toolkit.mcp.servers import McpToolServers
from loamz.runtime.toolkit.mcp.tool import MCPTool
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
            llm={"openai_api_key": "test-key"},  # the model itself is scripted in each test
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


async def test_every_agent_can_remember_what_the_person_shares(world):
    from loamz.server.agents.features.people.memory.service import memory

    use_model(
        world.container,
        ToolCall("memory", {"action": "remember", "value": "Prefers tea to coffee"}),
        "Noted.",
    )
    agent, header = await open_thread(world.container, [])
    text, asked = await converse(world.container, agent, header, "remember that I like tea")
    assert asked == []  # remembering is safe: it never waits for approval
    assert any("tea" in t for t in await world.container.get(memory).texts(CALLER))


async def test_an_agent_loads_the_persons_own_skill_when_it_uses_it_and_only_if_it_was_given(world):
    from loamz.server.agents.features.people.skills.model import SkillIn
    from loamz.server.agents.features.people.skills.service import skills

    own = await world.container.get(skills).create(
        CALLER,
        SkillIn(
            name="Standup",
            description="Write a standup update.",
            instructions="Three bullets only.",
        ),
    )
    given = await world.container.get(agents).create(
        CALLER, AgentIn(name="Given", allowed_tools=[], abilities=[f"skill:{own.id}"])
    )
    plain = await world.container.get(agents).create(
        CALLER, AgentIn(name="Plain", allowed_tools=[])
    )
    answers = {}
    for name, profile in (("given", given), ("plain", plain)):
        header = await world.container.get(threads).create_for("acme", "alex", agent_id=profile.id)
        use_model(
            world.container,
            ToolCall("use_skill", {"name": "Standup"}),
            lambda messages: "LOADED" if "Three bullets only." in str(messages) else "NOT LOADED",
        )
        answers[name], _ = await converse(world.container, profile, header, "standup please")
    assert '"text":"LOADED"' in answers["given"] and '"text":"NOT LOADED"' in answers["plain"]


async def test_an_agent_given_a_shipped_skill_can_load_its_full_instructions(world):
    given = await world.container.get(agents).create(
        CALLER, AgentIn(name="Reviewer", allowed_tools=[], abilities=["security-review"])
    )
    header = await world.container.get(threads).create_for("acme", "alex", agent_id=given.id)
    use_model(
        world.container,
        ToolCall("use_skill", {"name": "Security review"}),
        lambda messages: "LOADED" if "Access control" in str(messages) else "NOT LOADED",
    )
    text, _ = await converse(world.container, given, header, "review my login code")
    assert '"text":"LOADED"' in text


async def test_an_agent_gets_exactly_the_built_in_tools_it_was_given(world, monkeypatch):
    from loamz.server.agents.features.conversations.chat.lookups import CurrencyTool

    async def rate(self, **_):
        return "RATE 42"

    monkeypatch.setattr(CurrencyTool, "run", rate)
    calc = await world.container.get(agents).create(
        CALLER, AgentIn(name="Counter", allowed_tools=[], abilities=["currency"])
    )
    plain = await world.container.get(agents).create(CALLER, AgentIn(name="Bare", allowed_tools=[]))
    answers = {}
    for name, profile in (("calc", calc), ("plain", plain)):
        header = await world.container.get(threads).create_for("acme", "alex", agent_id=profile.id)
        use_model(
            world.container,
            ToolCall("currency", {"from": "USD", "to": "EUR"}),
            lambda messages: "GOT 42" if "RATE 42" in str(messages) else "NO RESULT",
        )
        answers[name], _ = await converse(
            world.container, profile, header, "what is a dollar in euros"
        )
    assert '"text":"GOT 42"' in answers["calc"] and '"text":"NO RESULT"' in answers["plain"]


async def test_every_agent_can_tell_the_time(world):
    plain = await world.container.get(agents).create(
        CALLER, AgentIn(name="Clock", allowed_tools=[])
    )
    header = await world.container.get(threads).create_for("acme", "alex", agent_id=plain.id)
    use_model(
        world.container,
        ToolCall("get_current_time", {"timezone": "Asia/Kolkata"}),
        lambda messages: "IST" if "IST" in str(messages) else "NO TIME",
    )
    text, _ = await converse(world.container, plain, header, "what time is it in India")
    assert '"text":"IST"' in text


# ── drafting an agent from a sentence ──────────────────────────────────────────
async def test_a_sentence_becomes_a_draft_with_only_abilities_that_exist(world):
    from loamz.runtime.testing.scripted import ScriptedModel
    from loamz.server.agents.features.directory.agents.draft import draft_agent

    seen = {}

    def reply(messages):
        seen["prompt"] = messages[-1].text
        return json.dumps(
            {
                "name": "  Inbox  ",
                "role": "Sorts new email into act now, later and ignore",
                "instructions": "1. Read each new message.\n2. Label it. Never send or delete anything without asking.",
                "abilities": ["todoist", "web-search", "invented-thing", "todoist"],
                "missing": ["Gmail", " "],
            }
        )

    draft = await draft_agent(
        ScriptedModel(reply),
        "an email triage agent",
        {"todoist": "Todoist: tasks", "web-search": "Web search: look things up"},
    )
    assert draft.name == "Inbox" and draft.abilities == [
        "todoist",
        "web-search",
    ]  # nothing invented, no repeats
    assert draft.missing == ["Gmail"] and "Never send or delete" in draft.instructions
    assert (
        "an email triage agent" in seen["prompt"] and "- todoist: Todoist: tasks" in seen["prompt"]
    )


async def test_a_draft_keeps_at_most_two_apps_and_prefers_the_connected_ones(world):
    import json

    from loamz.runtime.testing.scripted import ScriptedModel
    from loamz.server.agents.features.directory.agents.draft import draft_agent

    reply = json.dumps(
        {
            "name": "Schedule",
            "role": "r",
            "instructions": "i",
            "abilities": ["calendly", "cal-com", "web-search", "fastmail", "todoist"],
        }
    )
    offered = {k: k for k in ("calendly", "cal-com", "web-search", "fastmail", "todoist")}
    apps = {"calendly": False, "cal-com": False, "fastmail": False, "todoist": True}
    draft = await draft_agent(
        ScriptedModel(lambda m: reply), "a daily schedule assistant", offered, apps
    )
    assert draft.abilities == [
        "calendly",
        "web-search",
        "todoist",
    ]  # the model's order; tools are not apps


async def test_a_draft_that_is_not_json_or_has_no_name_is_refused_kindly(world):
    from loamz.engine.errors import InvalidRequestError
    from loamz.runtime.testing.scripted import ScriptedModel
    from loamz.server.agents.features.directory.agents.draft import draft_agent

    for text in ("sorry, I cannot", '{"name": "", "role": "x"}'):
        with pytest.raises(InvalidRequestError):
            await draft_agent(ScriptedModel(lambda m, t=text: t), "an email triage agent", {})
    with pytest.raises(InvalidRequestError):
        await draft_agent(ScriptedModel(lambda m: "{}"), "  ", {})


async def test_drafting_through_the_service_offers_the_catalog_and_the_persons_own_skills(
    world, monkeypatch
):
    from loamz.runtime.testing.scripted import ScriptedModel
    from loamz.server.agents.features.people.skills.model import SkillIn
    from loamz.server.agents.features.people.skills.service import skills

    asked = {}

    def reply(messages):
        asked["prompt"] = messages[-1].text
        return json.dumps(
            {
                "name": "Triage",
                "role": "r",
                "instructions": "i",
                "abilities": ["gmail", "code-interpreter"],
            }
        )

    monkeypatch.setattr(world.container, "chat_model", lambda *a: ScriptedModel(reply))
    await world.container.get(skills).create(
        CALLER, SkillIn(name="Our tone", description="How we write.", instructions="Be warm.")
    )
    draft = await world.container.get(agents).draft(CALLER, "an email triage agent")
    assert (
        draft.abilities == []
    )  # Gmail cannot be connected yet and the code interpreter comes with every agent
    assert (
        "- web-search:" in asked["prompt"]
        and "Our tone (the person's own skill)" in asked["prompt"]
    )
    assert "- gmail:" not in asked["prompt"] and "- code-interpreter:" not in asked["prompt"]
