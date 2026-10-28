"""Rebuild a channel member from saved membership, so queued wakes survive server restarts."""

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.agents.channel import ChannelMemberConfig
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools.approval import DurableApproval
from loamz.runtime.kernel.types.identity import Actor
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.directory.agents.service import agents
from loamz.server.agents.features.messaging.chats.service import chats, member_actor


async def build_member(container: Container, actor: Actor):
    agent_id, sep, chat_id = actor.key.rpartition("@")
    if not sep:
        raise NotFoundError("Invalid member address")
    service = container.get(chats)
    roster = await service._store.members(chat_id)
    member = next((m for m in roster if m.member == f"agent:{agent_id}"), None)
    if member is None:
        raise NotFoundError("Agent is no longer in the chat")
    caller = Caller(tenant_id=member.tenant, user_id=member.owner)
    directory = container.get(agents)
    profile = await directory.get_owned(caller, agent_id)
    # What the agent has in a conversation of its own, here too. The tool-server and data tools it was named for go through its allow-list;
    # everything else it has been given (built-ins, the code tool, the date and time, skills, connectors, memory) is added after it, because
    # the allow-list only ever speaks of tool-server tools. Nobody can be asked to approve a call from inside a group, so what asks first is left out.
    tools = list(container.tool_servers.tools(profile.allowed_tools or []))
    for name in profile.allowed_tools or []:
        if name in container.data_tool_set:
            tools.append(container.data_tool_set[name])
    from loamz.runtime.kernel.tools import ToolRisk
    from loamz.server.agents.features.conversations.chat.builtin import current_time, tools_for
    from loamz.server.agents.features.people.connections.service import connections
    from loamz.server.agents.features.people.marketplace.service import marketplace
    from loamz.server.agents.features.people.skills.service import UseSkillTool
    from loamz.server.agents.features.people.skills.service import skills as skills_feature

    market = container.get(marketplace)
    only = set(profile.abilities)
    extra: list = []
    if container.code_tool:  # every agent can run code on its own files
        extra.append(container.code_tool)
    extra.extend(
        t
        for t in tools_for(container, await market.tool_choices(caller, only))
        if getattr(t, "risk", None) is ToolRisk.SAFE
    )
    extra.append(current_time())
    extra.extend(
        await container.get(connections).tools(caller, only, profile.tool_access, asking=False)
    )
    own = container.get(skills_feature)
    skill_lines = [*await market.skill_lines(caller, only), *await own.lines(caller, only)]
    skills = ""
    if skill_lines:
        from loamz.server.agents.features.people.marketplace.service import SKILLS_INTRO

        skills = SKILLS_INTRO + "\n".join(skill_lines)

        async def load(name: str) -> str | None:
            return await market.skill_instructions(caller, name, only) or await own.instructions_of(
                caller, name, only
            )

        extra.append(UseSkillTool(load))
    names = {str(member_actor(m.member, chat_id)): m.name for m in roster}
    return directory.build_member(
        caller,
        profile,
        tools=tools,
        extra_tools=extra,
        name_of=lambda t: t.name,
        name="member",
        session_id=actor.key,
        extra_instructions=skills
        + (
            "\nYou are a visible AI participant in a shared chat. Reply when addressed or when you can help. "
            "If the conversation needs no answer, reply with exactly [PASS]. "
            "Do not repeat another participant's answer."
        ),
        channel=ChannelMemberConfig(
            names=names,
            scope={"tenant_id": member.tenant, "user_id": member.owner},
            triage=container.chat_model(),
        ),
        approval_handler=DurableApproval(),
    )


def register_members(container: Container) -> None:
    async def activate(actor: Actor):
        return await build_member(container, actor)

    # Profiles and rosters can change in a different process. Rebuild at lease time so tool permissions are current.
    container.runtime.register_factory("member", activate, cache=False)
