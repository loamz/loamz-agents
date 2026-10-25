from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.messaging.people.service import PeopleService, people

Service = Annotated[PeopleService, Depends(use(people))]
router = APIRouter(prefix="/api/v1/people", tags=["people"])


class InviteIn(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@router.get("")
async def search(caller: CallerDep, service: Service, q: str = Query("", max_length=100)) -> dict:
    return await service.search(caller, q)


@router.post("/invites", status_code=204)
async def invite(body: InviteIn, caller: CallerDep, service: Service) -> Response:
    await service.invite(caller, body.email)
    return Response(status_code=204)
