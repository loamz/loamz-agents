"""Chat membership is the authorization boundary, including for direct conversations."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from loamz.engine.sql import Database, migrate
from loamz.runtime.kernel.types.ids import new_id

SCHEMA = [
    """
CREATE TABLE chats (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL, e2ee INTEGER NOT NULL DEFAULT 0,
    avatar_key TEXT, token_cap BIGINT NOT NULL DEFAULT 0, budget_usd DOUBLE PRECISION,
    breaker INTEGER NOT NULL DEFAULT 40,
    created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE chat_members (
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    member TEXT NOT NULL, tenant TEXT NOT NULL, name TEXT NOT NULL,
    owner TEXT NOT NULL, mode TEXT NOT NULL, role TEXT NOT NULL,
    PRIMARY KEY (chat_id, member)
);
CREATE INDEX chat_members_lookup ON chat_members (tenant, member, chat_id);
""",
    # Its icon and colour when it has no photo (a JSON object: {"icon": ..., "color": ...}).
    "ALTER TABLE chats ADD COLUMN look TEXT;",
]


@dataclass(frozen=True)
class ChatRecord:
    id: str
    tenant_id: str
    user_id: str
    name: str
    kind: str
    e2ee: int
    avatar_key: str | None
    look: str | None
    token_cap: int
    budget_usd: float | None
    breaker: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class MemberRecord:
    chat_id: str
    member: str
    tenant: str
    name: str
    owner: str
    mode: str
    role: str


def direct_id(members: list[MemberRecord]) -> str:
    """Length-delimited identities include each participant's tenant, without one owning org in the id."""
    import json

    identities = sorted((m.tenant, m.member) for m in members)
    return (
        "direct-"
        + hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()
    )


class ChatsStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ready = False

    async def _init(self) -> None:
        if not self._ready:
            await migrate(self._db, "messaging.chats", SCHEMA)
            self._ready = True

    async def list(self, tenant: str, user: str) -> list[ChatRecord]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT c.* FROM chats c JOIN chat_members m ON m.chat_id = c.id "
                "WHERE m.tenant = ? AND m.member = ? ORDER BY c.updated_at DESC, c.id",
                tenant,
                f"user:{user}",
            )
        return [ChatRecord(**dict(r)) for r in rows]

    async def get(self, tenant: str, user: str, chat: str) -> ChatRecord | None:
        await self._init()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT c.* FROM chats c JOIN chat_members m ON m.chat_id = c.id "
                "WHERE c.id = ? AND m.tenant = ? AND m.member = ?",
                chat,
                tenant,
                f"user:{user}",
            )
        return ChatRecord(**dict(row)) if row else None

    async def create(
        self, tenant: str, user: str, name: str, kind: str, members: list[MemberRecord]
    ) -> str:
        await self._init()
        chat = direct_id(members) if kind == "direct" else new_id()
        now = time.time()
        async with self._db.transaction() as tx:
            inserted = await tx.execute(
                "INSERT INTO chats (id, tenant_id, user_id, name, kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                chat,
                tenant,
                user,
                name,
                kind,
                now,
                now,
            )
            if inserted:
                for m in members:
                    await tx.execute(
                        "INSERT INTO chat_members (chat_id, member, tenant, name, owner, mode, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        chat,
                        m.member,
                        m.tenant,
                        m.name,
                        m.owner,
                        m.mode,
                        m.role,
                    )
        return chat

    async def members(self, chat: str) -> list[MemberRecord]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT * FROM chat_members WHERE chat_id = ? ORDER BY member", chat
            )
        return [MemberRecord(**dict(r)) for r in rows]

    async def add(self, member: MemberRecord) -> None:
        await self._init()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO chat_members (chat_id, member, tenant, name, owner, mode, role) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (chat_id, member) DO UPDATE SET mode = excluded.mode, role = excluded.role",
                member.chat_id,
                member.member,
                member.tenant,
                member.name,
                member.owner,
                member.mode,
                member.role,
            )

    async def remove(self, chat: str, member: str) -> None:
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM chat_members WHERE chat_id = ? AND member = ?", chat, member
            )

    async def update(self, chat: str, values: dict[str, object]) -> None:
        allowed = {"name", "avatar_key", "look", "token_cap", "budget_usd", "breaker"}
        if not values.keys() <= allowed:
            raise ValueError("Unknown chat field")
        async with self._db.transaction() as tx:
            for key, value in values.items():
                await tx.execute(f"UPDATE chats SET {key} = ? WHERE id = ?", value, chat)
            await tx.execute("UPDATE chats SET updated_at = ? WHERE id = ?", time.time(), chat)

    async def delete(self, chat: str) -> None:
        async with self._db.transaction() as tx:
            await tx.execute("DELETE FROM chat_members WHERE chat_id = ?", chat)
            await tx.execute("DELETE FROM chats WHERE id = ?", chat)
