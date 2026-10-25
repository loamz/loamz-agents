"""Realtime is a hint over durable channels: reconnect and Redis outages both catch up from SQL."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.runtime.channel import ChannelChange
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.messaging.chats.service import chats
from loamz.server.agents.features.messaging.feed.model import FeedIn

log = logging.getLogger(__name__)
TOPIC = "loamz:messaging:changes"


def event(value: dict) -> bytes:
    return f"data: {json.dumps(value, separators=(',', ':'))}\n\n".encode()


class FeedService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._waiters: set[asyncio.Event] = set()
        self._redis = None
        self._task: asyncio.Task | None = None
        self._unsubscribe = None

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
                try:
                    await asyncio.wait_for(waiter.wait(), 1)
                except TimeoutError:
                    yield b": keepalive\n\n"
        finally:
            self._waiters.discard(waiter)


def feed(container: Container) -> FeedService:
    return FeedService(container)
