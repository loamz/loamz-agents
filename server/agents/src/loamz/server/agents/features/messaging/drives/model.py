from pydantic import BaseModel

from loamz.server.agents.features.messaging.chats.model import GroupFile


class GroupFileList(BaseModel):
    items: list[GroupFile]
