"""Data tools: an agent finds, understands and queries governed data as the person it is working for.

    tools = data_tools(connection, minter)       # data_search, data_describe, data_sql

Every call asks the data service with a short-lived token for the run's tenant, person and teams, minted here on the
server, so the service decides what is visible: a sales analyst's agent sees sales tables, a marketing analyst's sees
marketing's, and the model can neither widen nor see that identity. The work happens in the data engine; only a bounded
answer comes back, and a result that would be large is cut off with a note that tells the model to aggregate.
"""

from __future__ import annotations

import json
from typing import Any

from loamz.core.exceptions import HttpResponseError
from loamz.runtime.data._connection import DataServiceConnection
from loamz.runtime.data.tokens import DataTokenMinter
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.run import scope_of

MAX_ROWS = 200
MAX_CELL = 120
MAX_CHARS = 24_000
DATA_SCOPES = frozenset({"data:read"})


def cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def table(names: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


class DataTool:
    risk = ToolRisk.SAFE  # reads only, and only what the person's teams may read
    idempotent = True

    def __init__(self, connection: DataServiceConnection, minter: DataTokenMinter) -> None:
        self.connection, self.minter = connection, minter

    async def execute(self, *, ctx: object = None, **kwargs: Any) -> ToolExecutionResult:
        call_id = str(kwargs.pop("call_id", ""))
        scope = scope_of(ctx)
        if not scope.tenant_id or not scope.user_id:
            return self._reply(call_id, "Data needs a signed-in person; this run has none.", True)
        token = self.minter.mint(
            scope.tenant_id,
            scope.user_id,
            agent_id=scope.agent_id or None,
            thread_id=scope.thread_id or None,
            scopes=DATA_SCOPES,
            groups=scope.groups,
        )
        try:
            with self.connection.bearer(token):
                text = await self.run(**kwargs)
        except HttpResponseError as error:
            # The service's own explanation is what lets the model correct itself (a missing column, a table it may not read).
            return self._reply(call_id, error.detail or str(error), True)
        except (TypeError, ValueError) as error:
            return self._reply(call_id, f"Bad arguments: {error}", True)
        return self._reply(call_id, text, False)

    @staticmethod
    def _reply(call_id: str, text: str, is_error: bool) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=call_id, content=[TextBlock(text=text[:MAX_CHARS])], is_error=is_error
        )

    async def run(self, **kwargs: Any) -> str:
        raise NotImplementedError


class DataSearch(DataTool):
    name = "data_search"
    description = (
        "Find tables and views you may read by a word in their name, comment or columns. Leave `text` empty to list "
        "what is available. Start here when you do not know what data exists."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "A word to look for."}},
        "additionalProperties": False,
    }

    async def run(self, text: str = "", **_: Any) -> str:
        found = await self.connection.client.search(text, limit=25)
        if not found:
            return "Nothing you may read matches. Try another word, or an empty search to list everything."
        return json.dumps(found, default=str)


class DataDescribe(DataTool):
    name = "data_describe"
    description = "Columns, types, row count and a few sample rows of `catalog.schema.table_or_view`. Look before you query."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Full name, like main.nyc.yellow_trips."}
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    async def run(self, name: str, **_: Any) -> str:
        info = await self.connection.client.describe(name, sample=0)
        sample = await self.connection.client.sql.rows(f"SELECT * FROM {name} LIMIT 5", max_rows=5)
        return (
            json.dumps(info, default=str) + "\n\nSample rows:\n" + table(sample.names, sample.rows)
        )


class DataQuery(DataTool):
    name = "data_sql"
    description = (
        "Run one read-only SQL SELECT (DuckDB dialect) on the data engine and get the answer back as a table. The engine "
        "does the work on all the rows, so filter and aggregate in SQL (WHERE, GROUP BY, ORDER BY, LIMIT) and bring back "
        f"only the answer: results stop at {MAX_ROWS} rows. Use full table names (catalog.schema.table)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
        "required": ["query"],
        "additionalProperties": False,
    }

    async def run(self, query: str, **_: Any) -> str:
        result = await self.connection.client.sql.rows(query, max_rows=MAX_ROWS)
        if not result.rows:
            return "0 rows. This does not show that something is zero or absent: check the filter and the table's range."
        out = table(result.names, result.rows)
        if result.truncated:
            out += f"\n\n(Stopped at {MAX_ROWS} rows. The engine has more: aggregate, filter or LIMIT to see the rest.)"
        return out


def data_tools(connection: DataServiceConnection, minter: DataTokenMinter) -> list[DataTool]:
    return [
        DataSearch(connection, minter),
        DataDescribe(connection, minter),
        DataQuery(connection, minter),
    ]
