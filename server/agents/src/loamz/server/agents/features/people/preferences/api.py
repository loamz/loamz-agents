from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.preferences.model import Preferences
from loamz.server.agents.features.people.preferences.service import PreferencesService, preferences

Service = Annotated[PreferencesService, Depends(use(preferences))]

router = APIRouter(prefix="/api/v1/me/preferences", tags=["preferences"])


@router.get("", response_model=Preferences)
async def get_preferences(caller: CallerDep, service: Service) -> Preferences:
    return await service.get(caller)


class AgentsPaused(BaseModel):
    paused: bool


@router.put("/agents-paused", response_model=Preferences)
async def pause_agents(body: AgentsPaused, caller: CallerDep, service: Service) -> Preferences:
    """Pauses (or resumes) every agent of this person, and so their schedules."""
    return await service.set_agents_paused(caller, body.paused)


@router.put("", response_model=Preferences)
async def replace_preferences(
    body: Preferences, caller: CallerDep, service: Service
) -> Preferences:
    return await service.replace(caller, body)
