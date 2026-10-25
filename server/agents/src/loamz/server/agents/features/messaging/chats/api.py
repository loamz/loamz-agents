"""Versioned messenger routes; domain failures become RFC 9457 problems."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.messaging.chats.model import (
    CursorIn,
    GroupIn,
    GroupList,
    GroupOut,
    GroupPatch,
    MemberIn,
    MemberPatch,
    MessageList,
    MessageOut,
    MessagePatch,
    MessageReactionIn,
    SendMessageIn,
)
from loamz.server.agents.features.messaging.chats.service import ChatsService, chats

Service = Annotated[ChatsService, Depends(use(chats))]
router = APIRouter(prefix="/api/v1/groups", tags=["messaging"])


@router.get("", response_model=GroupList)
async def list_chats(caller: CallerDep, service: Service) -> GroupList:
    return await service.list(caller)


@router.post("", response_model=GroupOut)
async def create_chat(body: GroupIn, caller: CallerDep, service: Service) -> GroupOut:
    return await service.create(caller, body)


@router.get("/{chat}", response_model=GroupOut)
async def get_chat(chat: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.get(caller, chat)


@router.patch("/{chat}", response_model=GroupOut)
async def update_chat(chat: str, body: GroupPatch, caller: CallerDep, service: Service) -> GroupOut:
    return await service.patch(caller, chat, body)


@router.delete("/{chat}", status_code=204)
async def delete_chat(chat: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, chat)
    return Response(status_code=204)


@router.put("/{chat}/avatar", response_model=GroupOut)
async def avatar(
    chat: str, caller: CallerDep, service: Service, file: UploadFile = File(...)
) -> GroupOut:
    return await service.set_avatar(caller, chat, await file.read(5 * 1024 * 1024 + 1))


@router.delete("/{chat}/avatar", response_model=GroupOut)
async def clear_avatar(chat: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.clear_avatar(caller, chat)


@router.put("/{chat}/pin", response_model=GroupOut)
async def pin(chat: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.pin(caller, chat, True)


@router.delete("/{chat}/pin", response_model=GroupOut)
async def unpin(chat: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.pin(caller, chat, False)


@router.post("/{chat}/members", response_model=GroupOut)
async def add_member(chat: str, body: MemberIn, caller: CallerDep, service: Service) -> GroupOut:
    return await service.add_member(caller, chat, body)


@router.patch("/{chat}/members/{member}", response_model=GroupOut)
async def change_member(
    chat: str, member: str, body: MemberPatch, caller: CallerDep, service: Service
) -> GroupOut:
    return await service.change_member(caller, chat, member, body)


@router.delete("/{chat}/members/{member}", response_model=GroupOut)
async def remove_member(chat: str, member: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.remove_member(caller, chat, member)


@router.get("/{chat}/messages", response_model=MessageList)
async def messages(
    chat: str,
    caller: CallerDep,
    service: Service,
    after: int = Query(-1, ge=-1),
    wait: float = Query(0, ge=0, le=30),
) -> MessageList:
    return await service.read_messages(caller, chat, after=after, wait=wait)


@router.post("/{chat}/messages", response_model=MessageOut)
async def send(chat: str, body: SendMessageIn, caller: CallerDep, service: Service) -> MessageOut:
    return await service.send_message(caller, chat, body)


@router.patch("/{chat}/messages/{seq}", response_model=MessageOut)
async def edit(
    chat: str, seq: int, body: MessagePatch, caller: CallerDep, service: Service
) -> MessageOut:
    return await service.edit_message(caller, chat, seq, body.text)


@router.delete("/{chat}/messages/{seq}", response_model=MessageOut)
async def tombstone(chat: str, seq: int, caller: CallerDep, service: Service) -> MessageOut:
    return await service.delete_message(caller, chat, seq)


@router.put("/{chat}/messages/{seq}/reaction", response_model=MessageOut)
async def react(
    chat: str, seq: int, body: MessageReactionIn, caller: CallerDep, service: Service
) -> MessageOut:
    return await service.react(caller, chat, seq, body.emoji)


@router.post("/{chat}/read", status_code=204)
async def read(chat: str, body: CursorIn, caller: CallerDep, service: Service) -> Response:
    await service.mark_read(caller, chat, body.upto)
    return Response(status_code=204)


@router.post("/{chat}/delivered", status_code=204)
async def delivered(chat: str, body: CursorIn, caller: CallerDep, service: Service) -> Response:
    await service.mark_read(caller, chat, body.upto, delivered=True)
    return Response(status_code=204)
