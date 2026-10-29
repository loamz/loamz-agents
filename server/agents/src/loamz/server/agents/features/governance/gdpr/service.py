from __future__ import annotations

import logging
from typing import Annotated

from loamz.runtime.kernel.runtime.persistence.store import RuntimeStore
from loamz.runtime.kernel.types.identity import Actor
from loamz.runtime.stores.workspace.layout import tenant_prefix, user_prefix
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.governance.gdpr.model import ErasureSummary

logger = logging.getLogger(__name__)


def _erasers() -> tuple[tuple[str, object], ...]:
    """Every feature that keeps something about a person, by name and provider. Adding a feature that keeps personal data means adding it
    here: a test fails while any service with an `erase` function is missing from this list."""
    from loamz.server.agents.features.automation.schedules.service import schedules
    from loamz.server.agents.features.conversations.audio.service import audio
    from loamz.server.agents.features.conversations.chat.service import chat
    from loamz.server.agents.features.conversations.feedback.service import feedback
    from loamz.server.agents.features.conversations.memory.service import memory as notes
    from loamz.server.agents.features.conversations.shares.service import shares
    from loamz.server.agents.features.conversations.tasks.service import tasks
    from loamz.server.agents.features.conversations.threads.service import threads
    from loamz.server.agents.features.directory.agents.service import agents
    from loamz.server.agents.features.directory.pairs.service import pairs
    from loamz.server.agents.features.directory.pins.service import pins
    from loamz.server.agents.features.governance.admin.service import admin
    from loamz.server.agents.features.governance.connector_tokens.service import connector_tokens
    from loamz.server.agents.features.governance.entitlements.service import entitlements
    from loamz.server.agents.features.governance.health.service import health
    from loamz.server.agents.features.governance.rate_limit.service import rate_limit
    from loamz.server.agents.features.governance.workspace_oauth.service import workspace_oauth
    from loamz.server.agents.features.knowledge.documents.service import documents
    from loamz.server.agents.features.knowledge.enrichment.service import enrichment
    from loamz.server.agents.features.knowledge.knowledge.service import knowledge
    from loamz.server.agents.features.knowledge.rag.service import rag
    from loamz.server.agents.features.messaging.chats.service import chats
    from loamz.server.agents.features.messaging.push.service import push
    from loamz.server.agents.features.people.connections.service import connections
    from loamz.server.agents.features.people.export.service import export
    from loamz.server.agents.features.people.marketplace.service import marketplace
    from loamz.server.agents.features.people.memory.service import memory
    from loamz.server.agents.features.people.notifications.service import notifications
    from loamz.server.agents.features.people.preferences.service import preferences
    from loamz.server.agents.features.people.skills.service import skills
    from loamz.server.agents.features.people.usage.service import usage
    from loamz.server.agents.features.workspace.artifacts.service import artifacts
    from loamz.server.agents.features.workspace.files.service import files
    from loamz.server.agents.features.workspace.workspace.service import workspace

    return (
        ("schedules", schedules), ("conversation_notes", notes), ("threads", threads), ("tasks", tasks), ("chat", chat),
        ("audio", audio), ("feedback", feedback), ("shares", shares), ("agents", agents), ("pairs", pairs), ("pins", pins),
        ("admin", admin), ("connector_tokens", connector_tokens), ("entitlements", entitlements), ("health", health),
        ("rate_limit", rate_limit), ("workspace_oauth", workspace_oauth), ("documents", documents), ("enrichment", enrichment),
        ("knowledge", knowledge), ("rag", rag), ("group_chats", chats), ("push", push), ("connections", connections),
        ("export", export), ("marketplace", marketplace), ("memory", memory), ("notifications", notifications),
        ("preferences", preferences), ("skills", skills), ("usage", usage), ("artifacts", artifacts), ("files", files),
        ("workspace", workspace),
    )  # fmt: skip


