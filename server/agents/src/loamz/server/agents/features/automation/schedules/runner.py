"""Runs schedules. Each firing is recorded before anything runs, and the schedule moves to its next time in the same step, so a crash leaves a
firing waiting (it is picked up again when its lease runs out) and never a lost one.

What is promised: every firing runs at least once, never two at the same moment, and what an agent does outside (send, book, delete) is asked
about first like anywhere else. It is not promised that an outside effect happens exactly once if the server dies in the middle of a run.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loamz.engine.errors import ConflictError, LoamzError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.types.content import Role
from loamz.server.agents.features.automation.schedules.cron import describe, next_run
from loamz.server.agents.features.automation.schedules.model import (
    MAX_FAILURES_IN_A_ROW,
    MAX_RUNS_PER_DAY,
)
from loamz.server.agents.features.automation.schedules.service import schedules
from loamz.server.agents.features.automation.schedules.store import Firing, Schedule

if TYPE_CHECKING:
    from loamz.server.agents.bootstrap.container import Container

logger = logging.getLogger(__name__)

LEASE_SECONDS = 600.0
HEARTBEAT_SECONDS = 120.0
MAX_ATTEMPTS = 3
TOO_LATE_FOR_ONCE = 6 * 3600
NOTHING = "[NOTHING_TO_REPORT]"


async def tick(container: Container, *, now: float | None = None) -> list[asyncio.Task[None]]:
    """Record what has come due, take the firings that are waiting, and start running them. The tasks started (so a test can wait for them)."""
    now = time.time() if now is None else now
    service = container.get(schedules)
    store = service.store
    for s in await store.due(now):
        due = s.next_run_at or now
        # The next time is worked out from now, not from the missed one: after a long stop a schedule runs once, not once per missed time.
        upcoming = next_run(
            s.kind,
            cron=s.cron,
            interval_seconds=s.interval_seconds,
            run_at=s.run_at,
            tz=s.timezone,
            after=now,
        )
        firing_id = await store.begin_firing(s, due, upcoming)
        if firing_id and s.kind == "once" and now - due > TOO_LATE_FOR_ONCE:
            await store.finish(
                s.tenant_id, firing_id, "missed", summary="The server was not running at that time."
            )
            await _tell(
                container,
                s,
                "Missed: " + s.name,
                "It was set for a time when nothing was running, and was not run late.",
            )
    started: list[asyncio.Task[None]] = []
    for firing in await store.claim(now, LEASE_SECONDS):
        task = asyncio.ensure_future(_guarded(container, firing))
        container.__dict__.setdefault("_schedule_tasks", set()).add(task)
        task.add_done_callback(container.__dict__["_schedule_tasks"].discard)
        started.append(task)
    return started


async def _guarded(container: Container, firing: Firing) -> None:
    try:
        await execute(container, firing)
    except Exception as exc:  # noqa: BLE001 - one firing must not stop the rest
        logger.exception("A scheduled run failed")
        store = container.get(schedules).store
        again = firing.attempt < MAX_ATTEMPTS and not isinstance(
            exc, (NotFoundError, ConflictError)
        )
        if again:
            # Put it back: temporary trouble (the model, the network) is tried again after the lease runs out.
            await store.finish(
                firing.tenant_id, firing.id, "pending", summary="", error=str(exc)[:300]
            )
        else:
            await store.finish(firing.tenant_id, firing.id, "failed", error=str(exc)[:300])
            await _failed(container, firing.tenant_id, firing.schedule_id, str(exc))


async def execute(container: Container, firing: Firing) -> None:
    service = container.get(schedules)
    store = service.store
    s = await store.by_id(firing.tenant_id, firing.schedule_id)
    if s is None:
        await store.finish(
            firing.tenant_id, firing.id, "skipped", summary="The schedule was deleted."
        )
        return
    caller = Caller(tenant_id=s.tenant_id, user_id=s.user_id)
    now = time.time()
    try:
        agent = await service._agent(caller, s.agent_id)  # noqa: SLF001
    except NotFoundError:
        await store.change(s.tenant_id, s.id, status="paused")
        await store.finish(
            firing.tenant_id, firing.id, "failed", error="The agent no longer exists."
        )
        await _tell(container, s, f"Paused: {s.name}", "Its agent no longer exists.")
        return
    midnight = (
        datetime.fromtimestamp(now, UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    if await store.started_since(s.tenant_id, s.user_id, midnight) > MAX_RUNS_PER_DAY:
        await store.finish(
            firing.tenant_id,
            firing.id,
            "skipped",
            summary=f"Over today's {MAX_RUNS_PER_DAY} scheduled runs.",
        )
        return
    if await store.running_for(s.tenant_id, s.id, now, firing.id):
        await store.finish(
            firing.tenant_id, firing.id, "skipped", summary="The last run is still going."
        )
        return
    container.require_model()

    from loamz.server.agents.features.conversations.chat.model import ChatRequest
    from loamz.server.agents.features.conversations.chat.service import chat
    from loamz.server.agents.features.conversations.threads.service import SCHEDULED_TAG, threads
    from loamz.server.agents.features.directory.agents.service import agents

    # A quiet schedule works in a thread of its own (so its "nothing to report" stays out of the chat); otherwise in the agent's chat.
    if s.quiet:
        thread_id = s.thread_id
        if thread_id is None:
            made = await container.get(threads).create_for(
                s.tenant_id, s.user_id, name=s.name, tags=(SCHEDULED_TAG,)
            )  # not labelled with the agent: that would make it the agent's chat
            thread_id = made.id
            await store.change(s.tenant_id, s.id, thread_id=thread_id)
    else:
        thread_id = (await container.get(agents).open_thread(caller, s.agent_id)).id
    words = describe(
        s.kind, cron=s.cron, interval_seconds=s.interval_seconds, run_at=s.run_at, tz=s.timezone
    )
    prompt = f"Scheduled task “{s.name}” ({words}).\n{s.prompt}"
    if s.quiet:
        prompt += f"\nIf there is nothing worth telling the person, answer with exactly {NOTHING} and nothing else."
    request = ChatRequest(thread_id=thread_id, prompt=prompt, agent_id=s.agent_id)
    chat_service = container.get(chat)
    try:
        await chat_service.admit(caller, request)
    except ConflictError:
        await store.finish(
            firing.tenant_id, firing.id, "skipped", summary="The agent was busy with you."
        )
        return

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await store.extend(firing.tenant_id, firing.id, time.time() + LEASE_SECONDS)

    async def work() -> None:
        async for line in chat_service.chat(caller, request):
            if '"type": "run.failed"' in line:
                try:
                    why = json.loads(line.split("data: ", 1)[1]).get("error", "the run failed")
                except IndexError, ValueError:
                    why = "the run failed"
                raise RuntimeError(why)

    heart = asyncio.ensure_future(beat())
    try:
        await asyncio.wait_for(work(), timeout=s.approval_timeout_s + 600)
    except TimeoutError:
        # Waiting on an approval nobody gave: stop it, and go on with the next time.
        with contextlib.suppress(LoamzError):
            await chat_service.cancel(caller, thread_id)
        await store.finish(
            firing.tenant_id, firing.id, "skipped", summary="Waited too long for an approval."
        )
        await _tell(
            container,
            s,
            f"Skipped: {s.name}",
            "It was waiting for your approval and the time ran out.",
        )
        return
    finally:
        heart.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heart

    answer = await _last_answer(container, thread_id)
    await store.change(s.tenant_id, s.id, last_run_at=time.time(), failures=0)
    if s.quiet and (not answer or answer.strip() == NOTHING):
        await store.finish(firing.tenant_id, firing.id, "succeeded", summary="Nothing to report.")
        return
    await store.finish(firing.tenant_id, firing.id, "succeeded", summary=answer)
    await _tell(
        container, s, f"{agent.name}: {s.name}", answer, thread_id=None if s.quiet else thread_id
    )
    if s.email_results and s.owner_email and answer:
        await _email(container, s, f"{agent.name}: {s.name}", answer)


async def _last_answer(container: Container, thread_id: str) -> str:
    nodes = await container.agent_store.threads.session_nodes(thread_id)
    for node in reversed(nodes):
        if node.payload.role == Role.ASSISTANT and node.payload.text.strip():
            return node.payload.text.strip()
    return ""


async def _tell(
    container: Container, s: Schedule, title: str, body: str, *, thread_id: str | None = None
) -> None:
    from loamz.server.agents.features.people.notifications.service import notifications

    try:
        await container.get(notifications).notify(
            s.tenant_id, s.user_id, title, body[:500], kind="schedule", thread_id=thread_id
        )
    except Exception:  # noqa: BLE001 - telling the person is best effort
        logger.exception("Could not notify about a scheduled run")


async def _email(container: Container, s: Schedule, subject: str, text: str) -> None:
    from loamz.server.agents.features.people.notifications.service import notifications

    await container.get(notifications).email(s.owner_email, subject, text)  # never raises


async def _failed(container: Container, tenant: str, schedule_id: str, why: str) -> None:
    store = container.get(schedules).store
    s = await store.by_id(tenant, schedule_id)
    if s is None:
        return
    failures = s.failures + 1
    await store.change(
        tenant,
        schedule_id,
        failures=failures,
        **({"status": "paused"} if failures >= MAX_FAILURES_IN_A_ROW else {}),
    )
    note = " It is paused until you turn it back on." if failures >= MAX_FAILURES_IN_A_ROW else ""
    await _tell(container, s, f"Could not run: {s.name}", f"{why[:200]}{note}")


def start_runner(container: Container, every: float = 30.0) -> Callable[[], Awaitable[None]]:
    """Look for due schedules every `every` seconds until the container closes (returns the closer)."""

    async def loop() -> None:
        while True:
            try:
                await tick(container)
            except Exception:  # noqa: BLE001
                logger.exception("The schedule runner failed; it will try again")
            await asyncio.sleep(every)

    task = asyncio.create_task(loop(), name="schedule-runner")

    async def stop() -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for running in list(container.__dict__.get("_schedule_tasks", ())):
            running.cancel()

    return stop
