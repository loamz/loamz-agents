from typing import Annotated

from fastapi import APIRouter, Depends, Response

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.messaging.push.model import SubscriptionIn
from loamz.server.agents.features.messaging.push.service import PushService, push

Service = Annotated[PushService, Depends(use(push))]
router = APIRouter(prefix="/api/v1/push", tags=["push"])


@router.get("/key")
async def key(caller: CallerDep, service: Service) -> dict:
    service.require_configured()
    return {"public_key": service._c.settings.push.vapid_public_key}


@router.put("/subscriptions", status_code=204)
async def subscribe(body: SubscriptionIn, caller: CallerDep, service: Service) -> Response:
    service.require_configured()
    await service.store.put(caller.tenant_id, caller.user_id, body)
    return Response(status_code=204)


@router.delete("/subscriptions/{device}", status_code=204)
async def unsubscribe(device: str, caller: CallerDep, service: Service) -> Response:
    await service.store.delete(caller.tenant_id, caller.user_id, device)
    return Response(status_code=204)
