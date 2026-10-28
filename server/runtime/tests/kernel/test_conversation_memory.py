"""A long conversation is summarized once it nears the budget, the recent turns stay word for word, and every original stays readable."""

from __future__ import annotations

import pytest

from loamz.runtime.kernel.context.compaction.checkpointing import SummaryCheckpointer
from loamz.runtime.kernel.context.history import project_messages
from loamz.runtime.kernel.context.protocols import CompactionContext
from loamz.runtime.kernel.ports.threads import MessageNode
from loamz.runtime.kernel.types.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from loamz.runtime.testing.scripted import ScriptedModel
from loamz.runtime.toolkit.conversation_history import ConversationHistoryTool
from tests._stores import fs_history

SESSION = "s1"
FILLER = "We went over the plan and the details again, at some length. " * 12


async def talk(history, turns: int, *, start: int = 0) -> str:
    """`turns` question-and-answer pairs on the main branch; the id of the last message."""
    leaf = (
        (await history.get_branch(SESSION, "main")).head_message_id
        if await history.get_branch(SESSION, "main")
        else None
    )
    await history.ensure_branch(SESSION, "main")
    for i in range(start, start + turns):
        for role, text in (
            (Role.USER, f"Question {i}. {FILLER}"),
            (Role.ASSISTANT, f"Answer {i}. {FILLER}"),
        ):
            node = MessageNode(
                session_id=SESSION,
                parent_id=leaf,
                payload=ChatMessage(role=role, content=(TextBlock(text=text),)),
            )
            await history.append_and_advance(node, "main")
            leaf = node.id
    return leaf


def checkpointer(history, model, **kw) -> SummaryCheckpointer:
    return SummaryCheckpointer(history, model, budget_tokens=4_000, min_tail_turns=3, **kw)


async def test_a_short_conversation_is_left_alone():
    history = fs_history()
    leaf = await talk(history, 2)
    assert (
        await checkpointer(history, ScriptedModel("never used")).checkpoint(SESSION, leaf) is None
    )


async def test_a_long_one_is_summarized_with_the_recent_turns_kept_word_for_word():
    history = fs_history()
    leaf = await talk(history, 20)
    model = ScriptedModel(
        "Goal: plan the trip. Booking reference QX7-4412 [#3]. Open: pay the deposit."
    )
    cp = await checkpointer(history, model).checkpoint(SESSION, leaf)
    assert cp is not None and "QX7-4412" in cp.summary
    await history.save_checkpoint(cp)
    shown = await project_messages(history, SESSION)
    # The reader shows the summary, then the tail untouched: the last question and its answer are still there verbatim.
    assert shown[0].metadata.get("is_checkpoint_summary") and "QX7-4412" in shown[0].text
    assert shown[-1].text.startswith("Answer 19") and shown[-2].text.startswith("Question 19")
    assert 6 <= len(shown) < 40  # at least the last three turns, far fewer than all forty messages
    # Nothing was deleted.
    assert len(await history.session_nodes(SESSION)) == 40
    # The next turn does not summarize again until it has grown to the budget once more.
    assert await checkpointer(history, ScriptedModel("no")).checkpoint(SESSION, leaf) is None


async def test_a_tail_never_starts_between_a_tool_call_and_its_result():
    history = fs_history()
    await history.ensure_branch(SESSION, "main")
    leaf = None
    for i in range(14):
        for message in (
            ChatMessage(role=Role.USER, content=(TextBlock(text=f"Look up item {i}. {FILLER}"),)),
            ChatMessage(
                role=Role.ASSISTANT,
                content=(ToolUseBlock(call_id=f"c{i}", tool_name="lookup", arguments={"n": i}),),
            ),
            ChatMessage(
                role=Role.TOOL,
                content=(
                    ToolResultBlock(
                        call_id=f"c{i}", name="lookup", content=(TextBlock(text=FILLER),)
                    ),
                ),
            ),
            ChatMessage(role=Role.ASSISTANT, content=(TextBlock(text=f"Item {i} done."),)),
        ):
            node = MessageNode(session_id=SESSION, parent_id=leaf, payload=message)
            await history.append_and_advance(node, "main")
            leaf = node.id
    cp = await checkpointer(history, ScriptedModel("summary")).checkpoint(SESSION, leaf)
    assert cp is not None
    await history.save_checkpoint(cp)
    shown = await project_messages(history, SESSION)
    assert (
        shown[1].role == Role.USER
    )  # right after the summary comes a person's message, never an orphaned tool result


