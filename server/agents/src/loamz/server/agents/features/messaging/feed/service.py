"""Realtime is a hint over durable channels: reconnect and Redis outages both catch up from SQL."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.runtime.channel import ChannelChange
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.messaging.chats.service import chats
from loamz.server.agents.features.messaging.feed.model import FeedIn

log = logging.getLogger(__name__)
TOPIC = "loamz:messaging:changes"
POLL_SECONDS = 1
"""How long a feed waits for a wake-up before it looks again anyway (also how often it sends a keepalive)."""
TYPING_SECONDS = 5
"""A typing signal is a hint that expires on its own; it is never stored in the chat or the database."""


def event(value: dict) -> bytes:
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode()


class FeedService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._waiters: set[asyncio.Event] = set()
        self._redis = None
        self._task: asyncio.Task | None = None
        self._unsubscribe = None
        self._typing: dict[tuple[str, str], tuple[str, float]] = {}

    async def typing(self, caller: Caller, chat: str) -> None:
        """Say the caller is typing in a chat they belong to. Redis carries it between processes; without Redis it lives here."""
        service = self._c.get(chats)
        await service.require(caller, chat)
        roster = await service._store.members(chat)
        name = next(
            (
                m.name
                for m in roster
                if m.member == f"user:{caller.user_id}" and m.tenant == caller.tenant_id
            ),
            caller.name or caller.user_id,
        )
        key = f"typing:{caller.tenant_id}:{chat}:{caller.user_id}"
        if self._redis is not None:
            try:
                await asyncio.wait_for(self._redis.set(key, name, ex=TYPING_SECONDS), 1)
            except Exception:  # noqa: BLE001 - a lost typing hint costs nothing
                log.debug("Typing hint not shared")
        self._typing[(chat, caller.user_id)] = (name, time.monotonic() + TYPING_SECONDS)
        self._notify()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._redis.publish(TOPIC, "{}"), 1)

    async def _typing_names(self, caller: Caller, chat: str) -> list[str]:
        """Who else is typing in a chat right now: other people, never the caller."""
        now = time.monotonic()
        names: dict[str, str] = {}
        for (room, user), (name, until) in list(self._typing.items()):
            if until <= now:
                del self._typing[(room, user)]
            elif room == chat and user != caller.user_id:
                names[user] = name
        if self._redis is not None:
            try:
                prefix = f"typing:{caller.tenant_id}:{chat}:"
                async for key in self._redis.scan_iter(match=f"{prefix}*", count=100):
                    user = key.decode().removeprefix(prefix)
                    value = await self._redis.get(key)
                    if user != caller.user_id and value is not None:
                        names[user] = value.decode()
            except Exception:  # noqa: BLE001 - the local hints still answer
                log.debug("Typing hints from other processes unavailable")
        return sorted(names.values())

    def _notify(self) -> None:
        for waiter in self._waiters:
            waiter.set()

    async def _changed(self, change: ChannelChange) -> None:
        self._notify()
        if self._redis is not None:
            try:
                # Pointers only; each recipient must authorize and read the committed entry itself.
                await asyncio.wait_for(self._redis.publish(TOPIC, change.model_dump_json()), 1)
            except Exception:  # noqa: BLE001 - the database catch-up remains authoritative
                log.warning("Messaging fan-out unavailable; clients will catch up from the store")

    async def _listen(self) -> None:
        while True:
            try:
                async with self._redis.pubsub() as subscription:
                    await subscription.subscribe(TOPIC)
                    async for message in subscription.listen():
                        if message["type"] == "message":
                            self._notify()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect without abandoning active clients
                await asyncio.sleep(1)

    async def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self._c.runtime.store.channel_observe(self._changed)
        if self._c.settings.redis.url:
            import redis.asyncio as redis

            self._redis = redis.from_url(
                self._c.settings.redis.url, socket_connect_timeout=1, socket_timeout=2
            )
            self._task = asyncio.create_task(self._listen(), name="messaging-fanout")
        self._c.on_close(self.aclose)

    async def aclose(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._redis:
            await self._redis.aclose()

    async def stream(self, caller: Caller, body: FeedIn) -> AsyncIterator[bytes]:
        service = self._c.get(chats)
        since = dict(body.since)
        waiter = asyncio.Event()
        self._waiters.add(waiter)
        known: list[str] | None = None
        state: dict[str, tuple] = {}
        typed: dict[str, list[str]] = {}
        try:
            while True:
                waiter.clear()
                allowed = [
                    r.id for r in await service._store.list(caller.tenant_id, caller.user_id)
                ]
                if known != allowed:
                    yield event({"type": "ready" if known is None else "chats", "chats": allowed})
                    known = allowed
                for chat in allowed:
                    # Reauthorize each page: removal during a stream must revoke it too.
                    try:
                        while True:
                            messages = await service.read_messages(
                                caller, chat, after=since.get(chat, -1)
                            )
                            for entry in messages.entries:
                                yield event(
                                    {
                                        "type": "entry",
                                        "chat": chat,
                                        "entry": entry.model_dump(mode="json"),
                                    }
                                )
                                since[chat] = entry.seq
                            if len(messages.entries) < 200:
                                break
                    except NotFoundError:
                        continue
                    current = (
                        tuple(messages.working),
                        messages.read_by_all,
                        messages.read_by,
                        messages.delivered_by,
                    )
                    previous = state.get(chat)
                    if previous is None or previous[0] != current[0]:
                        yield event({"type": "working", "chat": chat, "names": messages.working})
                    if previous is None or previous[1:] != current[1:]:
                        yield event(
                            {
                                "type": "read",
                                "chat": chat,
                                "by_all": messages.read_by_all,
                                "by": messages.read_by,
                                "delivered": messages.delivered_by,
                            }
                        )
                    state[chat] = current
                    typing = await self._typing_names(caller, chat)
                    if typing != typed.get(chat, []):
                        typed[chat] = typing
                        yield event({"type": "typing", "chat": chat, "names": typing})
                try:
                    await asyncio.wait_for(waiter.wait(), POLL_SECONDS)
                except TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            self._waiters.discard(waiter)


def feed(container: Container) -> FeedService:
    return FeedService(container)
