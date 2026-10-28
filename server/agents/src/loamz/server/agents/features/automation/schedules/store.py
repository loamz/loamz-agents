from __future__ import annotations

import time
from dataclasses import dataclass

from loamz.engine.sql import Database, Row, migrate
from loamz.runtime.kernel.types.ids import new_id

COMPONENT = "automation.schedules"
SCRIPTS = [
    """
    DROP TABLE IF EXISTS scheduled_task_runs;
    DROP TABLE IF EXISTS scheduled_tasks;
    CREATE TABLE IF NOT EXISTS agent_schedules (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        kind TEXT NOT NULL,
        cron TEXT NOT NULL,
        interval_seconds INTEGER NOT NULL,
        run_at DOUBLE PRECISION,
        timezone TEXT NOT NULL,
        status TEXT NOT NULL,
        quiet INTEGER NOT NULL DEFAULT 0,
        email_results INTEGER NOT NULL DEFAULT 0,
        approval_timeout_s INTEGER NOT NULL,
        thread_id TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        next_run_at DOUBLE PRECISION,
        last_run_at DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE INDEX IF NOT EXISTS agent_schedules_owner_idx ON agent_schedules (tenant_id, user_id, agent_id);
    CREATE INDEX IF NOT EXISTS agent_schedules_due_idx ON agent_schedules (status, next_run_at);
    CREATE TABLE IF NOT EXISTS schedule_firings (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        schedule_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        due_at DOUBLE PRECISION NOT NULL,
        status TEXT NOT NULL,
        lease_until DOUBLE PRECISION,
        attempt INTEGER NOT NULL DEFAULT 0,
        started_at DOUBLE PRECISION,
        finished_at DOUBLE PRECISION,
        summary TEXT NOT NULL DEFAULT '',
        error TEXT,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS schedule_firings_once_idx ON schedule_firings (tenant_id, schedule_id, due_at);
    CREATE INDEX IF NOT EXISTS schedule_firings_open_idx ON schedule_firings (status, lease_until);
    """,
    # The time zone the schedule was in when each run was due, so a run shows in the zone it was set for (the moment itself is UTC).
    "ALTER TABLE schedule_firings ADD COLUMN zone TEXT NOT NULL DEFAULT 'UTC';",
    # Where "e-mail me the result" goes: the address the person had when they asked (a run has no sign-in to read it from).
    "ALTER TABLE agent_schedules ADD COLUMN owner_email TEXT NOT NULL DEFAULT '';",
]

_S = (
    "id, user_id, agent_id, name, prompt, kind, cron, interval_seconds, run_at, timezone, status, quiet, email_results, "
    "approval_timeout_s, thread_id, failures, next_run_at, last_run_at, created_at, updated_at, owner_email, tenant_id"
)
_F = "id, schedule_id, user_id, tenant_id, due_at, zone, status, lease_until, attempt, started_at, finished_at, summary, error"


@dataclass(frozen=True)
class Schedule:
    id: str
    tenant_id: str
    user_id: str
    agent_id: str
    name: str
    prompt: str
    kind: str
    cron: str
    interval_seconds: int
    run_at: float | None
    timezone: str
    status: str
    quiet: bool
    email_results: bool
    approval_timeout_s: int
    thread_id: str | None
    failures: int
    next_run_at: float | None
    last_run_at: float | None
    created_at: float
    updated_at: float
    owner_email: str = ""


@dataclass(frozen=True)
class Firing:
    id: str
    schedule_id: str
    tenant_id: str
    user_id: str
    due_at: float
    zone: str
    status: str
    lease_until: float | None
    attempt: int
    started_at: float | None
    finished_at: float | None
    summary: str
    error: str | None


def _schedule(r: Row) -> Schedule:
    return Schedule(
        id=r["id"], tenant_id=r["tenant_id"], user_id=r["user_id"], agent_id=r["agent_id"], name=r["name"], prompt=r["prompt"],
        kind=r["kind"], cron=r["cron"], interval_seconds=r["interval_seconds"], run_at=r["run_at"], timezone=r["timezone"],
        status=r["status"], quiet=bool(r["quiet"]), email_results=bool(r["email_results"]), approval_timeout_s=r["approval_timeout_s"],
        thread_id=r["thread_id"], failures=r["failures"], next_run_at=r["next_run_at"], last_run_at=r["last_run_at"],
        created_at=r["created_at"], updated_at=r["updated_at"], owner_email=r["owner_email"],
    )  # fmt: skip