async def test_a_later_summary_builds_on_the_earlier_one_and_every_third_is_made_again_from_the_originals():
    history = fs_history()
    asked: list[str] = []
    model = ScriptedModel(
        lambda messages: asked.append(messages[0].text) or "summary " + str(len(asked))
    )
    leaf = await talk(history, 20)
    made = []
    for round_ in range(3):
        cp = await checkpointer(history, model, rebuild_every=3).checkpoint(SESSION, leaf)
        assert cp is not None, round_
        await history.save_checkpoint(cp)
        made.append(cp)
        leaf = await talk(history, 20, start=20 * (round_ + 1))
    assert [cp.state["rebuilt_from_originals"] for cp in made] == [False, False, True]
    assert made[1].parent_checkpoint_id == made[0].id and "Previous summary:\nsummary 1" in asked[1]
    # The third was made again from the first messages: no earlier summary went into it (only its own, between chunks).
    rebuilt_prompts = asked[2:]
    assert "summary 1" not in "".join(rebuilt_prompts) and "summary 2" not in "".join(
        rebuilt_prompts
    )
    assert "#1 person: Question 0" in rebuilt_prompts[0]
    # The one after that builds on it again.
    leaf = await talk(history, 20, start=80)
    fourth = await checkpointer(history, model, rebuild_every=3).checkpoint(SESSION, leaf)
    assert (
        fourth is not None
        and fourth.state["rebuilt_from_originals"] is False
        and fourth.parent_checkpoint_id == made[2].id
    )


async def test_the_agent_can_search_and_read_what_the_summary_left_out():
    history = fs_history()
    await history.ensure_branch(SESSION, "main")
    leaf = None
    said = [
        "Hi, I am planning a trip.",
        "Great, where to?",
        "My booking reference is QX7-4412 and I fly on 2026-11-03.",
        "Noted.",
        "Also, I want a window seat.",
    ]
    for i, text in enumerate(said):
        node = MessageNode(
            session_id=SESSION,
            parent_id=leaf,
            payload=ChatMessage(
                role=Role.USER if i % 2 == 0 else Role.ASSISTANT, content=(TextBlock(text=text),)
            ),
        )
        await history.append_and_advance(node, "main")
        leaf = node.id
    tool = ConversationHistoryTool(history, SESSION)

    async def run(**kw):
        return (await tool.execute(**kw)).content[0].text

    found = await run(action="search", query="booking reference")
    assert "#3" in found and "QX7-4412" in found and "person" in found
    assert "Nothing" in await run(action="search", query="passport")
    assert "QX7-4412" in await run(action="search", query="qx7-4412", who="person")
    assert "Nothing" in await run(action="search", query="qx7-4412", who="agent")
    assert "Nothing" in await run(action="search", query="qx7", from_date="2999-01-01")
    assert "QX7-4412" in await run(action="search", query="qx7", to_date="2999-01-01")
    read = await run(action="read", from_number=2, to_number=4)
    assert read.index("#2") < read.index("#3") < read.index("#4") and "window seat" not in read
    assert "There are 5 messages" in await run(action="read", from_number=9)


async def test_the_tool_reads_only_the_conversation_it_was_built_for():
    history = fs_history()
    await history.ensure_branch("other", "main")
    await history.append_and_advance(
        MessageNode(
            session_id="other",
            payload=ChatMessage(role=Role.USER, content=(TextBlock(text="secret plan"),)),
        ),
        "main",
    )
    await history.ensure_branch(SESSION, "main")
    await history.append_and_advance(
        MessageNode(
            session_id=SESSION,
            payload=ChatMessage(role=Role.USER, content=(TextBlock(text="hello"),)),
        ),
        "main",
    )
    tool = ConversationHistoryTool(history, SESSION)
    assert "Nothing" in (await tool.execute(action="search", query="secret")).content[0].text


@pytest.mark.parametrize("context_has_no_leaf", [True])
async def test_there_is_nothing_to_do_without_a_message_yet(context_has_no_leaf):
    history = fs_history()
    assert (
        await checkpointer(history, ScriptedModel("x")).create_checkpoint(
            CompactionContext(session_id=SESSION)
        )
        is None
    )
