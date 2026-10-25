"""Push only an opaque wake; chat identifiers and message text never leave through the push service."""

import asyncio
import contextlib
import logging

from loamz.engine.agents.kernel.runtime.channel import ChannelChange, EntryKind
from loamz.engine.errors import ServiceUnavailableError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.messaging.chats.service import chats
from loamz.server.agents.features.messaging.push.store import PushStore

log = logging.getLogger(__name__)


class PushService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self.store = PushStore(container.database)
        self._unsubscribe = None
        self._tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(4)

    def require_configured(self) -> None:
        if (
            not self._c.settings.push.vapid_private_key
            or not self._c.settings.push.vapid_public_key
        ):
            raise ServiceUnavailableError("Push notifications are not configured")

    async def start(self) -> None:
        self._unsubscribe = self._c.runtime.store.channel_observe(self._observe)
        self._c.on_close(self.aclose)

    async def _observe(self, change: ChannelChange) -> None:
        if (
            change.kind != EntryKind.MESSAGE
            or not change.channel.startswith("group/")
            or not self._c.settings.push.vapid_private_key
        ):
            return
        # A burst must not create an unbounded backlog. The durable feed still delivers every message.
        if len(self._tasks) >= 100:
            return
        task = asyncio.create_task(self._send(change))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, change: ChannelChange) -> None:
        async with self._slots:
            try:
                roster = await self._c.get(chats)._store.members(
                    change.channel.removeprefix("group/")
                )
                for member in roster:
                    if not member.member.startswith("user:") or (
                        change.sender.type == "user"
                        and member.member == f"user:{change.sender.key}"
                    ):
                        continue
                    for subscription in await self.store.for_user(member.tenant, member.member[5:]):
                        await asyncio.to_thread(self.deliver, subscription)
            except Exception:  # noqa: BLE001 - a push failure must not fail the committed message
                log.warning("A messenger wake could not be delivered")

    def deliver(self, subscription: dict) -> None:
        import requests
        from pywebpush import webpush

        with requests.Session() as session:
            session.max_redirects = 0
            webpush(
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
                },
                data='{"wake":1}',
                vapid_private_key=self._c.settings.push.vapid_private_key,
                vapid_claims={"sub": self._c.settings.push.contact},
                ttl=60,
                timeout=5,
                requests_session=session,
            )

    async def aclose(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        for task in self._tasks:
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self.store.erase(tenant_id, user_id)


def push(container: Container) -> PushService:
    return PushService(container)
