"""What a chat agent can do about its own schedules: look at them freely, change them only with the person's approval."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loamz.engine.errors import LoamzError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.server.agents.features.automation.schedules.cron import zone
from loamz.server.agents.features.automation.schedules.model import ScheduleIn, SchedulePatch
from loamz.server.agents.features.automation.schedules.service import SchedulesService


def _text(body: str, *, error: bool = False) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=body)], is_error=error)


class ListSchedulesTool:
    name = "schedules"
    risk = ToolRisk.SAFE
    idempotent = True
    description = "List your own schedules: what you are set to do on your own, and when (with each one's id)."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, service: SchedulesService, caller: Caller, agent_id: str) -> None:
        self._service, self._caller, self._agent = service, caller, agent_id

    async def execute(self, *, ctx: object = None, **_: object) -> ToolExecutionResult:
        items = (await self._service.list(self._caller, self._agent)).items
        if not items:
            return _text("You have no schedules.")
        return _text(
            "\n".join(
                f"[{s.id}] {s.name}: {s.timing}{', quiet' if s.quiet else ''} ({s.status}). Task: {s.prompt[:200]}"
                for s in items
            )
        )


class ChangeSchedulesTool:
    name = "change_schedules"
    risk = ToolRisk.HIGH  # the person approves every use
    idempotent = False
    description = (
        "Make, change, pause, resume or delete one of your own schedules (something you do on your own at set times and report in this chat). "
        "The person approves each change, so say in 'summary' exactly what will happen, in plain words, with the time and its zone. "
        "For 'recurring' give a 5-part cron (minute hour day-of-month month day-of-week, for example '0 8 * * 1-5' for weekdays at 8:00); "
        "for 'once' give run_at as a local date and time 'YYYY-MM-DDTHH:MM'; for 'interval' give interval_minutes (at least 15). "
        "With quiet true you tell the person only when there is something to report."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "pause", "resume", "delete"]},
            "summary": {
                "type": "string",
                "description": "What will happen, in plain words, for the person to approve.",
            },
            "id": {
                "type": "string",
                "description": "The schedule's id, for everything but create.",
            },
            "name": {"type": "string"},
            "task": {
                "type": "string",
                "description": "What to do each time, written as an instruction to yourself.",
            },
            "kind": {"type": "string", "enum": ["recurring", "once", "interval"]},
            "cron_expression": {"type": "string"},
            "run_at": {
                "type": "string",
                "description": "Local 'YYYY-MM-DDTHH:MM' in `timezone`. For once.",
            },
            "interval_minutes": {"type": "integer"},
            "timezone": {
                "type": "string",
                "description": "An IANA name like Europe/London; the person's own when left out.",
            },
            "quiet": {"type": "boolean"},
        },
        "required": ["action", "summary"],
        "additionalProperties": False,
    }

    def __init__(self, service: SchedulesService, caller: Caller, agent_id: str) -> None:
        self._service, self._caller, self._agent = service, caller, agent_id

    def _run_at(self, text: str, tz: str | None) -> float | None:
        if not text:
            return None
        local = datetime.fromisoformat(text)
        return local.replace(tzinfo=zone(tz or "UTC")).timestamp()

    async def execute(
        self, *, ctx: object = None, action: str = "", id: str = "", **kw: Any
    ) -> ToolExecutionResult:  # noqa: A002
        service, who = self._service, self._caller
        try:
            tz = kw.get("timezone") or None
            if tz is None and kw.get("run_at"):
                tz = await service._default_zone(who)  # noqa: SLF001
            if action == "create":
                made = await service.create(
                    who,
                    ScheduleIn(
                        agent_id=self._agent, name=kw.get("name") or "Scheduled task", prompt=kw.get("task") or "", kind=kw.get("kind") or "recurring",
                        cron_expression=kw.get("cron_expression") or "", interval_seconds=int(kw.get("interval_minutes") or 0) * 60,
                        run_at=self._run_at(kw.get("run_at") or "", tz), timezone=tz, quiet=bool(kw.get("quiet")),
                    ),
                )  # fmt: skip
                return _text(
                    f"Scheduled [{made.id}]: {made.timing}. Next run at {datetime.fromtimestamp(made.next_run_at or 0).isoformat()}."
                )
            current = next(
                (s for s in (await service.list(who, self._agent)).items if s.id == id), None
            )
            if current is None:
                return _text(
                    "You have no schedule with that id. Use the schedules tool to see yours.",
                    error=True,
                )
            if action == "delete":
                await service.delete(who, id)
                return _text("Deleted.")
            if action in ("pause", "resume"):
                await service.patch(
                    who, id, SchedulePatch(status="paused" if action == "pause" else "active")
                )
                return _text("Paused." if action == "pause" else "Resumed.")
            if action == "update":
                patch = SchedulePatch(
                    name=kw.get("name"), prompt=kw.get("task"), kind=kw.get("kind"), cron_expression=kw.get("cron_expression"),
                    interval_seconds=int(kw["interval_minutes"]) * 60 if kw.get("interval_minutes") else None,
                    run_at=self._run_at(kw.get("run_at") or "", tz), timezone=tz, quiet=kw.get("quiet"),
                )  # fmt: skip
                changed = await service.patch(who, id, patch)
                return _text(f"Updated: {changed.timing}.")
        except (LoamzError, ValueError) as why:
            return _text(str(why), error=True)
        return _text("Use create, update, pause, resume or delete.", error=True)


def schedule_tools(service: SchedulesService, caller: Caller, agent_id: str) -> list[Any]:
    return [
        ListSchedulesTool(service, caller, agent_id),
        ChangeSchedulesTool(service, caller, agent_id),
    ]
