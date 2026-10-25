"""Rebuild a channel member from saved membership, so queued wakes survive server restarts."""

from loamz.engine.agents.kernel.agents.channel import ChannelMemberConfig
from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.tools.approval import DurableApproval
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.engine.errors import NotFoundError
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
    tools = list(container.tool_servers.tools(profile.allowed_tools or []))
    for name in profile.allowed_tools or []:
        if name in container.data_tool_set:
            tools.append(container.data_tool_set[name])
        if name == "code_interpreter" and container.code_tool:
            tools.append(container.code_tool)
    names = {str(member_actor(m.member, chat_id)): m.name for m in roster}
    return directory.build_member(
        caller,
        profile,
        tools=tools,
        name_of=lambda t: t.name,
        name="member",
        session_id=actor.key,
        extra_instructions=(
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
