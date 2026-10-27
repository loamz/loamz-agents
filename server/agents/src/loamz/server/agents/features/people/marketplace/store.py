from __future__ import annotations

import json
import time

from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.people.marketplace.model import Install

COMPONENT = "people.marketplace"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_installs (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        permissions TEXT NOT NULL,
        installed_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id, item_id)
    )
    """,
    "ALTER TABLE people_installs ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
]


class InstallStore:
    """What each person has switched on. Every statement is keyed by tenant and person, so no query reaches another's rows."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def list(self, tenant_id: str, user_id: str) -> list[Install]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT item_id, permissions, enabled, installed_at FROM people_installs WHERE tenant_id = ? AND user_id = ? ORDER BY installed_at",
                tenant_id,
                user_id,
            )
        return [
            Install(
                id=r["item_id"],
                permissions=json.loads(r["permissions"]),
                enabled=bool(r["enabled"]),
                installed_at=r["installed_at"],
            )
            for r in rows
        ]

    async def put(self, tenant_id: str, user_id: str, item: Install) -> None:
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_installs (tenant_id, user_id, item_id, permissions, enabled, installed_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, user_id, item_id) DO UPDATE SET permissions = excluded.permissions, enabled = excluded.enabled",
                tenant_id,
                user_id,
                item.id,
                json.dumps(item.permissions),
                int(item.enabled),
                time.time(),
            )

    async def remove(self, tenant_id: str, user_id: str, item_id: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM people_installs WHERE tenant_id = ? AND user_id = ? AND item_id = ?",
                tenant_id,
                user_id,
                item_id,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user_id is None:
                return await tx.execute(
                    "DELETE FROM people_installs WHERE tenant_id = ?", tenant_id
                )
            return await tx.execute(
                "DELETE FROM people_installs WHERE tenant_id = ? AND user_id = ?",
                tenant_id,
                user_id,
            )
