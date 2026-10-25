"""Device subscriptions belong to a tenant and person; an endpoint cannot be reassigned by another user."""

from loamz.engine.errors import InvalidRequestError
from loamz.engine.sql import Database, migrate
from loamz.server.agents.features.messaging.push.model import SubscriptionIn

SCHEMA = [
    """
CREATE TABLE messaging_push (
    tenant TEXT NOT NULL, user_id TEXT NOT NULL, device TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
    PRIMARY KEY (tenant, user_id, device)
);
"""
]


class PushStore:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def ready(self) -> None:
        if not self._ready:
            await migrate(self._db, "messaging.push", SCHEMA)
            self._ready = True

    async def put(self, tenant: str, user: str, body: SubscriptionIn) -> None:
        await self.ready()
        async with self._db.transaction() as tx:
            await tx.lock(f"push:{body.endpoint}")
            row = await tx.fetchone(
                "SELECT tenant, user_id FROM messaging_push WHERE endpoint = ?", body.endpoint
            )
            if row and (row["tenant"], row["user_id"]) != (tenant, user):
                raise InvalidRequestError("This subscription belongs to another account")
            await tx.execute(
                "INSERT INTO messaging_push (tenant, user_id, device, endpoint, p256dh, auth) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant, user_id, device) DO UPDATE SET endpoint = excluded.endpoint, p256dh = excluded.p256dh, auth = excluded.auth",
                tenant,
                user,
                body.device,
                body.endpoint,
                body.p256dh,
                body.auth,
            )

    async def for_user(self, tenant: str, user: str) -> list[dict]:
        await self.ready()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT device, endpoint, p256dh, auth FROM messaging_push WHERE tenant = ? AND user_id = ?",
                tenant,
                user,
            )
        return [dict(row) for row in rows]

    async def delete(self, tenant: str, user: str, device: str) -> None:
        await self.ready()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM messaging_push WHERE tenant = ? AND user_id = ? AND device = ?",
                tenant,
                user,
                device,
            )

    async def erase(self, tenant: str, user: str | None = None) -> int:
        await self.ready()
        async with self._db.transaction() as tx:
            if user is None:
                return await tx.execute("DELETE FROM messaging_push WHERE tenant = ?", tenant)
            return await tx.execute(
                "DELETE FROM messaging_push WHERE tenant = ? AND user_id = ?", tenant, user
            )
