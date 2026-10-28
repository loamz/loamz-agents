"""Messenger wire values shared by ordinary and direct conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from loamz.runtime.kernel.runtime.channel import Mode
from loamz.server.agents.features.directory.agents.looks import Look


class MemberIn(BaseModel):
    member: str = Field(pattern=r"^(user|agent):[^/]+$", max_length=256)
    mode: Mode = Mode.MENTIONS
    role: Literal["admin", "member"] = "member"


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    members: list[MemberIn] = Field(default_factory=list, max_length=100)
    kind: Literal["group", "direct"] = "group"

    @model_validator(mode="after")
    def unique_members(self) -> GroupIn:
        if len({m.member for m in self.members}) != len(self.members):
            raise ValueError("A member may only appear once")
        return self


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    token_cap: int | None = Field(default=None, ge=0)
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    breaker: int | None = Field(default=None, ge=1, le=1000)
    look: Look | None = None
    clear_look: bool = False
    """`look: null` leaves it as it is, so going back to the first letter needs a flag."""


class MemberPatch(BaseModel):
    mode: Mode | None = None
    role: Literal["admin", "member"] | None = None


class MemberOut(BaseModel):
    member: str
    kind: Literal["human", "agent"]
    tenant: str
    user_id: str | None = None
    agent_id: str | None = None
    name: str
    role: str
    mode: Mode
    avatar: str | None = None
    tokens_used: int = 0
    cost_usd: float = 0
    read: int = -1
    delivered: int = -1


class GroupOut(BaseModel):
    id: str
    name: str
    kind: Literal["group", "direct"] = "group"
    e2ee: bool = False
    created_at: datetime
    updated_at: datetime
    members: list[MemberOut]
    last_message: str | None = None
    last_sender: str | None = None
    unread: int = 0
    paused: bool = False
    working: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    token_cap: int = 0
    budget_usd: float | None = None
    cost_usd: float = 0
    breaker: int = 40
    avatar: str | None = None
    look: Look | None = None
    pinned_at: datetime | None = None


class GroupList(BaseModel):
    items: list[GroupOut]


class GroupFile(BaseModel):
    key: str
    name: str
    size: int = Field(ge=0)
    mime: str = "application/octet-stream"
    modified: float | None = None


class SendMessageIn(BaseModel):
    text: str = Field(max_length=64000)
    reply_to: int | None = Field(default=None, ge=0)
    attachments: list[GroupFile] = Field(default_factory=list, max_length=20)


class MessageReactionIn(BaseModel):
    emoji: str = Field(max_length=32)


class MessagePatch(BaseModel):
    text: str = Field(max_length=64000)


class CursorIn(BaseModel):
    upto: int = Field(ge=-1)


class Reaction(BaseModel):
    emoji: str
    sender_id: str
    sender: str
    from_user: bool


class MessageOut(BaseModel):
    seq: int
    id: str
    sender_id: str
    sender: str
    sender_kind: Literal["human", "agent", "system"]
    from_user: bool
    kind: str
    text: str
    mentions: list[str]
    reply_to: int | None
    attachments: list[GroupFile] = Field(default_factory=list)
    at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    reactions: list[Reaction] = Field(default_factory=list)
    ui: list[dict[str, Any]] | None = None


class MessageList(BaseModel):
    entries: list[MessageOut]
    latest: int
    working: list[str] = Field(default_factory=list)
    read_by_all: int = -1
    read_by: dict[str, int] = Field(default_factory=dict)
    delivered_by: dict[str, int] = Field(default_factory=dict)
