from __future__ import annotations

from pydantic import BaseModel, Field


class EraseUserRequest(BaseModel):
    tenant_id: str
    user_id: str


class EraseTenantRequest(BaseModel):
    tenant_id: str


class ErasureSummary(BaseModel):
    tenant_id: str
    user_id: str | None = None
    conversations_deleted: int = 0
    objects_deleted: int = 0
    memories_deleted: int = 0
    removed: dict[str, int] = Field(default_factory=dict)
    """What was removed, by feature (-1: that feature failed)."""
    status: str = "completed"
    failed: list[str] = Field(default_factory=list)
