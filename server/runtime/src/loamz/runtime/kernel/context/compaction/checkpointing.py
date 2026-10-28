"""SummaryCheckpointer — keeps a long conversation inside the model's budget by summarizing its older part, once, and remembering where.

After a turn, when what the next request would carry nears the budget, everything before a recent tail (kept word for word) is folded into
one summary and saved as a checkpoint anchored at the last summarized message. The reader already shows "the summary, then everything after
the anchor", so the prompt stays the same from turn to turn (which is what prompt caching needs) until the next checkpoint. No message is
ever deleted or changed: the originals stay in the store, and `ConversationHistoryTool` reads them back.

A summary of a summary loses detail each time, so every `rebuild_every`-th checkpoint is made again from the original messages instead.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import uuid4

from loamz.runtime.kernel.context.history import AncestryCheckpointResolver, DefaultHistoryResolver
from loamz.runtime.kernel.context.protocols import CompactionContext
from loamz.runtime.kernel.context.tokens import estimate_message_tokens
from loamz.runtime.kernel.models.protocols import ChatModel, GenerationOptions
from loamz.runtime.kernel.ports.threads import HistoryCheckpoint, MessageNode, ThreadStore
from loamz.runtime.kernel.types.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = 1
LINE_CHARS = 1_500
# A rough count under-reads code, numbers and non-English text, so the trigger is set on a padded one.
SAFETY = 1.25

SYSTEM = """You keep the memory of a long working conversation between a person and an AI agent. Older messages are replaced by your \
summary, so the agent must be able to carry on exactly where they left off, without asking again.

