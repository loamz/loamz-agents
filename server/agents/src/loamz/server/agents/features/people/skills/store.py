from __future__ import annotations

import time
from datetime import UTC, datetime

from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.people.skills.model import SkillOut

COMPONENT = "people.skills"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_skills (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        instructions TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id, id)
    )
    """,
]
_COLUMNS = "id, name, description, instructions, enabled, created_at"


def _out(row) -> SkillOut:  # noqa: ANN001
    return SkillOut(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        instructions=row["instructions"],
        enabled=bool(row["enabled"]),
        created_at=datetime.fromtimestamp(row["created_at"], UTC),
    )


class SkillStore:
    """A person's own skills. Every statement is keyed by tenant and person, so no query reaches another's rows."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def list(self, tenant_id: str, user_id: str) -> list[SkillOut]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS} FROM people_skills WHERE tenant_id = ? AND user_id = ? ORDER BY created_at, id",
                tenant_id,
                user_id,
            )
        return [_out(r) for r in rows]

    async def get(self, tenant_id: str, user_id: str, skill_id: str) -> SkillOut | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                f"SELECT {_COLUMNS} FROM people_skills WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant_id,
                user_id,
                skill_id,
            )
        return None if row is None else _out(row)

    async def put(self, tenant_id: str, user_id: str, skill: SkillOut) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_skills (tenant_id, user_id, id, name, description, instructions, enabled, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (tenant_id, user_id, id) DO UPDATE SET "
                "name = excluded.name, description = excluded.description, instructions = excluded.instructions, enabled = excluded.enabled",
                tenant_id,
                user_id,
                skill.id,
                skill.name,
                skill.description,
                skill.instructions,
                int(skill.enabled),
                skill.created_at.timestamp() if skill.created_at else time.time(),
            )

    async def delete(self, tenant_id: str, user_id: str, skill_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM people_skills WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant_id,
                user_id,
                skill_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute("DELETE FROM people_skills WHERE tenant_id = ?", tenant_id)
            return await tx.execute(
                "DELETE FROM people_skills WHERE tenant_id = ? AND user_id = ?", tenant_id, user_id
            )