class GdprService:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def _erase_session(self, tenant_id: str, session: str) -> int:
        """One conversation's history, task boards and state, under the plain id the chat service writes them with and under the
        tenant-prefixed name `store.tenant(t)` uses: erasing only the second would leave what the server actually wrote."""
        store = self._c.agent_store
        erased = await store.tenant(tenant_id).erase_conversation(session)
        count = erased.thread_nodes
        for facet, attribute in (
            ("threads", "threads"),
            ("tasks", "tasks"),
            ("session", "session_state"),
        ):
            if await store.has(facet):
                count += await getattr(store, attribute).erase_under(session)
        return count

    async def erase_user(self, tenant_id: str, user_id: str) -> ErasureSummary:
        """Removes everything this server holds about one person: their conversations (history, task boards, state and the run journal),
        their agents and everything those hold, schedules, notes, memories, files, connections and settings. Other people's data is left alone.
        Counts are what was really removed."""
        c = self._c
        store = c.agent_store
        removed: dict[str, int] = {}

        thread_ids: list[str] = []
        if await store.has("thread_headers"):
            thread_ids = list(
                await store.thread_headers.owned_ids(
                    tenant_id, user_id, limit=None, include_deleted=True
                )
            )
        journal = RuntimeStore(c.database) if await store.has("runtime") else None
        scope = store.tenant(tenant_id)
        messages = 0
        for thread_id in thread_ids:
            messages += await self._erase_session(tenant_id, thread_id)
            if journal:
                removed["runs"] = removed.get("runs", 0) + await journal.erase(
                    tenant=tenant_id, thread_id=thread_id
                )

        # What the person's agents said in groups lives in a session of its own per agent and chat, with no header: find them before the
        # agents are gone.
        lanes: list = []
        try:
            async with c.database.transaction() as tx:
                lanes = await tx.fetchall(
                    "SELECT chat_id, member FROM chat_members WHERE tenant = ? AND owner = ? AND member LIKE 'agent:%'",
                    tenant_id,
                    user_id,
                )
        except Exception:  # noqa: BLE001 - the group chats were never used here, so there is no such table and nothing to remove
            lanes = []
        for row in lanes:
            session = f"{str(row['member']).removeprefix('agent:')}@{row['chat_id']}"
            messages += await self._erase_session(tenant_id, session)
            if journal:
                removed["runs"] = removed.get("runs", 0) + await journal.erase(
                    tenant=tenant_id, agent=Actor(type="agent", key=session)
                )
        erased = await scope.erase(user_id)
        removed["conversations"] = len(thread_ids) + len(lanes)
        removed["messages"] = messages + erased.thread_nodes

        for name, provider in _erasers():
            try:
                removed[name] = int(await c.get(provider).erase(tenant_id, user_id) or 0)  # type: ignore[attr-defined]
            except Exception:
                # One feature failing must not leave the rest of the person's data behind: say so loudly, and let the caller see it did not finish.
                logger.exception("Erasing %s for %s failed", name, user_id)
                removed[name] = -1

        objects = await c.file_store.delete_prefix(user_prefix(tenant_id, user_id))
        failed = sorted(name for name, count in removed.items() if count < 0)
        return ErasureSummary(
            tenant_id=tenant_id,
            user_id=user_id,
            conversations_deleted=removed["conversations"],
            objects_deleted=objects,
            memories_deleted=max(removed.get("memory", 0), 0)
            + max(removed.get("conversation_notes", 0), 0),
            removed=removed,
            status="incomplete" if failed else "completed",
            failed=failed,
        )

    async def erase_tenant(self, tenant_id: str) -> ErasureSummary:
        files = self._c.file_store
        pfx = tenant_prefix(tenant_id)
        deleted_count = await files.delete_prefix(pfx)

        try:
            store = self._c.agent_store.tenant(tenant_id)
            threads = await store.threads.list()
            for th in threads:
                try:
                    await store.threads.delete(th.id)
                except Exception:
                    pass
        except Exception:
            pass

        return ErasureSummary(
            tenant_id=tenant_id,
            conversations_deleted=0,
            objects_deleted=deleted_count,
            memories_deleted=0,
            status="completed",
        )


def gdpr(c: Container) -> GdprService:
    return GdprService(c)


GdprServiceDep = Annotated[GdprService, None]