def _firing(r: Row) -> Firing:
    return Firing(
        id=r["id"], schedule_id=r["schedule_id"], tenant_id=r["tenant_id"], user_id=r["user_id"], due_at=r["due_at"], zone=r["zone"], status=r["status"],
        lease_until=r["lease_until"], attempt=r["attempt"], started_at=r["started_at"], finished_at=r["finished_at"],
        summary=r["summary"], error=r["error"],
    )  # fmt: skip


class SchedulesStore:
    """Schedules and their firings. Every statement is keyed by the tenant; a person's reads by the user too."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _tx(self):  # noqa: ANN202
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True
        return self._db.transaction()

    # ── schedules ───────────────────────────────────────────────────────────
    async def insert(self, s: Schedule) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                "INSERT INTO agent_schedules (tenant_id, id, user_id, agent_id, name, prompt, kind, cron, interval_seconds, run_at, timezone, "
                "status, quiet, email_results, approval_timeout_s, thread_id, failures, next_run_at, last_run_at, created_at, updated_at, owner_email) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                s.tenant_id, s.id, s.user_id, s.agent_id, s.name, s.prompt, s.kind, s.cron, s.interval_seconds, s.run_at, s.timezone,
                s.status, int(s.quiet), int(s.email_results), s.approval_timeout_s, s.thread_id, s.failures, s.next_run_at, s.last_run_at,
                s.created_at, s.updated_at, s.owner_email,
            )  # fmt: skip

    async def get(self, tenant: str, user: str, schedule_id: str) -> Schedule | None:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                f"SELECT {_S} FROM agent_schedules WHERE tenant_id = ? AND user_id = ? AND id = ?",
                tenant,
                user,
                schedule_id,
            )
        return _schedule(row) if row else None

    async def by_id(self, tenant: str, schedule_id: str) -> Schedule | None:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                f"SELECT {_S} FROM agent_schedules WHERE tenant_id = ? AND id = ?",
                tenant,
                schedule_id,
            )
        return _schedule(row) if row else None

    async def list(self, tenant: str, user: str, agent_id: str | None = None) -> list[Schedule]:
        sql = f"SELECT {_S} FROM agent_schedules WHERE tenant_id = ? AND user_id = ?"
        args: list[object] = [tenant, user]
        if agent_id:
            sql += " AND agent_id = ?"
            args.append(agent_id)
        async with await self._tx() as tx:
            rows = await tx.fetchall(sql + " ORDER BY created_at, id", *args)
        return [_schedule(r) for r in rows]

    async def change(self, tenant: str, schedule_id: str, **values: object) -> None:
        allowed = {
            "name", "prompt", "kind", "cron", "interval_seconds", "run_at", "timezone", "status", "quiet", "email_results",
            "approval_timeout_s", "thread_id", "failures", "next_run_at", "last_run_at", "owner_email",
        }  # fmt: skip
        if not values.keys() <= allowed:
            raise ValueError("Unknown schedule field")
        async with await self._tx() as tx:
            for key, value in values.items():
                if isinstance(value, bool):
                    value = int(value)
                await tx.execute(
                    f"UPDATE agent_schedules SET {key} = ? WHERE tenant_id = ? AND id = ?",
                    value,
                    tenant,
                    schedule_id,
                )
            await tx.execute(
                "UPDATE agent_schedules SET updated_at = ? WHERE tenant_id = ? AND id = ?",
                time.time(),
                tenant,
                schedule_id,
            )

    async def delete(self, tenant: str, user: str, schedule_id: str) -> bool:
        async with await self._tx() as tx:
            await tx.execute(
                "DELETE FROM schedule_firings WHERE tenant_id = ? AND user_id = ? AND schedule_id = ?",
                tenant,
                user,
                schedule_id,
            )
            return bool(
                await tx.execute(
                    "DELETE FROM agent_schedules WHERE tenant_id = ? AND user_id = ? AND id = ?",
                    tenant,
                    user,
                    schedule_id,
                )
            )

    async def delete_for_agent(self, tenant: str, user: str, agent_id: str) -> int:
        async with await self._tx() as tx:
            await tx.execute(
                "DELETE FROM schedule_firings WHERE tenant_id = ? AND user_id = ? AND schedule_id IN "
                "(SELECT id FROM agent_schedules WHERE tenant_id = ? AND user_id = ? AND agent_id = ?)",
                tenant, user, tenant, user, agent_id,
            )  # fmt: skip
            return await tx.execute(
                "DELETE FROM agent_schedules WHERE tenant_id = ? AND user_id = ? AND agent_id = ?",
                tenant,
                user,
                agent_id,
            )

    # ── firings ─────────────────────────────────────────────────────────────
    async def due(self, now: float, limit: int = 50) -> list[Schedule]:
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_S} FROM agent_schedules WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ? "
                "ORDER BY next_run_at LIMIT ?",
                now, limit,
            )  # fmt: skip
        return [_schedule(r) for r in rows]

    async def begin_firing(
        self, s: Schedule, due_at: float, next_at: float | None, *, advance: bool = True
    ) -> str | None:
        """Record that `s` is due and move it to its next time, in one step: a crash leaves a firing waiting (it is picked up again),
        never a lost one. None when this firing already exists (another server recorded it first)."""
        firing_id = new_id()
        async with await self._tx() as tx:
            made = await tx.execute(
                "INSERT INTO schedule_firings (tenant_id, id, schedule_id, user_id, due_at, zone, status) VALUES (?, ?, ?, ?, ?, ?, 'pending') "
                "ON CONFLICT (tenant_id, schedule_id, due_at) DO NOTHING",
                s.tenant_id, firing_id, s.id, s.user_id, due_at, s.timezone,
            )  # fmt: skip
            if not made:
                return None
            if advance:
                await tx.execute(
                    "UPDATE agent_schedules SET next_run_at = ?, status = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                    next_at, "active" if next_at is not None else "completed", time.time(), s.tenant_id, s.id,
                )  # fmt: skip
        return firing_id

    async def claim(self, now: float, lease_seconds: float, limit: int = 10) -> list[Firing]:
        """Firings that are waiting, or whose worker went away, taken by this server for `lease_seconds`."""
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_F} FROM schedule_firings WHERE status = 'pending' OR (status = 'running' AND lease_until < ?) "
                "ORDER BY due_at LIMIT ?",
                now, limit,
            )  # fmt: skip
        taken: list[Firing] = []
        for r in rows:
            async with await self._tx() as tx:
                won = await tx.execute(
                    "UPDATE schedule_firings SET status = 'running', lease_until = ?, attempt = attempt + 1, started_at = COALESCE(started_at, ?) "
                    "WHERE tenant_id = ? AND id = ? AND (status = 'pending' OR (status = 'running' AND lease_until < ?))",
                    now + lease_seconds, now, r["tenant_id"], r["id"], now,
                )  # fmt: skip
            if won:
                taken.append(_firing(r))
        return taken

    async def extend(self, tenant: str, firing_id: str, until: float) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                "UPDATE schedule_firings SET lease_until = ? WHERE tenant_id = ? AND id = ?",
                until,
                tenant,
                firing_id,
            )

    async def finish(
        self,
        tenant: str,
        firing_id: str,
        status: str,
        *,
        summary: str = "",
        error: str | None = None,
    ) -> None:
        async with await self._tx() as tx:
            await tx.execute(
                "UPDATE schedule_firings SET status = ?, summary = ?, error = ?, finished_at = ?, lease_until = NULL WHERE tenant_id = ? AND id = ?",
                status, summary[:2000], error, time.time(), tenant, firing_id,
            )  # fmt: skip

    async def firings(
        self, tenant: str, user: str, schedule_id: str, limit: int = 20
    ) -> list[Firing]:
        async with await self._tx() as tx:
            rows = await tx.fetchall(
                f"SELECT {_F} FROM schedule_firings WHERE tenant_id = ? AND user_id = ? AND schedule_id = ? ORDER BY due_at DESC LIMIT ?",
                tenant, user, schedule_id, limit,
            )  # fmt: skip
        return [_firing(r) for r in rows]

    async def running_for(self, tenant: str, schedule_id: str, now: float, exclude: str) -> bool:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                "SELECT 1 AS x FROM schedule_firings WHERE tenant_id = ? AND schedule_id = ? AND status = 'running' AND lease_until >= ? AND id != ?",
                tenant, schedule_id, now, exclude,
            )  # fmt: skip
        return row is not None

    async def started_since(self, tenant: str, user: str, since: float) -> int:
        async with await self._tx() as tx:
            row = await tx.fetchone(
                "SELECT COUNT(*) AS n FROM schedule_firings WHERE tenant_id = ? AND user_id = ? AND started_at >= ?",
                tenant,
                user,
                since,
            )
        return int(row["n"]) if row else 0

    async def erase(self, tenant: str, user: str | None = None) -> int:
        async with await self._tx() as tx:
            if user is None:
                await tx.execute("DELETE FROM schedule_firings WHERE tenant_id = ?", tenant)
                return await tx.execute("DELETE FROM agent_schedules WHERE tenant_id = ?", tenant)
            await tx.execute(
                "DELETE FROM schedule_firings WHERE tenant_id = ? AND user_id = ?", tenant, user
            )
            return await tx.execute(
                "DELETE FROM agent_schedules WHERE tenant_id = ? AND user_id = ?", tenant, user
            )
