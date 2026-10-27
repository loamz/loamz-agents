"""Shared uploads are authorized before reading bytes or allocating a storage key."""

import mimetypes
from pathlib import PurePosixPath

from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.types.ids import new_id
from loamz.engine.errors import InvalidRequestError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.messaging.chats.model import GroupFile
from loamz.server.agents.features.messaging.chats.service import chats
from loamz.server.agents.features.messaging.drives.model import GroupFileList


class DrivesService:
    def __init__(self, container: Container) -> None:
        self._c = container

    def _prefix(self, tenant: str, chat: str) -> str:
        return f"tenants/{tenant}/groups/{chat}/files/"

    async def list_files(self, caller: Caller, group_id: str) -> GroupFileList:
        await self._c.get(chats).require(caller, group_id)
        files = await self._c.file_store.list_prefix(self._prefix(caller.tenant_id, group_id))
        return GroupFileList(
            items=[
                GroupFile(
                    key=key,
                    name=key.rsplit("/", 1)[-1],
                    size=size,
                    modified=mtime,
                    mime=mimetypes.guess_type(key)[0] or "application/octet-stream",
                )
                for key, size, mtime in files
            ]
        )

    async def upload_file(self, caller: Caller, group_id: str, name: str, data: bytes) -> GroupFile:
        await self._c.get(chats).require(caller, group_id)
        if len(data) > 20 * 1024 * 1024:
            raise InvalidRequestError("Files must be under 20 MiB")
        name = PurePosixPath(name.replace("\\", "/")).name
        if name in ("", ".", ".."):
            raise InvalidRequestError("Choose a file name")
        key = f"{self._prefix(caller.tenant_id, group_id)}{new_id()}/{name}"
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        await self._c.file_store.upload(key, data, content_type=mime)
        return GroupFile(key=key, name=name, size=len(data), mime=mime)


def drives(container: Container) -> DrivesService:
    return DrivesService(container)
