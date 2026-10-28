"""A conversation's notes, written in the background and kept apart from the messages; the idle pass; and search over everything said."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.ports.threads import MessageNode
from loamz.runtime.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.runtime.testing.scripted import ScriptedModel
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.conversations.memory.model import NoteIn, NotePatch
from loamz.server.agents.features.conversations.memory.service import memory
from loamz.server.agents.features.conversations.memory.sweeper import sweep_once
from loamz.server.agents.features.conversations.threads.service import threads
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice", name="Alice")
BOB = Caller(tenant_id="acme", user_id="bob", name="Bob")
FILLER = "We went through the plan and its details once more, at length. " * 14


class Writer:
    """What the notes writer answers (one JSON object per pass) and what the summary writer answers."""

    def __init__(self) -> None:
        self.passes: list[str] = []
        self.answers: list[dict] = []

    def __call__(self, messages: list[ChatMessage]) -> str:
        text = messages[0].text
        if text.startswith("Notes so far"):
            self.passes.append(text)
            return json.dumps(self.answers.pop(0) if self.answers else {})
        return "Goal: plan the trip. Booking QX7-4412 [#3]. Budget 1800 euros [#3]."


@pytest.fixture
async def world(tmp_path, monkeypatch):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "f")},
            llm={"openai_api_key": "test"},
            context={"max_tokens": 8_000, "output_reserve": 500, "min_tail_turns": 3},
        )
    )
    writer = Writer()
    monkeypatch.setattr(container, "chat_model", lambda *a: ScriptedModel(writer))
    header = await container.get(threads).create_for("acme", "alice", name="Trip")
    yield container, header.id, writer
    await container.aclose()


async def say(container, session: str, turns: int, *, first: str | None = None) -> str:
    history = container.agent_store.threads
    await history.ensure_branch(session, "main")
    branch = await history.get_branch(session, "main")
    leaf = branch.head_message_id
    lines = [(Role.USER, first)] if first else []
    for i in range(turns):
        lines += [(Role.USER, f"Question {i}. {FILLER}"), (Role.ASSISTANT, f"Answer {i}. {FILLER}")]
    for role, text in lines:
        node = MessageNode(
            session_id=session,
            parent_id=leaf,
            payload=ChatMessage(role=role, content=(TextBlock(text=text),)),
        )
        await history.append_and_advance(node, "main")
        leaf = node.id
    return leaf


async def test_the_writer_only_proposes_and_the_server_records_notes_with_its_own_time(world):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(
        container,
        session,
        5,
        first="Always reply in Telugu. My budget is 3000 euros and I fly on 2026-11-03.",
    )
    before = time.time()
    writer.answers = [
        {
            "add": [
                {
                    "kind": "instruction",
                    "text": "Always reply in Telugu.",
                    "source": [1],
                    "effective_at": "1999-01-01T00:00:00Z",
                    "created_at": 1.0,
                },
                {"kind": "fact", "text": "Budget is 3000 euros.", "source": [1], "basis": "stated"},
                {"kind": "task", "text": "Pay the deposit.", "source": [1], "basis": "inferred"},
                {"kind": "nonsense", "text": "ignored: not a known kind", "source": [1]},
            ]
        }
    ]
    assert await service.update_notes(ALICE, session, history) == 3
    notes = (await service.notes(ALICE, session)).items
    assert {n.text for n in notes} == {
        "Always reply in Telugu.",
        "Budget is 3000 euros.",
        "Pay the deposit.",
    }
    assert all(n.written_by == "writer" and n.source == [1] for n in notes)
    # The time is the server's, whatever the model wrote; what was inferred is marked as such.
    assert all(before <= n.effective_at.timestamp() <= time.time() for n in notes)
    assert (
        next(n for n in notes if "deposit" in n.text).basis == "inferred"
        and next(n for n in notes if "Telugu" in n.text).basis == "stated"
    )


async def test_a_correction_adds_a_new_note_and_the_old_one_stays_in_the_history_with_when_and_why(
    world,
):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(container, session, 5, first="My budget is 3000 euros. I must pay the deposit.")
    writer.answers = [
        {
            "add": [
                {"kind": "fact", "text": "Budget is 3000 euros.", "source": [1]},
                {"kind": "task", "text": "Pay the deposit.", "source": [1]},
            ]
        }
    ]
    await service.update_notes(ALICE, session, history)
    notes = (await service.notes(ALICE, session)).items
    budget, deposit = (
        next(n for n in notes if "Budget" in n.text),
        next(n for n in notes if "deposit" in n.text),
    )

    await say(
        container, session, 5, first="Actually the budget is 1800 euros, and I paid the deposit."
    )
    writer.answers = [
        {
            "supersede": [
                {
                    "id": budget.id,
                    "kind": "fact",
                    "text": "Budget is 1800 euros.",
                    "source": [12],
                    "reason": "The person corrected it.",
                }
            ],
            "done": [deposit.id],
        }
    ]
    await service.update_notes(ALICE, session, history)
    assert "Budget is 3000 euros." in writer.passes[1]  # the writer was shown what was in force

    now = {n.text: n for n in (await service.notes(ALICE, session)).items}
    assert (
        "Budget is 3000 euros." not in now
        and now["Budget is 1800 euros."].status == "active"
        and now["Pay the deposit."].status == "done"
    )
    assert now["Budget is 1800 euros."].supersedes_id == budget.id
    everything = {n.text: n for n in (await service.notes(ALICE, session, history=True)).items}
    old = everything["Budget is 3000 euros."]  # never deleted, never rewritten
    assert old.status == "superseded" and old.replaced_by == now["Budget is 1800 euros."].id
    assert (
        old.ended_reason == "The person corrected it."
        and old.ended_at
        and old.ended_at >= old.effective_at
    )
    trail = (await service.history(ALICE, budget.id)).events
    assert [(e.event, e.by) for e in trail] == [("superseded", "writer")] and trail[
        0
    ].related_id == old.replaced_by
    block = await service.block(ALICE, session)
    assert (
        "1800 euros" in block
        and "3000" not in block
        and "deposit" not in block.lower()
        and "(noted " in block
    )


async def test_a_note_is_retired_not_deleted_and_the_writer_cannot_touch_what_the_person_wrote_or_pinned(
    world,
):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(container, session, 5)
    mine = await service.add(
        ALICE, session, NoteIn(kind="instruction", text="Never email my boss directly.")
    )
    writer.answers = [
        {
            "add": [
                {"kind": "decision", "text": "Use the temporary workaround.", "source": [2]},
                {"kind": "fact", "text": "Likes window seats.", "source": [2]},
            ]
        }
    ]
    await service.update_notes(ALICE, session, history)
    workaround = next(
        n for n in (await service.notes(ALICE, session)).items if "workaround" in n.text
    )
    seats = next(n for n in (await service.notes(ALICE, session)).items if "window" in n.text)
    await service.patch(ALICE, seats.id, NotePatch(pinned=True))
    await say(container, session, 5)
    writer.answers = [
        {
            "retire": [
                {"id": workaround.id, "reason": "The migration is finished."},
                {"id": mine.id, "reason": "x"},
                {"id": seats.id, "reason": "x"},
            ]
        }
    ]
    assert await service.update_notes(ALICE, session, history) == 1
    states = {n.text: n for n in (await service.notes(ALICE, session, history=True)).items}
    assert (
        states["Use the temporary workaround."].status == "retired"
        and states["Use the temporary workaround."].ended_reason == "The migration is finished."
    )
    assert (
        states["Never email my boss directly."].status == "active"
        and states["Likes window seats."].status == "active"
    )  # the person's, and pinned
    assert "workaround" not in await service.block(ALICE, session)
    # A person can take a retired note back, and that too is a recorded step.
    await service.patch(ALICE, workaround.id, NotePatch(status="active"))
    assert "workaround" in await service.block(ALICE, session)
    assert [e.event for e in (await service.history(ALICE, workaround.id)).events] == [
        "retired",
        "reopened",
    ]


async def test_each_note_and_step_records_the_zone_the_person_was_in_and_keeps_it_when_they_move(
    world,
):
    from loamz.server.agents.features.people.preferences.model import Preferences
    from loamz.server.agents.features.people.preferences.service import preferences

    container, session, _ = world
    service = container.get(memory)
    # Just before midnight UTC it is already the next day in India; the note shows the day the person was in.
    await container.get(preferences).replace(ALICE, Preferences(timezone="Asia/Kolkata"))
    first = await service.add(ALICE, session, NoteIn(kind="fact", text="Booked the venue."))
    assert (
        first.zone == "Asia/Kolkata" and first.effective_at.utcoffset().total_seconds() == 0
    )  # the moment itself is UTC
    await container.get(preferences).replace(ALICE, Preferences(timezone="Europe/London"))
    await service.patch(ALICE, first.id, NotePatch(status="done"))
    second = await service.add(ALICE, session, NoteIn(kind="fact", text="Paid."))
    trail = (await service.history(ALICE, first.id)).events
    assert [(e.event, e.zone) for e in trail] == [("done", "Europe/London")]
    assert second.zone == "Europe/London"
    assert (await service.notes(ALICE, session)).items[
        0
    ].zone == "Asia/Kolkata"  # the first note keeps the zone it was recorded in
    await container.get(preferences).replace(ALICE, Preferences())  # no zone chosen
    assert (await service.add(ALICE, session, NoteIn(kind="fact", text="Third."))).zone == "UTC"


async def test_a_stale_proposal_is_refused_and_a_replaced_note_is_final(world):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(container, session, 5)
    writer.answers = [{"add": [{"kind": "fact", "text": "Budget is 3000 euros.", "source": [1]}]}]
    await service.update_notes(ALICE, session, history)
    old = (await service.notes(ALICE, session)).items[0]
    # The person replaces it while a background pass still holds the old version.
    newer = await service.patch(ALICE, old.id, NotePatch(text="Budget is 1800 euros."))
    assert newer.id != old.id and newer.supersedes_id == old.id and newer.written_by == "person"
    await say(container, session, 5)
    writer.answers = [
        {
            "supersede": [
                {
                    "id": old.id,
                    "kind": "fact",
                    "text": "Budget is 5000 euros.",
                    "source": [3],
                    "reason": "stale",
                }
            ]
        }
    ]
    assert (
        await service.update_notes(ALICE, session, history) == 0
    )  # it was already replaced: nothing recorded
    assert [n.text for n in (await service.notes(ALICE, session)).items] == [
        "Budget is 1800 euros."
    ]
    with pytest.raises(InvalidRequestError, match="superseded"):
        await service.patch(ALICE, old.id, NotePatch(status="active"))
    with pytest.raises(NotFoundError):
        await service.patch(BOB, newer.id, NotePatch(status="done"))


async def test_a_dismissed_note_is_not_written_again(world):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(container, session, 5)
    writer.answers = [{"add": [{"kind": "fact", "text": "Likes window seats.", "source": [2]}]}]
    await service.update_notes(ALICE, session, history)
    note = (await service.notes(ALICE, session)).items[0]
    await service.patch(ALICE, note.id, NotePatch(status="dismissed"))
    await say(container, session, 5)
    writer.answers = [{}]
    await service.update_notes(ALICE, session, history)
    assert (
        "Likes window seats."
        in writer.passes[-1].split("Dismissed by the person")[1].split("New messages")[0]
    )
    assert (await service.notes(ALICE, session)).items == [] and await service.block(
        ALICE, session
    ) == ""
    assert [n.status for n in (await service.notes(ALICE, session, history=True)).items] == [
        "dismissed"
    ]  # still on record


async def test_a_short_stretch_does_not_cost_a_pass_and_a_malformed_answer_changes_nothing(world):
    container, session, writer = world
    service, history = container.get(memory), container.agent_store.threads
    await say(container, session, 1)
    assert await service.update_notes(ALICE, session, history) == 0 and writer.passes == []
    await say(container, session, 6)
    writer.answers = [{"add": "not a list"}]
    assert await service.update_notes(ALICE, session, history) == 0 and len(writer.passes) == 1


async def test_the_agents_notes_tool_adds_replaces_finishes_and_retires_but_never_deletes(world):
    container, session, _ = world
    service = container.get(memory)
    tool = service.tools(ALICE, session)[0]

    async def run(**kw):
        return (await tool.execute(**kw)).content[0].text

    added = await run(action="add", kind="instruction", text="Never email my boss directly.")
    note_id = added.split("[")[1].split("]")[0]
    assert "Never email my boss directly." in await run(action="list") and "(noted " in await run(
        action="list"
    )
    assert "Replaced" in await run(
        action="replace",
        id=note_id,
        text="Never email my boss or her team directly.",
        reason="Widened",
    )
    listed = await run(action="list")
    assert "or her team" in listed and "directly.\n" not in listed.replace(
        "or her team directly.", ""
    )
    new_id = listed.split("[")[1].split("]")[0]
    assert (await tool.execute(action="done", id=new_id)).content[0].text == "Done."
    assert "Retired" in await run(action="retire", id=new_id, reason="No longer needed")
    assert (await tool.execute(action="dismiss", id=new_id)).is_error and (
        await tool.execute(action="done", id="nope")
    ).is_error
    kinds = {n.status for n in (await service.notes(ALICE, session, history=True)).items}
    assert kinds == {"superseded", "retired"}  # both still there
    mine = await service.add(ALICE, session, NoteIn(kind="fact", text="The person wrote this."))
    refused = await tool.execute(action="retire", id=mine.id, reason="x")
    assert refused.is_error and "only they can retire" in refused.content[0].text
    other = await container.get(threads).create_for("acme", "alice", name="Other")
    assert (
        await service.tools(ALICE, other.id)[0].execute(action="done", id=mine.id)
    ).is_error  # a note of another conversation


async def test_an_idle_long_conversation_is_summarized_once_and_its_notes_written(world):
    container, session, writer = world
    await say(container, session, 20, first="My booking reference is QX7-4412. Budget 1800 euros.")
    writer.answers = [
        {"add": [{"kind": "fact", "text": "Booking reference is QX7-4412.", "source": [1]}]}
    ]
    history = container.agent_store.threads
    import time

    soon = time.time()
    assert await sweep_once(container, now=soon) == 0  # not idle yet: said a moment ago
    later = soon + 31 * 60
    assert await sweep_once(container, now=later) == 1
    assert len(await history.list_checkpoints(session)) == 1
    assert "QX7-4412" in (await history.list_checkpoints(session))[0].summary
    assert [n.text for n in (await container.get(memory).notes(ALICE, session)).items] == [
        "Booking reference is QX7-4412."
    ]
    assert len(await history.session_nodes(session)) == 41  # nothing was deleted
    shown = await container.get(memory).summary(ALICE, session)
    assert shown is not None and 0 < shown.covers < shown.total == 41 and "QX7-4412" in shown.text
    assert await sweep_once(container, now=later + 3600) == 0  # nothing new since: no second pass


async def test_a_short_idle_conversation_is_left_alone_and_two_servers_do_not_both_take_one(world):
    container, session, _ = world
    await say(container, session, 1)
    import time

    assert await sweep_once(container, now=time.time() + 3600) == 0
    assert (await container.agent_store.threads.list_checkpoints(session)) == []
    from loamz.server.agents.features.conversations.memory.store import MemoryStore

    store = MemoryStore(container.database)
    assert await store.claim("acme", "x", 1000.0, 600) is True
    assert await store.claim("acme", "x", 1100.0, 600) is False  # still held by the first
    assert await store.claim("acme", "x", 1700.0, 600) is True  # its claim went stale


async def test_a_person_searches_everything_said_by_words_dates_and_speaker_but_only_their_own(
    world,
):
    container, session, _ = world
    await say(container, session, 3, first="My booking reference is QX7-4412.")
    app = create_app(container, roles={"gateway"})
    app.dependency_overrides[caller_dependency] = lambda: ALICE
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            found = (
                await client.get(f"/api/v1/threads/{session}/search", params={"q": "qx7-4412"})
            ).json()
            assert (
                found["total"] == 1
                and found["items"][0]["number"] == 1
                and found["items"][0]["who"] == "person"
            )
            assert (
                await client.get(
                    f"/api/v1/threads/{session}/search", params={"q": "qx7", "who": "agent"}
                )
            ).json()["total"] == 0
            assert (
                await client.get(
                    f"/api/v1/threads/{session}/search", params={"q": "qx7", "from": "2999-01-01"}
                )
            ).json()["total"] == 0
            nothing = await client.get(f"/api/v1/threads/{session}/search")
            assert nothing.status_code == 400  # no words and no dates: nothing to search for
            assert (
                await client.get(f"/api/v1/threads/{session}/summary")
            ).json() is None  # nothing summarized yet
            made = await client.post(
                f"/api/v1/threads/{session}/notes", json={"kind": "task", "text": "Call the bank"}
            )
            assert made.status_code == 201 and made.json()["written_by"] == "person"
            note = made.json()["id"]
            assert (
                await client.patch(f"/api/v1/notes/{note}", json={"pinned": True, "status": "done"})
            ).json()["status"] == "done"
            assert [
                n["text"]
                for n in (await client.get(f"/api/v1/threads/{session}/notes")).json()["items"]
            ] == ["Call the bank"]
            app.dependency_overrides[caller_dependency] = lambda: BOB
            assert (
                await client.get(f"/api/v1/threads/{session}/search", params={"q": "qx7"})
            ).status_code == 404
            assert (await client.get(f"/api/v1/threads/{session}/notes")).status_code == 404
            assert (
                await client.patch(f"/api/v1/notes/{note}", json={"status": "dismissed"})
            ).status_code == 404


async def test_after_a_busy_stretch_the_notes_are_brought_up_to_date_in_the_background_and_one_at_a_time(
    world,
):
    container, session, writer = world
    service = container.get(memory)
    await say(container, session, 3)  # 6 messages: not enough yet
    service.keep_up(ALICE, session, None)
    await asyncio.gather(*service._tasks)
    assert writer.passes == []
    await say(container, session, 6)  # 18 in all
    writer.answers = [
        {
            "add": [
                {"kind": "fact", "text": "Budget is 3000 euros.", "source": [1], "basis": "stated"}
            ]
        }
    ]
    service.keep_up(ALICE, session, None)
    service.keep_up(
        ALICE, session, None
    )  # a second ask while the first is writing is not another pass
    await asyncio.gather(*service._tasks)
    assert len(writer.passes) == 1
    assert [n.text for n in (await service.notes(ALICE, session)).items] == [
        "Budget is 3000 euros."
    ]
