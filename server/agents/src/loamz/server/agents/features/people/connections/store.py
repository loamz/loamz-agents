from __future__ import annotations

import json
import time

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from loamz.engine.sql import Database, migrate
from loamz.runtime.kernel.runtime.sealing import PlainSealer, Sealer

COMPONENT = "people.connections"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_connections (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        client_info TEXT,
        tokens TEXT,
        status TEXT NOT NULL,
        connected_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id, item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS people_custom_connectors (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        secret TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, user_id, item_id)
    )
    """,
]


class ConnectionStore:
    """Each person's tokens for each connector. Tokens are sealed with the server's keys and keyed by tenant and person, so no
    query reaches another person's row; erasing an account or an organization removes them with the rest."""

    def __init__(self, database: Database, sealer: Sealer | None = None) -> None:
        self._db = database
        self._sealer = sealer or PlainSealer()
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    def _scope(self, tenant: str, user: str, item: str) -> str:
        return f"connection:{tenant}:{user}:{item}"

    async def _put(self, tenant: str, user: str, item: str, column: str, value: str | None) -> None:
        await self._ensure()
        sealed = (
            None
            if value is None
            else self._sealer.seal(self._scope(tenant, user, item), column, value)
        )
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_connections (tenant_id, user_id, item_id, status, connected_at) "
                "VALUES (?, ?, ?, 'pending', ?) ON CONFLICT (tenant_id, user_id, item_id) DO NOTHING",
                tenant,
                user,
                item,
                time.time(),
            )
            await tx.execute(
                f"UPDATE people_connections SET {column} = ? WHERE tenant_id = ? AND user_id = ? AND item_id = ?",  # noqa: S608 - column is one of two constants
                sealed,
                tenant,
                user,
                item,
            )

    async def _get(self, tenant: str, user: str, item: str, column: str) -> str | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                f"SELECT {column} FROM people_connections WHERE tenant_id = ? AND user_id = ? AND item_id = ?",  # noqa: S608
                tenant,
                user,
                item,
            )
        if row is None or row[column] is None:
            return None
        return self._sealer.open(self._scope(tenant, user, item), column, row[column])

    async def set_status(self, tenant: str, user: str, item: str, status: str) -> None:
        """Record where a connection stands. A server that needs no sign-in has no tokens and so no row yet: it gets one here."""
        await self._ensure()
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_connections (tenant_id, user_id, item_id, status, connected_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, user_id, item_id) DO UPDATE SET status = excluded.status, connected_at = excluded.connected_at",
                tenant,
                user,
                item,
                status,
                time.time(),
            )

    async def list(self, tenant: str, user: str) -> list[dict[str, object]]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT item_id, status, connected_at FROM people_connections WHERE tenant_id = ? AND user_id = ? ORDER BY connected_at",
                tenant,
                user,
            )
        return [
            {"id": r["item_id"], "status": r["status"], "connected_at": r["connected_at"]}
            for r in rows
        ]

    async def add_custom(
        self, tenant: str, user: str, item: str, name: str, url: str, secret: str | None
    ) -> None:
        """A connector this person added by address. The access token they gave (if any) is sealed like the sign-in tokens."""
        await self._ensure()
        sealed = (
            None
            if not secret
            else self._sealer.seal(self._scope(tenant, user, item), "secret", secret)
        )
        async with self._db.transaction() as tx:
            await tx.execute(
                "INSERT INTO people_custom_connectors (tenant_id, user_id, item_id, name, url, secret, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                tenant,
                user,
                item,
                name,
                url,
                sealed,
                time.time(),
            )

    async def customs(self, tenant: str, user: str) -> list[dict[str, str]]:
        await self._ensure()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT item_id, name, url FROM people_custom_connectors WHERE tenant_id = ? AND user_id = ? ORDER BY created_at",
                tenant,
                user,
            )
        return [{"id": r["item_id"], "name": r["name"], "url": r["url"]} for r in rows]

    async def custom_secret(self, tenant: str, user: str, item: str) -> str | None:
        await self._ensure()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT secret FROM people_custom_connectors WHERE tenant_id = ? AND user_id = ? AND item_id = ?",
                tenant,
                user,
                item,
            )
        if row is None or row["secret"] is None:
            return None
        return self._sealer.open(self._scope(tenant, user, item), "secret", row["secret"])

    async def remove_custom(self, tenant: str, user: str, item: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM people_custom_connectors WHERE tenant_id = ? AND user_id = ? AND item_id = ?",
                tenant,
                user,
                item,
            )

    async def has_tokens(self, tenant: str, user: str, item: str) -> bool:
        return await self._get(tenant, user, item, "tokens") is not None

    async def remove(self, tenant: str, user: str, item: str) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            return await tx.execute(
                "DELETE FROM people_connections WHERE tenant_id = ? AND user_id = ? AND item_id = ?",
                tenant,
                user,
                item,
            )

    async def erase(self, tenant: str, user: str | None = None) -> int:
        await self._ensure()
        async with self._db.transaction() as tx:
            if user is None:
                await tx.execute("DELETE FROM people_custom_connectors WHERE tenant_id = ?", tenant)
                return await tx.execute(
                    "DELETE FROM people_connections WHERE tenant_id = ?", tenant
                )
            await tx.execute(
                "DELETE FROM people_custom_connectors WHERE tenant_id = ? AND user_id = ?",
                tenant,
                user,
            )
            return await tx.execute(
                "DELETE FROM people_connections WHERE tenant_id = ? AND user_id = ?", tenant, user
            )

    def storage(self, tenant: str, user: str, item: str) -> TokenStorage:
        return _PersonStorage(self, tenant, user, item)


class _PersonStorage:
    """What the MCP SDK's OAuth client reads and writes: this one person's tokens for this one connector."""

    def __init__(self, store: ConnectionStore, tenant: str, user: str, item: str) -> None:
        self._s, self._k = store, (tenant, user, item)

    async def get_tokens(self) -> OAuthToken | None:
        raw = await self._s._get(*self._k, "tokens")  # noqa: SLF001
        return OAuthToken.model_validate(json.loads(raw)) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._s._put(*self._k, "tokens", tokens.model_dump_json(exclude_none=True))  # noqa: SLF001

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = await self._s._get(*self._k, "client_info")  # noqa: SLF001
        return OAuthClientInformationFull.model_validate(json.loads(raw)) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._s._put(  # noqa: SLF001
            *self._k, "client_info", client_info.model_dump_json(exclude_none=True)
        )
