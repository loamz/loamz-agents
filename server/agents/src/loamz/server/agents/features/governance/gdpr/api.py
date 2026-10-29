from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.governance.gdpr.model import (
    EraseTenantRequest,
    EraseUserRequest,
    ErasureSummary,
)
from loamz.server.agents.features.governance.gdpr.service import GdprService, gdpr

Service = Annotated[GdprService, Depends(use(gdpr))]

router = APIRouter(prefix="/api/v1", tags=["gdpr"])


@router.delete("/me/data", response_model=ErasureSummary)
async def erase_my_data(caller: CallerDep, service: Service) -> ErasureSummary:
    """The signed-in person removes everything this server holds about them (used when they close their account)."""
    return await service.erase_user(caller.tenant_id, caller.user_id)


@router.post("/internal/gdpr/erase-user", response_model=ErasureSummary)
async def erase_user_data(body: EraseUserRequest, service: Service) -> ErasureSummary:
    return await service.erase_user(body.tenant_id, body.user_id)


@router.post("/internal/gdpr/erase-tenant", response_model=ErasureSummary)
async def erase_tenant_data(body: EraseTenantRequest, service: Service) -> ErasureSummary:
    return await service.erase_tenant(body.tenant_id)
