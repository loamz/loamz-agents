"""The background pass over idle conversations: summarize the long ones and bring their notes up to date, so the person's next message does not
carry (and pay for) a huge prompt the provider's cache has forgotten."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from loamz.engine.errors import LoamzError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.features.conversations.memory.service import memory
from loamz.server.agents.features.conversations.memory.store import MemoryStore

if TYPE_CHECKING:
    from loamz.server.agents.bootstrap.container import Container

logger = logging.getLogger(__name__)

LOOK_BACK_DAYS = 14
"""A conversation idle for longer than this is not worth summarizing before the person comes back."""
PER_PASS = 20
CLAIM_FOR_SECONDS = 600


async def sweep_once(container: Container, *, now: float | None = None) -> int:
    """One pass: how many conversations were summarized or had their notes brought up to date."""
    cfg = container.settings.context
    now = time.time() if now is None else now
    try:
        container.require_model()
    except LoamzError:
        return 0  # nothing to write summaries with
    quiet_since = datetime.fromtimestamp(now - cfg.idle_minutes * 60, UTC).isoformat()
    not_before = (datetime.fromtimestamp(now, UTC) - timedelta(days=LOOK_BACK_DAYS)).isoformat()
    async with container.database.transaction() as tx:
        rows = await tx.fetchall(
            "SELECT n.session_id AS session_id, h.tenant_id AS tenant_id, h.user_id AS user_id, h.agent_id AS agent_id, COUNT(*) AS total "
            "FROM thread_nodes n JOIN thread_headers h ON h.id = n.session_id AND h.deleted_at IS NULL "
            "GROUP BY n.session_id, h.tenant_id, h.user_id, h.agent_id "
            "HAVING MAX(n.created_at) < ? AND MAX(n.created_at) > ? ORDER BY MAX(n.created_at) DESC LIMIT ?",
            quiet_since, not_before, PER_PASS,
        )  # fmt: skip
    store = MemoryStore(container.database)
    service = container.get(memory)
    history = container.agent_store.threads
    done = 0
    for row in rows:
        tenant, session = row["tenant_id"], row["session_id"]
        if (await store.state(tenant, session)).idle_through >= row["total"]:
            continue  # nothing new since the last pass
        if not await store.claim(tenant, session, now, CLAIM_FOR_SECONDS):
            continue  # another server has it
        owner = Caller(tenant_id=tenant, user_id=row["user_id"])
        try:
            model = await _model_of(container, owner, row["agent_id"])
            branch = await history.get_branch(session, "main")
            if branch is None or branch.head_message_id is None:
                continue
            _, checkpointer = container.conversation_memory(history, session, model)
            checkpoint = await checkpointer.checkpoint(
                session, branch.head_message_id, trigger_ratio=cfg.idle_ratio
            )  # type: ignore[attr-defined]
            if checkpoint is not None:
                await history.save_checkpoint(checkpoint)
            changed = await service.update_notes(
                owner, session, history, model, min_new=8 if checkpoint is None else 1
            )
            await store.set_state(tenant, session, idle_through=row["total"])
            done += 1 if (checkpoint is not None or changed) else 0
        except Exception:  # noqa: BLE001 - one conversation must not stop the pass
            logger.exception("The background summary of a conversation failed")
    return done


async def _model_of(container: Container, owner: Caller, agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    from loamz.server.agents.features.directory.agents.service import agents

    try:
        return (await container.get(agents).get_owned(owner, agent_id)).model
    except LoamzError:
        return None


def start_sweeper(container: Container) -> Callable[[], Awaitable[None]]:
    """Run `sweep_once` every `sweep_seconds` until the container closes (returns the closer)."""

    async def loop() -> None:
        while True:
            await asyncio.sleep(container.settings.context.sweep_seconds)
            try:
                await sweep_once(container)
            except Exception:  # noqa: BLE001
                logger.exception("The idle sweep failed; it will try again")

    task = asyncio.create_task(loop(), name="conversation-sweeper")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
