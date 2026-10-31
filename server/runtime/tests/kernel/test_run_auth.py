"""A run acts with the authority the host gave it: it reaches tools and spawned children through ctx, and is never stored."""

from __future__ import annotations

import asyncio
import json

from loamz.runtime.kernel.runtime import DataPayload, Message
from loamz.runtime.kernel.types.auth import AsUser, Auth
from loamz.runtime.kernel.types.identity import Actor
from loamz.runtime.kernel.types.run_log import RunLogKind
from loamz.runtime.testing.runtime import ephemeral_runtime

SECRET = "ana-secret-access-token"
seen: dict[str, Auth | None] = {}


class Child:
    id = Actor("agent", "child")

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        seen["child"] = getattr(ctx, "auth", "missing")  # type: ignore[assignment]


class Parent:
    id = Actor("agent", "parent")

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        seen["parent"] = getattr(ctx, "auth", "missing")  # type: ignore[assignment]
        handle = await ctx.spawn(  # type: ignore[attr-defined]
            Child.id, boot=Message(target=Child.id, sender=self.id, payload=DataPayload(data={}))
        )
        await ctx.join(handle)  # type: ignore[attr-defined]


def _msg(to: Actor) -> Message:
    return Message(target=to, sender=Actor.system("t"), payload=DataPayload(data={}))


async def test_the_authority_given_at_submit_reaches_the_run_and_its_children_and_is_not_stored() -> (
    None
):
    seen.clear()
    async with ephemeral_runtime() as rt:
        await rt.register(Parent())
        await rt.register(Child())
        run_id = await rt.submit(Parent.id, _msg(Parent.id), auth=AsUser(SECRET), max_retries=0)
        async for entry in rt.tail(run_id):
            if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                break
        await asyncio.sleep(0.05)
        everything = json.dumps(
            [e.model_dump(mode="json") for e in await rt.store.read_events(run_id)], default=str
        )

    assert seen["parent"] == AsUser(SECRET) and seen["child"] == AsUser(SECRET)
    assert SECRET not in everything, "a credential is never written to the run's journal"


async def test_a_run_submitted_without_authority_has_none() -> None:
    seen.clear()
    async with ephemeral_runtime() as rt:
        await rt.register(Child())
        run_id = await rt.submit(Child.id, _msg(Child.id), max_retries=0)
        async for entry in rt.tail(run_id):
            if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED):
                break
    assert seen["child"] is None
