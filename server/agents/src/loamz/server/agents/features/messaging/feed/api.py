from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.messaging.feed.model import FeedIn
from loamz.server.agents.features.messaging.feed.service import FeedService, feed

Service = Annotated[FeedService, Depends(use(feed))]

router = APIRouter(tags=["feed"])


@router.post("/api/v1/feed")
async def open_feed(body: FeedIn, caller: CallerDep, service: Service) -> StreamingResponse:
    return StreamingResponse(
        service.stream(caller, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/groups/{chat}/typing", status_code=204)
async def typing(chat: str, caller: CallerDep, service: Service) -> Response:
    """The caller is typing in this chat. A hint that expires in a few seconds; nothing is stored."""
    await service.typing(caller, chat)
    return Response(status_code=204)
