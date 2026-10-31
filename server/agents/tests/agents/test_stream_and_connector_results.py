"""Two defects that turned a perfectly good outcome into a broken one."""

from __future__ import annotations

import json
from types import SimpleNamespace

from loamz.runtime.kernel.tools import ToolExecutionResult
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.run_log import RunLogKind
from loamz.server.agents.features.conversations.chat.service import ChatService
from loamz.server.agents.features.people.connections.service import MAX_RESULT_CHARS, shortened


def test_a_long_connector_reply_is_shortened_not_turned_into_an_error() -> None:
    """Content blocks are immutable: cutting one used to raise, and the model was told the connector had failed."""
    long = ToolExecutionResult(
        call_id="c",
        content=[TextBlock(text="x" * (MAX_RESULT_CHARS + 50)), TextBlock(text="short")],
    )
    out = shortened(long)
    assert not out.is_error
    assert (
        out.content[0].text.endswith("[shortened]")
        and len(out.content[0].text) < MAX_RESULT_CHARS + 20
    )
    assert out.content[1].text == "short"
    assert (
        long.content[0].text.startswith("x" * 10)
        and len(long.content[0].text) == MAX_RESULT_CHARS + 50
    )


async def test_a_failed_runs_error_reaches_the_client_as_valid_json_whatever_it_says() -> None:
    message = 'tool "calc" said: {"x": 1}\nand then a line break'

    class _Runtime:
        async def tail(self, run_id: str, from_seq: int = 0):  # type: ignore[no-untyped-def]
            yield SimpleNamespace(kind=RunLogKind.RUN_FAILED, payload={"error": message}, seq=0)

    service = object.__new__(ChatService)
    service._c = SimpleNamespace(runtime=_Runtime())  # type: ignore[attr-defined]
    events = [line async for line in service._relay("r1")]
    failed = next(line for line in events if '"run.failed"' in line)
    body = json.loads(failed.removeprefix("data: ").strip())
    assert body == {"type": "run.failed", "error": message}
