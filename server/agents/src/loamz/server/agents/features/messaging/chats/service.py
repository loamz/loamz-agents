"""People and agents share one channel log; membership is checked before every operation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.runtime.channel import (
    ChannelEntry,
    Member,
    Mode,
    ParticipantKind,
    mentions_in,
)
from loamz.runtime.kernel.types.identity import Actor
from loamz.runtime.kernel.types.supervision import ExecutionBudget
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.directory.agents.service import agents
from loamz.server.agents.features.directory.pins.service import pins
from loamz.server.agents.features.messaging.chats.model import (
    GroupIn,
    GroupList,
    GroupOut,
    GroupPatch,
    MemberIn,
    MemberOut,
    MemberPatch,
    MessageList,
    MessageOut,
    Reaction,
    SendMessageIn,
)
from loamz.server.agents.features.messaging.chats.store import ChatRecord, ChatsStore, MemberRecord


def member_actor(member: str, chat: str) -> Actor:
    kind, key = member.split(":", 1)
    return Actor("user", key) if kind == "user" else Actor("member", f"{key}@{chat}")


class ChatsService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._store = ChatsStore(container.database)
        self._channels = container.runtime.store

    def _channel_name(self, chat: str) -> str:
        return f"group/{chat}"

    async def require(self, caller: Caller, chat: str, *, admin: bool = False) -> ChatRecord:
        row = await self._store.get(caller.tenant_id, caller.user_id, chat)
        if row is None:
            raise NotFoundError("Chat not found")
        if admin:
            roster = await self._store.members(chat)
            if not any(
                m.member == f"user:{caller.user_id}"
                and m.tenant == caller.tenant_id
                and m.role == "admin"
                for m in roster
            ):
                raise NotFoundError("Chat not found")
        return row

    async def _resolve(self, caller: Caller, body: MemberIn, chat: str = "") -> MemberRecord:
        kind, key = body.member.split(":", 1)
        if kind == "agent":
            profile = await self._c.get(agents).get_owned(caller, key)
            name, owner = profile.name, profile.user_id
        elif key == caller.user_id:
            name, owner = caller.name or key, key
        else:
            person = await asyncio.to_thread(self._c.directory.user, caller.tenant_id, key)
            name = (
                " ".join(filter(None, (person.get("firstName"), person.get("lastName"))))
                or person.get("username")
                or key
            )
            owner = key
        return MemberRecord(chat, body.member, caller.tenant_id, name, owner, body.mode, body.role)

    async def _mirror(self, chat: str, m: MemberRecord) -> None:
        actor = member_actor(m.member, chat)
        human = m.member.startswith("user:")
        await self._channels.channel_set_member(
            self._channel_name(chat),
            Member(
                agent=actor,
                kind=ParticipantKind.HUMAN if human else ParticipantKind.AGENT,
                inbox=None if human else actor,
                mode=Mode(m.mode),
            ),
        )
        if not human:
            self._c.runtime.forget(actor)

    async def _working(self, chat: str, roster: list[MemberRecord]) -> list[str]:
        names = {
            member_actor(m.member, chat): m.name for m in roster if m.member.startswith("agent:")
        }
        return [names[a] for a in await self._channels.working(list(names))]

    async def _out(self, caller: Caller, row: ChatRecord) -> GroupOut:
        roster = await self._store.members(row.id)
        channel = self._channel_name(row.id)
        cursors = {m.agent: m for m in await self._channels.channel_members(channel)}
        members = []
        for m in roster:
            human = m.member.startswith("user:")
            actor = member_actor(m.member, row.id)
            cursor = cursors.get(actor)
            spend = await self._channels.account_spend(f"agent:{actor}") if not human else None
            members.append(
                MemberOut(
                    member=m.member,
                    kind="human" if human else "agent",
                    tenant=m.tenant,
                    user_id=m.member[5:] if human else None,
                    agent_id=m.member[6:] if not human else None,
                    name=m.name,
                    role=m.role,
                    mode=m.mode,
                    read=cursor.cursor if cursor else -1,
                    delivered=cursor.delivered if cursor else -1,
                    tokens_used=spend.tokens if spend else 0,
                    cost_usd=spend.cost_usd if spend else 0,
                )
            )
        heads = await self._channels.channel_heads([channel], Actor("user", caller.user_id))
        head = heads[0] if heads else None
        spend = await self._channels.account_spend(f"channel:{channel}")
        async with self._c.database.transaction() as tx:
            state = await tx.fetchone("SELECT paused FROM rt_channels WHERE channel = ?", channel)
        pinned = await self._c.get(pins).pinned_at(caller, "group")
        return GroupOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            e2ee=False,
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            updated_at=datetime.fromtimestamp(row.updated_at, UTC),
            members=members,
            last_message=head.latest.text if head and head.latest else None,
            last_sender=(
                None
                if not head or not head.latest
                else "You"
                if head.latest.sender == Actor("user", caller.user_id)
                else next(
                    (
                        m.name
                        for m in roster
                        if member_actor(m.member, row.id) == head.latest.sender
                    ),
                    head.latest.sender.key,
                )
            ),
            unread=head.unread if head else 0,
            paused=bool(state and state["paused"]),
            working=await self._working(row.id, roster),
            tokens_used=spend.tokens,
            cost_usd=spend.cost_usd,
            token_cap=row.token_cap,
            budget_usd=row.budget_usd,
            breaker=row.breaker,
            avatar=row.avatar_key,
            pinned_at=pinned.get(row.id),
        )

    async def list(self, caller: Caller) -> GroupList:
        return GroupList(
            items=[
                await self._out(caller, r)
                for r in await self._store.list(caller.tenant_id, caller.user_id)
            ]
        )

    async def get(self, caller: Caller, chat: str) -> GroupOut:
        return await self._out(caller, await self.require(caller, chat))

    async def create(self, caller: Caller, body: GroupIn) -> GroupOut:
        roster = [
            await self._resolve(caller, m)
            for m in body.members
            if m.member != f"user:{caller.user_id}"
        ]
        roster.append(
            await self._resolve(caller, MemberIn(member=f"user:{caller.user_id}", role="admin"))
        )
        if body.kind == "direct" and (
            len(roster) != 2 or any(not m.member.startswith("user:") for m in roster)
        ):
            raise InvalidRequestError("A direct chat needs exactly two people")
        chat = await self._store.create(
            caller.tenant_id, caller.user_id, body.name, body.kind, roster
        )
        await self._channels.channel_open(self._channel_name(chat), tenant=caller.tenant_id)
        for m in await self._store.members(chat):
            await self._mirror(chat, m)
        return await self.get(caller, chat)

    async def patch(self, caller: Caller, chat: str, body: GroupPatch) -> GroupOut:
        await self.require(caller, chat, admin=True)
        values = body.model_dump(exclude_unset=True)
        if any(v is None for k, v in values.items() if k != "budget_usd"):
            raise InvalidRequestError("Chat settings cannot be null")
        if values.get("budget_usd") == 0:
            values["budget_usd"] = None
        await self._store.update(chat, values)
        row = await self.require(caller, chat)
        await self._channels.channel_configure(self._channel_name(chat), breaker=row.breaker)
        await self._channels.account_limit(
            f"channel:{self._channel_name(chat)}",
            ExecutionBudget(max_tokens=row.token_cap or None, max_cost_usd=row.budget_usd),
        )
        return await self.get(caller, chat)

    async def delete(self, caller: Caller, chat: str) -> None:
        await self.require(caller, chat, admin=True)
        await self._channels.channel_delete(self._channel_name(chat))
        await self._store.delete(chat)

    async def set_avatar(self, caller: Caller, chat: str, data: bytes) -> GroupOut:
        await self.require(caller, chat, admin=True)
        if len(data) > 5 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidRequestError("Use a PNG picture under 5 MiB")
        key = f"tenants/{caller.tenant_id}/groups/{chat}/avatar.png"
        await self._c.file_store.upload(key, data, content_type="image/png")
        await self._store.update(chat, {"avatar_key": key})
        return await self.get(caller, chat)

    async def clear_avatar(self, caller: Caller, chat: str) -> GroupOut:
        await self.require(caller, chat, admin=True)
        await self._store.update(chat, {"avatar_key": None})
        return await self.get(caller, chat)

    async def pin(self, caller: Caller, chat: str, pinned: bool) -> GroupOut:
        await self.require(caller, chat)
        await self._c.get(pins).set_pinned(caller, "group", chat, pinned)
        return await self.get(caller, chat)

    async def add_member(self, caller: Caller, chat: str, body: MemberIn) -> GroupOut:
        row = await self.require(caller, chat, admin=True)
        if row.kind == "direct":
            raise InvalidRequestError("Create a group to add more people")
        member = await self._resolve(caller, body, chat)
        if any(m.member == member.member for m in await self._store.members(chat)):
            raise InvalidRequestError("Already a member")
        await self._store.add(member)
        await self._mirror(chat, member)
        return await self.get(caller, chat)

    async def change_member(
        self, caller: Caller, chat: str, address: str, body: MemberPatch
    ) -> GroupOut:
        await self.require(caller, chat, admin=True)
        roster = await self._store.members(chat)
        member = next((m for m in roster if m.member == address), None)
        if member is None:
            raise NotFoundError("Member not found")
        if (
            member.role == "admin"
            and body.role == "member"
            and sum(m.role == "admin" for m in roster) == 1
        ):
            raise InvalidRequestError("Keep at least one admin")
        changed = replace(member, **body.model_dump(exclude_none=True))
        await self._store.add(changed)
        await self._mirror(chat, changed)
        return await self.get(caller, chat)

    async def remove_member(self, caller: Caller, chat: str, address: str) -> GroupOut:
        row = await self.require(caller, chat, admin=True)
        roster = await self._store.members(chat)
        member = next((m for m in roster if m.member == address), None)
        if member is None:
            raise NotFoundError("Member not found")
        if row.kind == "direct" or (
            member.role == "admin" and sum(m.role == "admin" for m in roster) == 1
        ):
            raise InvalidRequestError("Keep the chat's participants and at least one admin")
        await self._channels.channel_remove_member(
            self._channel_name(chat), member_actor(address, chat)
        )
        await self._store.remove(chat, address)
        self._c.runtime.forget(member_actor(address, chat))
        return await self.get(caller, chat)

    async def _entry(self, caller: Caller, chat: str, entry: ChannelEntry) -> MessageOut:
        roster = await self._store.members(chat)
        names = {member_actor(m.member, chat): m.name for m in roster}
        reactions = await self._channels.channel_reactions(self._channel_name(chat), [entry.seq])
        by_address = {str(a): (a, name) for a, name in names.items()}
        reacted = []
        for address, emoji in reactions.get(entry.seq, {}).items():
            actor, name = by_address.get(address, (Actor("unknown", address), address))
            reacted.append(
                Reaction(
                    emoji=emoji,
                    sender_id=actor.key,
                    sender=name,
                    from_user=actor == Actor("user", caller.user_id),
                )
            )
        return MessageOut(
            seq=entry.seq,
            id=entry.id,
            sender_id=entry.sender.key,
            sender=names.get(entry.sender, entry.sender.key),
            sender_kind="human"
            if entry.sender.type == "user"
            else "agent"
            if entry.sender.type == "member"
            else "system",
            from_user=entry.sender == Actor("user", caller.user_id),
            kind=entry.kind,
            text=entry.text,
            mentions=[names.get(Actor.from_str(a), a) if "/" in a else a for a in entry.mentions],
            reply_to=entry.reply_to,
            attachments=entry.data.get("attachments", []),
            at=entry.at,
            edited_at=entry.edited_at,
            deleted_at=entry.deleted_at,
            reactions=reacted,
            ui=entry.data.get("ui"),
        )

    async def read_messages(
        self, caller: Caller, chat: str, *, after: int = -1, wait: float = 0
    ) -> MessageList:
        await self.require(caller, chat)
        channel = self._channel_name(chat)
        if wait:
            await self._channels.channel_wait(channel, after, wait)
            await self.require(caller, chat)
        roster = await self._store.members(chat)
        runtime_members = await self._channels.channel_members(channel)
        own = Actor("user", caller.user_id)
        cursor = next((m for m in runtime_members if m.agent == own), None)
        if cursor is None:
            raise NotFoundError("Chat membership is unavailable")
        entries = await self._channels.channel_read(
            channel, after=max(after, cursor.joined_seq - 1), limit=200
        )
        people = [m for m in runtime_members if m.kind == ParticipantKind.HUMAN and m.agent != own]
        return MessageList(
            entries=[await self._entry(caller, chat, e) for e in entries],
            latest=entries[-1].seq if entries else after,
            working=await self._working(chat, roster),
            read_by_all=min((m.cursor for m in people), default=-1),
            read_by={m.agent.key: m.cursor for m in people},
            delivered_by={m.agent.key: m.delivered for m in people},
        )

    async def send_message(self, caller: Caller, chat: str, body: SendMessageIn) -> MessageOut:
        await self.require(caller, chat)
        if not body.text.strip() and not body.attachments:
            raise InvalidRequestError("Write a message or attach a file")
        prefix = f"tenants/{caller.tenant_id}/groups/{chat}/files/"
        if any(
            not a.key.startswith(prefix) or "/../" in a.key or "\\" in a.key
            for a in body.attachments
        ):
            raise InvalidRequestError("Attachments must belong to this chat")
        if body.reply_to is not None:
            await self._target(caller, chat, body.reply_to)
        roster = await self._store.members(chat)
        names = {str(member_actor(m.member, chat)): m.name for m in roster}
        result = await self._channels.channel_append(
            self._channel_name(chat),
            sender=Actor("user", caller.user_id),
            text=body.text,
            mentions=mentions_in(body.text, names),
            reply_to=body.reply_to,
            data={"attachments": [a.model_dump() for a in body.attachments]},
        )
        if result.seq is None:
            raise InvalidRequestError("Message was not accepted")
        await self._store.update(chat, {})
        return await self._at(caller, chat, result.seq)

    async def _at(self, caller: Caller, chat: str, seq: int) -> MessageOut:
        entries = await self._channels.channel_read(
            self._channel_name(chat), after=seq - 1, limit=1
        )
        if not entries or entries[0].seq != seq:
            raise NotFoundError("Message not found")
        return await self._entry(caller, chat, entries[0])

    async def _target(self, caller: Caller, chat: str, seq: int) -> None:
        await self.require(caller, chat)
        members = await self._channels.channel_members(self._channel_name(chat))
        own = next((m for m in members if m.agent == Actor("user", caller.user_id)), None)
        if own is None or seq < own.joined_seq:
            raise NotFoundError("Message not found")
        await self._at(caller, chat, seq)

    async def edit_message(self, caller: Caller, chat: str, seq: int, text: str) -> MessageOut:
        await self._target(caller, chat, seq)
        marker = await self._channels.channel_edit(
            self._channel_name(chat), seq, Actor("user", caller.user_id), text
        )
        if marker is None:
            raise NotFoundError("Message not found")
        return await self._at(caller, chat, marker)

    async def delete_message(self, caller: Caller, chat: str, seq: int) -> MessageOut:
        await self._target(caller, chat, seq)
        marker = await self._channels.channel_tombstone(
            self._channel_name(chat), seq, Actor("user", caller.user_id)
        )
        if marker is None:
            raise NotFoundError("Message not found")
        return await self._at(caller, chat, marker)

    async def react(self, caller: Caller, chat: str, seq: int, emoji: str) -> MessageOut:
        await self._target(caller, chat, seq)
        marker = await self._channels.channel_react(
            self._channel_name(chat), seq, Actor("user", caller.user_id), emoji
        )
        if marker is None:
            raise NotFoundError("Message not found")
        return await self._at(caller, chat, marker)

    async def mark_read(
        self, caller: Caller, chat: str, upto: int, *, delivered: bool = False
    ) -> None:
        await self.require(caller, chat)
        latest = await self._channels.channel_last(self._channel_name(chat))
        if upto > (latest[-1].seq if latest else -1):
            raise InvalidRequestError("Cursor is beyond this chat")
        fn = (
            self._channels.channel_mark_delivered if delivered else self._channels.channel_mark_read
        )
        await fn(self._channel_name(chat), Actor("user", caller.user_id), upto)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        # Shared histories need the same participant erasure as the runtime, not owner-only deletion.
        await self._store._init()
        async with self._c.database.transaction() as tx:
            if user_id is None:
                rows = await tx.fetchall("SELECT id FROM chats WHERE tenant_id = ?", tenant_id)
            else:
                rows = await tx.fetchall(
                    "SELECT chat_id AS id FROM chat_members WHERE tenant = ? AND member = ?",
                    tenant_id,
                    f"user:{user_id}",
                )
        for row in rows:
            chat = str(row["id"])
            if user_id is None:
                await self._channels.channel_delete(self._channel_name(chat))
                await self._store.delete(chat)
            else:
                await self._channels.channel_remove_member(
                    self._channel_name(chat), Actor("user", user_id)
                )
                await self._store.remove(chat, f"user:{user_id}")
        return len(rows)


def chats(container: Container) -> ChatsService:
    return ChatsService(container)
