from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.marketplace.model import (
    Catalog,
    Install,
    InstallBody,
    InstallList,
    Listing,
)
from loamz.server.agents.features.people.marketplace.service import MarketplaceService, marketplace

Service = Annotated[MarketplaceService, Depends(use(marketplace))]

router = APIRouter(prefix="/api/v1", tags=["marketplace"])


@router.get("/marketplace", response_model=Catalog)
async def list_marketplace(
    caller: CallerDep,
    service: Service,
    kind: Annotated[str | None, Query(pattern="^(skill|tool|connector|agent)$")] = None,
    category: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str, Query(pattern="^(recommended|popular|newest|name)$")] = "recommended",
) -> Catalog:
    return service.catalog(kind=kind, category=category, q=q, sort=sort)


@router.get("/marketplace/{item_id}", response_model=Listing)
async def get_listing(item_id: str, caller: CallerDep, service: Service) -> Listing:
    return service.listing(item_id)


@router.get("/me/installs", response_model=InstallList)
async def my_installs(caller: CallerDep, service: Service) -> InstallList:
    return await service.installed(caller)


@router.put("/me/installs/{item_id}", response_model=Install)
async def install(item_id: str, body: InstallBody, caller: CallerDep, service: Service) -> Install:
    return await service.install(caller, item_id, body)


@router.delete("/me/installs/{item_id}", status_code=204)
async def remove(item_id: str, caller: CallerDep, service: Service) -> Response:
    await service.remove(caller, item_id)
    return Response(status_code=204)
