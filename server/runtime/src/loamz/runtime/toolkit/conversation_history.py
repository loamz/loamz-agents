"""ConversationHistoryTool — search and read every message of the conversation the agent is in.

Summaries keep a long conversation inside the model's window, but a summary can leave out a number, a quote or a date. Every original
message is kept; this is how the agent gets one back. It is bound to one conversation when it is built (its store is already the
person's own): the model cannot name another conversation, only look through this one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from loamz.runtime.kernel.ports.threads import MessageNode, ThreadStore
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

MAX_HITS = 10
MAX_READ = 20
MAX_READ_CHARS = 12_000
SNIPPET = 240
TOOL_RESULT_CHARS = 4_000


def flatten(message: ChatMessage) -> str:
    """The plain words of a message: what was said, the tools it called (name and arguments) and what they gave back (clipped)."""
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            parts.append(f"[called {block.tool_name} {block.arguments}]")
        elif isinstance(block, ToolResultBlock):
            parts.append(f"[{block.name or 'tool'} returned: {block.text[:TOOL_RESULT_CHARS]}]")
    return "\n".join(p for p in parts if p).strip()


def _who(node: MessageNode) -> str:
    role = str(
        node.payload.role.value if hasattr(node.payload.role, "value") else node.payload.role
    )
    return {"user": "person", "assistant": "agent", "tool": "tool"}.get(role, role)


def _day(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class ConversationHistoryTool:
    name = "conversation_history"
    risk = ToolRisk.SAFE
    idempotent = True
    description = (
        "Look through everything said in this conversation, including messages that are no longer in front of you because "
        "older ones were summarized. Use it whenever a detail matters (a number, a name, a quote, a date, what the person "
        "asked for). 'search' finds messages by words, optionally in a date range or by who said them; 'read' returns exact "
        "messages by number."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "read"]},
            "query": {
                "type": "string",
                "description": "Words to find (all of them must appear). For search.",
            },
            "from_date": {
                "type": "string",
                "description": "Only messages on or after this day, YYYY-MM-DD. For search.",
            },
            "to_date": {
                "type": "string",
                "description": "Only messages on or before this day, YYYY-MM-DD. For search.",
            },
            "who": {
                "type": "string",
                "enum": ["person", "agent", "tool"],
                "description": "Only messages from them. For search.",
            },
            "from_number": {
                "type": "integer",
                "description": "First message number to read. For read.",
            },
            "to_number": {
                "type": "integer",
                "description": "Last message number to read (at most 20 after the first). For read.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, history: ThreadStore, session_id: str) -> None:
        self._history = history
        self._session = session_id

    async def execute(  # type: ignore[override]
        self,
        *,
        ctx: object = None,
        action: str = "",
        query: str = "",
        from_date: str = "",
        to_date: str = "",
        who: str = "",
        from_number: int = 0,
        to_number: int = 0,
        **_: object,
    ) -> ToolExecutionResult:
        nodes = [
            n
            for n in await self._history.session_nodes(self._session)
            if not n.payload.metadata.get("is_checkpoint_summary")
        ]
        if action == "search":
            return _ok(self._search(nodes, query, from_date, to_date, who))
        if action == "read":
            return _ok(self._read(nodes, from_number, to_number))
        return ToolExecutionResult(
            content=[TextBlock(text="Use action 'search' or 'read'.")], is_error=True
        )

    def _search(
        self, nodes: list[MessageNode], query: str, from_date: str, to_date: str, who: str
    ) -> str:
        words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
        if not words and not (from_date or to_date):
            return "Say what to search for, or give a date range."
        since = _day(from_date) if from_date else None
        until = _day(to_date) if to_date else None
        hits: list[str] = []
        for number, node in enumerate(nodes, start=1):
            if who and _who(node) != who:
                continue
            when = (
                node.created_at if node.created_at.tzinfo else node.created_at.replace(tzinfo=UTC)
            )
            if since and when < since:
                continue
            if until and when.date() > until.date():
                continue
            text = flatten(node.payload)
            low = text.lower()
            if not text or any(w not in low for w in words):
                continue
            at = low.find(words[0]) if words else 0
            start = max(0, at - SNIPPET // 3)
            snippet = text[start : start + SNIPPET].replace("\n", " ")
            hits.append(
                f"#{number} · {when:%Y-%m-%d %H:%M} · {_who(node)}: {'…' if start else ''}{snippet}{'…' if start + SNIPPET < len(text) else ''}"
            )
        if not hits:
            return "Nothing in this conversation matches."
        shown = hits[-MAX_HITS:]  # the most recent matches are the likeliest to be what is wanted
        more = (
            f"\n({len(hits) - len(shown)} older matches not shown: narrow it with a date range or more words.)"
            if len(hits) > len(shown)
            else ""
        )
        return "\n".join(shown) + more

    def _read(self, nodes: list[MessageNode], first: int, last: int) -> str:
        if first < 1 or first > len(nodes):
            return f"There are {len(nodes)} messages. Give from_number between 1 and {len(nodes)}."
        last = min(max(last or first, first), first + MAX_READ - 1, len(nodes))
        out: list[str] = []
        used = 0
        for number in range(first, last + 1):
            node = nodes[number - 1]
            text = flatten(node.payload)
            line = f"#{number} · {node.created_at:%Y-%m-%d %H:%M} · {_who(node)}: {text}"
            if used + len(line) > MAX_READ_CHARS:
                out.append(
                    f"(stopped at #{number - 1}: too long to show at once. Read the next ones with from_number={number}.)"
                )
                break
            out.append(line)
            used += len(line)
        return "\n\n".join(out)


def _ok(text: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=text)])
