from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.conversations.memory.model import (
    NoteHistory,
    NoteIn,
    NoteList,
    NoteOut,
    NotePatch,
    SearchOut,
    SummaryOut,
)
from loamz.server.agents.features.conversations.memory.service import MemoryService, memory

Service = Annotated[MemoryService, Depends(use(memory))]

router = APIRouter(prefix="/api/v1", tags=["memory"])


@router.get("/threads/{thread_id}/notes", response_model=NoteList)
async def notes(
    thread_id: str, caller: CallerDep, service: Service, history: bool = False
) -> NoteList:
    """The notes in force: instructions, decisions, facts, tasks and dates. With `history`, every note ever recorded, replaced and retired ones too."""
    return await service.notes(caller, thread_id, history=history)


@router.get("/notes/{note_id}/history", response_model=NoteHistory)
async def note_history(note_id: str, caller: CallerDep, service: Service) -> NoteHistory:
    """What happened to a note, in order, with the time the server recorded each step."""
    return await service.history(caller, note_id)


@router.post(
    "/threads/{thread_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED
)
async def add_note(thread_id: str, body: NoteIn, caller: CallerDep, service: Service) -> NoteOut:
    return await service.add(caller, thread_id, body)


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def change_note(
    note_id: str, body: NotePatch, caller: CallerDep, service: Service
) -> NoteOut:
    """Replace a note's text (a new note that replaces it), finish, retire, dismiss or reopen it, or pin it. Nothing is deleted."""
    return await service.patch(caller, note_id, body)


@router.get("/threads/{thread_id}/summary", response_model=SummaryOut | None)
async def summary(thread_id: str, caller: CallerDep, service: Service) -> SummaryOut | None:
    """What the agent holds in place of the older messages, or null while it still has them all in front of it."""
    return await service.summary(caller, thread_id)


@router.get("/threads/{thread_id}/search", response_model=SearchOut)
async def search(
    thread_id: str,
    caller: CallerDep,
    service: Service,
    q: str = "",
    from_date: str = Query(
        default="", alias="from", description="Only messages on or after this day, YYYY-MM-DD."
    ),
    to_date: str = Query(
        default="", alias="to", description="Only messages on or before this day, YYYY-MM-DD."
    ),
    who: Literal["", "person", "agent", "tool"] = "",
    limit: int = 30,
) -> SearchOut:
    """Search everything said in the conversation, including what summaries left out."""
    return await service.search(
        caller, thread_id, q, from_date=from_date, to_date=to_date, who=who, limit=limit
    )
