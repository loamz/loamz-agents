from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.automation.schedules.model import (
    FiringList,
    FiringOut,
    ScheduleIn,
    ScheduleList,
    ScheduleOut,
    SchedulePatch,
)
from loamz.server.agents.features.automation.schedules.service import SchedulesService, schedules

Service = Annotated[SchedulesService, Depends(use(schedules))]

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.get("", response_model=ScheduleList)
async def list_schedules(
    caller: CallerDep, service: Service, agent_id: str | None = None
) -> ScheduleList:
    """The person's schedules, each belonging to one of their chat agents (all of them, or one agent's)."""
    return await service.list(caller, agent_id)


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, caller: CallerDep, service: Service) -> ScheduleOut:
    return await service.create(caller, body)


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: str, caller: CallerDep, service: Service) -> ScheduleOut:
    return await service.get(caller, schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def change_schedule(
    schedule_id: str, body: SchedulePatch, caller: CallerDep, service: Service
) -> ScheduleOut:
    return await service.patch(caller, schedule_id, body)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, schedule_id)
    return Response(status_code=204)


@router.post("/{schedule_id}/run", response_model=FiringOut, status_code=status.HTTP_202_ACCEPTED)
async def run_schedule(schedule_id: str, caller: CallerDep, service: Service) -> FiringOut:
    """Run it now, besides its usual times (it starts within half a minute)."""
    return await service.run_now(caller, schedule_id)


@router.get("/{schedule_id}/runs", response_model=FiringList)
async def schedule_runs(schedule_id: str, caller: CallerDep, service: Service) -> FiringList:
    return await service.firings(caller, schedule_id)
