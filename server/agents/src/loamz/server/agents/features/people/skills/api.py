from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Response, UploadFile, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.skills.model import (
    SkillIn,
    SkillList,
    SkillOut,
    SkillPatch,
    UploadResult,
)
from loamz.server.agents.features.people.skills.parse import MAX_UPLOAD_BYTES
from loamz.server.agents.features.people.skills.service import SkillsService, skills

Service = Annotated[SkillsService, Depends(use(skills))]

router = APIRouter(prefix="/api/v1/me/skills", tags=["skills"])


@router.get("", response_model=SkillList)
async def my_skills(caller: CallerDep, service: Service) -> SkillList:
    return SkillList(items=await service.list(caller))


@router.post("", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def create(body: SkillIn, caller: CallerDep, service: Service) -> SkillOut:
    return await service.create(caller, body)


@router.post("/upload", response_model=UploadResult)
async def upload(
    caller: CallerDep, service: Service, file: Annotated[UploadFile, File()]
) -> UploadResult:
    """A security scan runs first. A refusal comes back as `findings` (file by file) with nothing added."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    return await service.upload(caller, file.filename or "skill.md", data)


@router.get("/{skill_id}", response_model=SkillOut)
async def get(skill_id: str, caller: CallerDep, service: Service) -> SkillOut:
    return await service.get(caller, skill_id)


@router.patch("/{skill_id}", response_model=SkillOut)
async def change(skill_id: str, body: SkillPatch, caller: CallerDep, service: Service) -> SkillOut:
    return await service.change(caller, skill_id, body)


@router.delete("/{skill_id}", status_code=204)
async def delete(skill_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, skill_id)
    return Response(status_code=204)
