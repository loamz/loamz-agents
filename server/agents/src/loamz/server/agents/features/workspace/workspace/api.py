from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.workspace.workspace.model import (
    DeleteFileResponse,
    RestoreRequest,
    SaveFileRequest,
    VersionsResponse,
    WorkspaceFilesResponse,
    WorkspaceUsageResponse,
)
from loamz.server.agents.features.workspace.workspace.service import WorkspaceService, workspace

Service = Annotated[WorkspaceService, Depends(use(workspace))]

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])


@router.get("/usage", response_model=WorkspaceUsageResponse)
async def get_usage(caller: CallerDep, service: Service) -> WorkspaceUsageResponse:
    return await service.usage(caller)


@router.get("/files", response_model=WorkspaceFilesResponse)
async def list_files(
    caller: CallerDep,
    service: Service,
    thread_id: str | None = Query(default=None),
) -> WorkspaceFilesResponse:
    return await service.list_files(caller, session_id=thread_id)


@router.get("/file")
async def serve_file(
    path: str,
    thread_id: str,
    caller: CallerDep,
    service: Service,
    branch_id: str = "main",
) -> Response:
    content = await service.serve_file(caller, path, thread_id, branch_id=branch_id)
    # The real type, so the browser shows an image or a PDF instead of downloading bytes; nosniff keeps it to that type.
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Response(
        content=content, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
    )


@router.put("/file")
async def save_file(
    body: SaveFileRequest,
    caller: CallerDep,
    service: Service,
) -> dict[str, bool]:
    await service.save_file(
        caller,
        body.path,
        body.content,
        body.thread_id or "",
        branch_id=body.branch_id,
    )
    return {"saved": True}


@router.delete("/files", response_model=DeleteFileResponse)
async def delete_file(
    path: str,
    thread_id: str,
    caller: CallerDep,
    service: Service,
    branch_id: str = "main",
) -> DeleteFileResponse:
    deleted = await service.delete_file(caller, path, thread_id, branch_id=branch_id)
    return DeleteFileResponse(deleted=deleted)


@router.get("/versions", response_model=VersionsResponse)
async def get_versions(
    path: str,
    thread_id: str,
    caller: CallerDep,
    service: Service,
    branch_id: str = "main",
) -> VersionsResponse:
    return await service.list_versions(caller, path, thread_id, branch_id=branch_id)


@router.post("/versions/restore")
async def restore_version(
    body: RestoreRequest,
    caller: CallerDep,
    service: Service,
) -> dict[str, bool]:
    await service.restore_version(
        caller,
        body.path,
        body.seq,
        body.thread_id or "",
        branch_id=body.branch_id,
    )
    return {"restored": True}
