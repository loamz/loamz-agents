from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

MAX_SKILLS_PER_USER = 50
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_INSTRUCTIONS = 20_000


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION)
    instructions: str = Field(min_length=1, max_length=MAX_INSTRUCTIONS)


class SkillPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, min_length=1, max_length=MAX_DESCRIPTION)
    instructions: str | None = Field(default=None, min_length=1, max_length=MAX_INSTRUCTIONS)
    enabled: bool | None = None


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    enabled: bool = True
    created_at: datetime


class SkillList(BaseModel):
    items: list[SkillOut]


class ScanFinding(BaseModel):
    file: str
    problem: str


class UploadResult(BaseModel):
    """What the security scan found, and the skills it made (none when anything was refused)."""

    created: list[SkillOut] = Field(default_factory=list)
    findings: list[ScanFinding] = Field(default_factory=list)
