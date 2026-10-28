"""Helpers: a chat agent can hand a few separate pieces of work to helpers that run side by side, and gets back one short report from each.

A helper is the same agent (its instructions, model and what it knows) given one job, working in a hidden conversation of its own. It can only
look things up and think: it uses read-only tools, is never asked for approval, and cannot start helpers or change schedules. The agent that
started them sees only their reports, which keeps its own conversation small.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from loamz.engine.errors import LoamzError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import Role, TextBlock

if TYPE_CHECKING:
    from loamz.server.agents.bootstrap.container import Container

logger = logging.getLogger(__name__)

MAX_HELPERS = 4
HELPER_SECONDS = 300
REPORT_CHARS = 3_000
HELPERS_PER_DAY = 100

RULES = (
    "You are a helper started by another assistant to do ONE job. Do just that job with the tools you have (you can only look things up "
    "and think; you cannot change, send, book or delete anything). Finish with a short, complete report of what you found or concluded: "
    "the facts, numbers and sources the assistant needs, and nothing it did not ask for. Do not ask questions."
)


def _text(body: str, *, error: bool = False) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=body)], is_error=error)


class StartHelpersTool:
    name = "start_helpers"
    risk = ToolRisk.SAFE
    idempotent = False
    description = (
        f"Hand up to {MAX_HELPERS} separate pieces of work to helpers that run at the same time (for example researching three topics, or "
        "checking several things). Each helper works on its own job and gives back a short report. They can look things up but cannot change "
        "anything, so use them for finding out, not for doing. Give each job in full: a helper knows nothing of this conversation."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "jobs": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_HELPERS,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "A few words naming the job."},
                        "job": {
                            "type": "string",
                            "description": "Everything the helper needs to do it.",
                        },
                    },
                    "required": ["name", "job"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["jobs"],
        "additionalProperties": False,
    }

    def __init__(self, container: Container, caller: Caller, agent_id: str) -> None:
        self._c, self._caller, self._agent = container, caller, agent_id

    async def execute(
        self, *, ctx: object = None, jobs: list[dict[str, Any]] | None = None, **_: object
    ) -> ToolExecutionResult:
        jobs = [j for j in (jobs or []) if str(j.get("job", "")).strip()][:MAX_HELPERS]
        if not jobs:
            return _text("Give at least one job.", error=True)
        day = await self._c.counters.add_day(
            f"helpers:{self._caller.tenant_id}:{self._caller.user_id}",
            limit=HELPERS_PER_DAY,
            count=len(jobs),
        )
        if not day.allowed:
            await self._c.counters.release_day(
                f"helpers:{self._caller.tenant_id}:{self._caller.user_id}", count=len(jobs)
            )
            return _text(
                f"The limit of {HELPERS_PER_DAY} helpers a day is used up. Do the work yourself, or try tomorrow.",
                error=True,
            )
        results = await asyncio.gather(*(self._run(j) for j in jobs), return_exceptions=True)
        parts: list[str] = []
        for job, result in zip(jobs, results, strict=True):
            title = str(job.get("name") or "Helper")[:80]
            if isinstance(result, BaseException):
                parts.append(f"## {title}\nThis helper could not finish: {str(result)[:200]}")
            else:
                parts.append(f"## {title}\n{result or '(it came back with nothing)'}")
        return _text("\n\n".join(parts))

    async def _run(self, job: dict[str, Any]) -> str:
        from loamz.server.agents.features.conversations.chat.model import ChatRequest
        from loamz.server.agents.features.conversations.chat.service import chat
        from loamz.server.agents.features.conversations.threads.service import (
            SCHEDULED_TAG,
            threads,
        )

        c, who = self._c, self._caller
        header = await c.get(threads).create_for(
            who.tenant_id,
            who.user_id,
            name=f"Helper: {str(job.get('name') or 'job')[:60]}",
            tags=(SCHEDULED_TAG,),
        )
        request = ChatRequest(
            thread_id=header.id,
            prompt=str(job["job"]).strip(),
            agent_id=self._agent,
            system_instructions=RULES,
            helper=True,
        )
        service = c.get(chat)
        await service.admit(who, request)

        async def work() -> None:
            async for line in service.chat(who, request):
                if '"type": "run.failed"' in line:
                    with contextlib.suppress(IndexError, ValueError):
                        raise RuntimeError(
                            json.loads(line.split("data: ", 1)[1]).get("error", "the helper failed")
                        )
                    raise RuntimeError("the helper failed")

        try:
            await asyncio.wait_for(work(), timeout=HELPER_SECONDS)
        except TimeoutError:
            with contextlib.suppress(LoamzError):
                await service.cancel(who, header.id)
            raise RuntimeError("it took too long") from None
        for node in reversed(await c.agent_store.threads.session_nodes(header.id)):
            if node.payload.role == Role.ASSISTANT and node.payload.text.strip():
                return node.payload.text.strip()[:REPORT_CHARS]
        return ""
