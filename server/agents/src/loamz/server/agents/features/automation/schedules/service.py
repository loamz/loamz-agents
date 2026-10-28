from __future__ import annotations

import time

from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.types.ids import new_id
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.automation.schedules.cron import (
    describe,
    next_run,
    parse_cron,
    zone,
)
from loamz.server.agents.features.automation.schedules.model import (
    MAX_SCHEDULES_PER_PERSON,
    FiringList,
    FiringOut,
    ScheduleIn,
    ScheduleList,
    ScheduleOut,
    SchedulePatch,
)
from loamz.server.agents.features.automation.schedules.store import Firing, Schedule, SchedulesStore


def _firing_out(f: Firing) -> FiringOut:
    return FiringOut(
        id=f.id, due_at=f.due_at, zone=f.zone, status=f.status, attempt=f.attempt, started_at=f.started_at, finished_at=f.finished_at,
        summary=f.summary, error=f.error,
    )  # fmt: skip


class SchedulesService:
    """Schedules belong to one chat agent each (never to a session) and are deleted with it."""

    def __init__(self, container: Container, store: SchedulesStore) -> None:
        self._c = container
        self._store = store

    @property
    def store(self) -> SchedulesStore:
        return self._store

    # ── helpers ─────────────────────────────────────────────────────────────
    async def _agent(self, caller: Caller, agent_id: str):  # noqa: ANN202
        from loamz.server.agents.features.directory.agents.service import agents

        return await self._c.get(agents).get_owned(caller, agent_id)

    async def _default_zone(self, caller: Caller) -> str:
        from loamz.server.agents.features.people.preferences.service import preferences

        try:
            return await self._c.get(preferences).zone(caller)
        except Exception:  # noqa: BLE001 - a zone is a convenience; fall back to UTC
            return "UTC"

    @staticmethod
    def _check(
        kind: str, cron: str, interval: int, run_at: float | None, tz: str, now: float
    ) -> float | None:
        """Validate the timing and give its first firing."""
        zone(tz)
        if kind == "recurring":
            parse_cron(cron)
        elif kind == "interval":
            pass
        elif kind == "once":
            if run_at is None or run_at <= now:
                raise InvalidRequestError("Give a date and time in the future.")
        return next_run(kind, cron=cron, interval_seconds=interval, run_at=run_at, tz=tz, after=now)

    async def _out(self, s: Schedule, names: dict[str, str], recent: int = 3) -> ScheduleOut:
        firings = await self._store.firings(s.tenant_id, s.user_id, s.id, limit=recent)
        return ScheduleOut(
            id=s.id, agent_id=s.agent_id, agent_name=names.get(s.agent_id, ""), name=s.name, prompt=s.prompt,
            kind=s.kind, cron_expression=s.cron, interval_seconds=s.interval_seconds, run_at=s.run_at, timezone=s.timezone,
            timing=describe(s.kind, cron=s.cron, interval_seconds=s.interval_seconds, run_at=s.run_at, tz=s.timezone),
            status=s.status, quiet=s.quiet, email_results=s.email_results, approval_timeout_s=s.approval_timeout_s,
            next_run_at=s.next_run_at, last_run_at=s.last_run_at, created_at=s.created_at, updated_at=s.updated_at,
            recent=[_firing_out(f) for f in firings],
        )  # type: ignore[arg-type]  # fmt: skip

    async def _names(self, caller: Caller) -> dict[str, str]:
        from loamz.server.agents.features.directory.agents.service import agents

        return {a.id: a.name for a in (await self._c.get(agents).list(caller)).items}

    # ── what a person (or their agent) does ─────────────────────────────────
    async def create(self, caller: Caller, body: ScheduleIn) -> ScheduleOut:
        agent = await self._agent(caller, body.agent_id)
        if (
            len(await self._store.list(caller.tenant_id, caller.user_id))
            >= MAX_SCHEDULES_PER_PERSON
        ):
            raise ConflictError(
                f"You can have {MAX_SCHEDULES_PER_PERSON} schedules. Delete one to add another."
            )
        tz = body.timezone or await self._default_zone(caller)
        now = time.time()
        first = self._check(
            body.kind, body.cron_expression.strip(), body.interval_seconds, body.run_at, tz, now
        )
        schedule = Schedule(
            id=new_id(), tenant_id=caller.tenant_id, user_id=caller.user_id, agent_id=agent.id, name=body.name.strip(),
            prompt=body.prompt.strip(), kind=body.kind, cron=body.cron_expression.strip(), interval_seconds=body.interval_seconds,
            run_at=body.run_at, timezone=tz, status="active", quiet=body.quiet, email_results=body.email_results,
            approval_timeout_s=body.approval_timeout_s, thread_id=None, failures=0, next_run_at=first, last_run_at=None,
            created_at=now, updated_at=now, owner_email=(caller.email or "") if body.email_results else "",
        )  # fmt: skip
        await self._store.insert(schedule)
        return await self._out(schedule, {agent.id: agent.name})

    async def list(self, caller: Caller, agent_id: str | None = None) -> ScheduleList:
        names = await self._names(caller)
        rows = await self._store.list(caller.tenant_id, caller.user_id, agent_id)
        return ScheduleList(items=[await self._out(s, names) for s in rows])

    async def _mine(self, caller: Caller, schedule_id: str) -> Schedule:
        found = await self._store.get(caller.tenant_id, caller.user_id, schedule_id)
        if found is None:
            raise NotFoundError("Schedule not found")
        return found

    async def get(self, caller: Caller, schedule_id: str) -> ScheduleOut:
        return await self._out(
            await self._mine(caller, schedule_id), await self._names(caller), recent=10
        )

    async def patch(self, caller: Caller, schedule_id: str, body: SchedulePatch) -> ScheduleOut:
        s = await self._mine(caller, schedule_id)
        change: dict[str, object] = {}
        for field in ("name", "prompt", "quiet", "email_results"):
            if (value := getattr(body, field)) is not None:
                change[field] = value.strip() if isinstance(value, str) else value
        if body.email_results and caller.email:
            change["owner_email"] = caller.email
        kind = body.kind or s.kind
        cron = body.cron_expression.strip() if body.cron_expression is not None else s.cron
        interval = (
            body.interval_seconds if body.interval_seconds is not None else s.interval_seconds
        )
        run_at = body.run_at if body.run_at is not None else s.run_at
        tz = body.timezone or s.timezone
        timing_changed = any(
            v is not None
            for v in (
                body.kind,
                body.cron_expression,
                body.interval_seconds,
                body.run_at,
                body.timezone,
            )
        )
        status = body.status or s.status
        if timing_changed or (status == "active" and s.status != "active"):
            now = time.time()
            first = self._check(kind, cron, interval, run_at, tz, now)
            change.update(
                kind=kind,
                cron=cron,
                interval_seconds=interval,
                run_at=run_at,
                timezone=tz,
                next_run_at=first,
            )
            if status == "active" and first is None:
                raise InvalidRequestError("That time has passed.")
            change["failures"] = 0
        if body.status is not None:
            change["status"] = body.status
        if change:
            await self._store.change(s.tenant_id, s.id, **change)
        return await self.get(caller, schedule_id)

    async def delete(self, caller: Caller, schedule_id: str) -> None:
        if not await self._store.delete(caller.tenant_id, caller.user_id, schedule_id):
            raise NotFoundError("Schedule not found")

    async def run_now(self, caller: Caller, schedule_id: str) -> FiringOut:
        """A firing right away, without moving the schedule's next time."""
        s = await self._mine(caller, schedule_id)
        now = time.time()
        firing_id = await self._store.begin_firing(s, now, None, advance=False)
        if firing_id is None:
            raise ConflictError("It was just started. Give it a moment.")
        return FiringOut(id=firing_id, due_at=now, zone=s.timezone, status="pending")

    async def firings(self, caller: Caller, schedule_id: str) -> FiringList:
        s = await self._mine(caller, schedule_id)
        return FiringList(
            items=[
                _firing_out(f) for f in await self._store.firings(s.tenant_id, s.user_id, s.id, 30)
            ]
        )

    # ── hooks and erasure ───────────────────────────────────────────────────
    async def agent_gone(self, tenant_id: str, user_id: str, agent_id: str) -> int:
        return await self._store.delete_for_agent(tenant_id, user_id, agent_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def schedules(container: Container) -> SchedulesService:
    from loamz.server.agents.features.directory.agents.service import agent_hooks

    service = SchedulesService(container, SchedulesStore(container.database))

    async def gone(profile) -> None:  # noqa: ANN001
        await service.agent_gone(profile.tenant_id, profile.user_id, profile.id)

    # The container builds this once, so the hook is registered once: deleting an agent deletes its schedules.
    container.get(agent_hooks).deleted.append(gone)
    return service
