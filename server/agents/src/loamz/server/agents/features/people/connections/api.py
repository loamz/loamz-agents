from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.connections.service import ConnectionsService, connections

Service = Annotated[ConnectionsService, Depends(use(connections))]

router = APIRouter(prefix="/api/v1", tags=["connections"])


@router.get("/me/connections")
async def my_connections(caller: CallerDep, service: Service) -> dict[str, object]:
    return {"items": await service.status(caller)}


@router.post("/me/connections/{item_id}/start")
async def start(item_id: str, caller: CallerDep, service: Service) -> dict[str, str]:
    """Where to send the person to sign in on the vendor's page, or `connected` when the saved sign-in still works."""
    return await service.start(caller, item_id)


@router.delete("/me/connections/{item_id}", status_code=204)
async def disconnect(item_id: str, caller: CallerDep, service: Service) -> Response:
    await service.disconnect(caller, item_id)
    return Response(status_code=204)


@router.get("/connections/client.json")
async def client_metadata(service: Service) -> JSONResponse:
    """Our client id document. Public by design: connector servers fetch it to learn who is asking."""
    return JSONResponse(
        service.client_metadata(), headers={"Cache-Control": "public, max-age=3600"}
    )


@router.get("/connections/callback")
async def callback(
    service: Service,
    state: str = "",
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """The vendor sends the person's browser here after they sign in. Public: the `state` value ties it to the waiting sign-in."""
    try:
        item, ok = await service.finish(state, code, error)
    except Exception:  # noqa: BLE001 - an expired or unknown sign-in: back to the app, not connected
        item, ok = "", False
    back = service._c.settings.http.frontend_url.rstrip("/")  # noqa: SLF001
    query = urlencode({"connected" if ok else "connect_failed": item or "unknown"})
    return RedirectResponse(f"{back}/?{query}", status_code=303)