Write the summary in the language of the conversation. Be complete about:
- what the person wants (goals) and every instruction or preference they gave, in their own words where it matters ("always reply in Telugu");
- decisions made, and which earlier ideas were dropped;
- exact facts: names, numbers, amounts, dates, times, addresses, links, ids, references, versions. Copy them exactly. When something was corrected, keep only the corrected value and say it was changed;
- files and documents shared or made (names and what they hold);
- what is still to do (open tasks, promises, deadlines) and what is done;
- important results that tools returned.
Leave out greetings, filler and anything that no longer matters. Use short sections with plain headings. After each point put the message numbers it came from in brackets, like [#12, #15], so the agent can read the originals. If a previous summary is given, keep everything in it that still matters and add what the new messages change. Answer with the summary only."""


def _line(number: int, node: MessageNode) -> str:
    message = node.payload
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            parts.append(f"[called {block.tool_name} {block.arguments}]")
        elif isinstance(block, ToolResultBlock):
            parts.append(f"[result: {block.text}]")
    text = " ".join(" ".join(parts).split())
    who = {"user": "person", "assistant": "agent", "tool": "tool"}.get(
        str(getattr(message.role, "value", message.role)), ""
    )
    clipped = text if len(text) <= LINE_CHARS else text[:LINE_CHARS] + " …"
    return f"#{number} {who}: {clipped}"


def _is_turn_start(node: MessageNode) -> bool:
    return node.payload.role == Role.USER and not any(
        isinstance(b, ToolResultBlock) for b in node.payload.content
    )


class SummaryCheckpointer:
    """Satisfies the coordinator's summarizer (`create_checkpoint`). One per agent; it reads the conversation from `history`."""

    def __init__(
        self,
        history: ThreadStore,
        model: ChatModel,
        *,
        budget_tokens: int,
        trigger_ratio: float = 0.8,
        tail_ratio: float = 0.2,
        min_tail_turns: int = 6,
        rebuild_every: int = 3,
        model_name: str = "",
    ) -> None:
        self._history = history
        self._model = model
        self._budget = budget_tokens
        self._trigger = trigger_ratio
        self._tail = tail_ratio
        self._min_turns = min_tail_turns
        self._rebuild_every = max(2, rebuild_every)
        self._model_name = model_name

    async def create_checkpoint(self, context: CompactionContext) -> HistoryCheckpoint | None:
        if not context.leaf_node_id:
            return None
        return await self.checkpoint(context.session_id, context.leaf_node_id)

    async def checkpoint(
        self, session_id: str, leaf_id: str, *, force: bool = False
    ) -> HistoryCheckpoint | None:
        """A new checkpoint when the conversation up to `leaf_id` is near the budget (or `force`), else None."""
        nodes = await DefaultHistoryResolver(self._history).resolve_ancestry(leaf_id)
        current = await AncestryCheckpointResolver(self._history).find_applicable_checkpoint(
            leaf_id
        )
        anchor_at = next(
            (i for i, n in enumerate(nodes) if current and n.id == current.anchor_message_id), -1
        )
        live = nodes[anchor_at + 1 :]
        carried = int(
            estimate_message_tokens(
                ChatMessage(role=Role.USER, content=(TextBlock(text=current.summary),))
            )
            if current
            else 0
        )
        size = int((carried + sum(estimate_message_tokens(n.payload) for n in live)) * SAFETY)
        if not force and size < self._trigger * self._budget:
            return None
        cut = self._cut(live)
        if cut <= 0:
            return None  # nothing before the tail worth summarizing
        covered = live[:cut]
        chain = len(await self._history.list_checkpoints(session_id))
        rebuild = current is not None and (chain + 1) % self._rebuild_every == 0
        offset = anchor_at + 1
        if rebuild:
            summary = await self._from_originals(nodes[: offset + cut])
        else:
            lines = [_line(offset + i + 1, n) for i, n in enumerate(covered)]
            summary = await self._summarize(current.summary if current else None, lines)
        if not summary.strip():
            return None
        after = int(
            estimate_message_tokens(ChatMessage(role=Role.USER, content=(TextBlock(text=summary),)))
        )
        return HistoryCheckpoint(
            id=uuid4().hex,
            session_id=session_id,
            anchor_message_id=covered[-1].id,
            summary=summary.strip(),
            parent_checkpoint_id=current.id if current else None,
            state={
                "covers_to_message": offset + cut,
                "tokens_before": size,
                "tokens_after": after,
                "model": self._model_name,
                "prompt_version": PROMPT_VERSION,
                "rebuilt_from_originals": rebuild,
            },
        )

    def _cut(self, live: Sequence[MessageNode]) -> int:
        """Where the word-for-word tail starts: at the start of a turn, so no tool call is parted from its result, after `tail_ratio` of
        the budget and at least `min_tail_turns` turns have been kept."""
        kept = 0
        turns = 0
        for i in range(len(live) - 1, -1, -1):
            kept += estimate_message_tokens(live[i].payload)
            if _is_turn_start(live[i]):
                turns += 1
                if turns >= self._min_turns and kept >= self._tail * self._budget:
                    return i
        return 0

    async def _summarize(self, previous: str | None, lines: list[str]) -> str:
        prompt = (
            ("Previous summary:\n" + previous + "\n\n" if previous else "")
            + "New messages to fold in:\n"
            + "\n".join(lines)
        )
        return await self._ask(prompt)

    async def _from_originals(self, nodes: Sequence[MessageNode]) -> str:
        """A summary made again from the original messages, in chunks, so what an earlier summary dropped is not lost for good."""
        chunk_tokens = max(4_000, self._budget // 2)
        summary: str | None = None
        batch: list[str] = []
        used = 0
        for number, node in enumerate(nodes, start=1):
            line = _line(number, node)
            cost = len(line) // 4 + 8
            if batch and used + cost > chunk_tokens:
                summary = await self._summarize(summary, batch)
                batch, used = [], 0
            batch.append(line)
            used += cost
        if batch:
            summary = await self._summarize(summary, batch)
        return summary or ""

    async def _ask(self, prompt: str) -> str:
        response = await self._model.generate(
            [ChatMessage(role=Role.USER, content=(TextBlock(text=prompt),))],
            options=GenerationOptions(system_instructions=SYSTEM, temperature=0.2),
        )
        return response.text
