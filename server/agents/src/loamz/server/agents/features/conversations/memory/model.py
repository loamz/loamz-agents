from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["instruction", "decision", "fact", "task", "date", "file", "person"]
Status = Literal["active", "done", "superseded", "retired", "dismissed"]
Basis = Literal["stated", "inferred"]
KINDS: tuple[str, ...] = ("instruction", "decision", "fact", "task", "date", "file", "person")

MAX_NOTE_CHARS = 300
MAX_ACTIVE_NOTES = 60
MAX_REASON_CHARS = 200


class NoteIn(BaseModel):
    kind: Kind = "fact"
    text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


class NotePatch(BaseModel):
    """A change to a note. Nothing is overwritten or deleted: new text is a new note that replaces the old one, and a status change is
    a recorded event. The time of every change is the server's."""

    text: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_CHARS)
    status: Literal["active", "done", "retired", "dismissed"] | None = None
    """`active` takes a finished, retired or dismissed note back into use."""
    pinned: bool | None = None
    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS)


class NoteOut(BaseModel):
    id: str
    kind: Kind
    text: str
    status: Status
    pinned: bool
    source: list[int] = Field(default_factory=list)
    """The numbers of the messages it came from."""
    basis: Basis = "stated"
    """`stated`: the person said it. `inferred`: the agent or writer concluded it."""
    written_by: Literal["writer", "agent", "person"]
    effective_at: datetime
    """When it was recorded and took effect: set by the server, never by a model."""
    zone: str = "UTC"
    """The person's time zone when it was recorded (IANA name), so the day it shows is the day they were in."""
    ended_at: datetime | None = None
    """When it stopped applying (replaced, retired or dismissed), and why."""
    ended_reason: str | None = None
    supersedes_id: str | None = None
    replaced_by: str | None = None


class NoteEventOut(BaseModel):
    event: str
    at: datetime
    zone: str = "UTC"
    by: Literal["writer", "agent", "person"]
    reason: str = ""
    related_id: str | None = None


class NoteList(BaseModel):
    items: list[NoteOut]


class NoteHistory(BaseModel):
    note: NoteOut
    events: list[NoteEventOut]


class SummaryOut(BaseModel):
    """What the agent holds in place of the older messages."""

    covers: int
    """The first this many messages are summarized (every one is still kept and searchable)."""
    total: int
    text: str
    at: datetime
    rebuilt_from_originals: bool = False


class SearchHit(BaseModel):
    number: int
    at: datetime
    who: Literal["person", "agent", "tool"]
    snippet: str


class SearchOut(BaseModel):
    items: list[SearchHit]
    total: int
