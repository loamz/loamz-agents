"""A real SQLite runtime, with a person and two independently deciding channel agents."""

import asyncio

from loamz.engine.agents.kernel.agents.channel import ChannelMemberAgent, ChannelMemberConfig
from loamz.engine.agents.kernel.context import ContextConfig
from loamz.engine.agents.kernel.runtime.channel import Member, ParticipantKind
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.engine.agents.testing.scripted import ScriptedModel
from tests._stores import fs_history, open_runtime


async def test_a_human_post_wakes_agents_and_only_the_answer_is_published():
    human = Actor("user", "alice")
    scout = Actor("member", "scout@team")
    quiet = Actor("member", "quiet@team")
    names = {str(human): "Alice", str(scout): "Scout", str(quiet): "Quiet"}
    async with open_runtime() as runtime:
        for actor, answer in [(scout, "Hello Alice"), (quiet, "[PASS]")]:
            await runtime.register(
                ChannelMemberAgent(
                    "member",
                    session_id=actor.key,
                    model=ScriptedModel(answer),
                    context=ContextConfig(history=fs_history()),
                    channel=ChannelMemberConfig(names=names, debounce_s=0.01),
                )
            )
        await runtime.store.channel_open(
            "team",
            members=[
                Member(agent=human, kind=ParticipantKind.HUMAN),
                Member(agent=scout),
                Member(agent=quiet),
            ],
        )
        posted = await runtime.store.channel_append("team", sender=human, text="Hello team")
        assert set(posted.woken) == {scout, quiet}
        async with asyncio.timeout(5):
            while True:
                entries = await runtime.store.channel_read("team")
                if len(entries) == 2:
                    break
                await asyncio.sleep(0.02)
        assert [(e.sender, e.text) for e in entries] == [
            (human, "Hello team"),
            (scout, "Hello Alice"),
        ]
        await runtime.store.channel_mark_delivered("team", human, 1)
        await runtime.store.channel_mark_read("team", human, 1)
        members = await runtime.store.channel_members("team")
        assert next(m for m in members if m.agent == human).delivered == 1
