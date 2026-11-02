from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class ChatMessageIn(BaseModel):
    role: str = "user"
    content: str = ""
    attachments: list[str] = Field(default_factory=list)


_CATALOG = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_CATALOGS = 16


class DataContext(BaseModel):
    """Which Loamz Data workspace this conversation is about, and which of its catalogs the person chose to look at.

    The application states it for one conversation (it is not the person's identity, which only the verified sign-in gives). Loamz
    Data still decides what the person may read: naming a workspace they are not in gets nothing, and a choice of catalogs can
    only take away from what they may read.
    """

    workspace: str = Field(pattern=r"^[0-9a-f-]{8,64}$")
    catalogs: list[str] = Field(default_factory=list, max_length=MAX_CATALOGS)

    @field_validator("catalogs")
    @classmethod
    def _names(cls, names: list[str]) -> list[str]:
        for name in names:
            if not _CATALOG.fullmatch(name):
                raise ValueError(f"{name!r} is not a catalog name")
        if len(",".join(names)) > 256:  # a user attribute is short text
            raise ValueError("too many catalogs")
        return sorted(set(names))

    def attributes(self) -> dict[str, str]:
        """As the run's user attributes (`loamz.data/workspace`, and `loamz.data/catalogs` when some were chosen)."""
        out = {"loamz.data/workspace": self.workspace}
        if self.catalogs:
            out["loamz.data/catalogs"] = ",".join(self.catalogs)
        return out


class ChatRequest(BaseModel):
    thread_id: str
    messages: list[ChatMessageIn] = Field(default_factory=list)
    model: str | None = None
    agent_id: str | None = None
    attachments: list[dict[str, object]] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    system_instructions: str | None = None
    prompt: str | None = None
    data: DataContext | None = None
    helper: bool = False
    """A helper's run (see `chat/helpers.py`): read-only tools, no approvals, and no helpers, schedules or notes of its own."""

    def prompt_text(self) -> str:
        if self.prompt:
            return self.prompt
        for m in reversed(self.messages):
            if m.role == "user" and m.content.strip():
                return m.content
        return ""


class HitlResponse(BaseModel):
    action: str = "approve"
    modified_args: dict[str, object] | None = None
    reason: str | None = None


class HitlStatus(BaseModel):
    status: str = "idle"
    pending: list[dict[str, object]] = Field(default_factory=list)


class ApprovalOut(BaseModel):
    request_id: str
    thread_id: str
    run_id: str
    tool_name: str
    tool_args: dict[str, object] = Field(default_factory=dict)
    requested_at: str | None = None


class CancelResponse(BaseModel):
    thread_id: str
    cancelled: bool = True
