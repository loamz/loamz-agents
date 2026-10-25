from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.conversations.pictures.service import PictureProxy, pictures

Service = Annotated[PictureProxy, Depends(use(pictures))]

router = APIRouter(prefix="/api/v1", tags=["pictures"])


@router.get("/media/image")
async def web_picture(
    _: CallerDep, service: Service, url: str = Query(max_length=2048)
) -> Response:
    """A web picture a card shows, fetched and checked by the server (see the service for the rules)."""
    picture = await service.fetch(url)
    return Response(
        picture.content,
        media_type=picture.media_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
