"""The agent's own data tools: look at what a person may read in a Loamz Data workspace, and query it.

They run on the host, not in a sandbox. Each call acts for the person the run is for, with the authority the host gave the
run (``ctx.auth``): a credential is asked of the broker for that person at the moment of the call, so these tools hold none.
What a person may read is decided by Loamz Data; a table they may not read is simply not there.
"""

from __future__ import annotations

import json
from typing import ClassVar

import httpx

from loamz.core.exceptions import HttpResponseError
from loamz.data import AsyncDataClient
from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.data.source import WORKSPACE_ATTRIBUTE
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.run import scope_of
from loamz.runtime.kernel.types.user import UserCtx

MAX_CHARS = 24_000
MAX_ROWS = 200
SCOPES = frozenset({"data:read"})


class DataConnection:
    """Where Loamz Data is, and how a credential for one person's call is obtained."""

    def __init__(
        self,
        url: str,
        broker: CredentialBroker,
        *,
        audience: str = "loamz-data",
        workspace: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url, self.broker, self.audience = url, broker, audience
        self.workspace, self.transport = workspace, transport

    def workspace_of(self, user: UserCtx | None) -> str | None:
        return (user.attribute(WORKSPACE_ATTRIBUTE) if user else None) or self.workspace

    async def client(self, user: UserCtx | None, auth: Auth, workspace: str) -> AsyncDataClient:
        credential = await self.broker.credential(
            user, auth, audience=self.audience, scopes=SCOPES, ttl_s=120
        )
        return AsyncDataClient(
            self.url, credential, workspace=workspace, transport=self.transport, retry=None
        )


class _DataTool:
    risk = ToolRisk.SAFE  # reads only, and only what the person may read
    idempotent = True
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, object]]

    def __init__(self, connection: DataConnection) -> None:
        self.connection = connection

    async def execute(
        self, *, ctx: object = None, call_id: str = "", **arguments: str | int
    ) -> ToolExecutionResult:
        run = scope_of(ctx)
        user = run.user
        auth: Auth | None = getattr(ctx, "auth", None)
        workspace = self.connection.workspace_of(user)
        if (
            auth is None
            or workspace is None
            or (user is None and self.connection.workspace is None)
        ):
            return self._reply(
                call_id, "Data needs a signed-in person and a workspace; this run has none.", True
            )
        client = await self.connection.client(user, auth, workspace)
        try:
            async with client:
                text = await self.run(client, **arguments)
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

    async def run(self, client: AsyncDataClient, **arguments: str | int) -> str:
        raise NotImplementedError


class DataTables(_DataTool):
    name = "data_tables"
    description = (
        "List the tables you may read, by their full name (catalog.schema.table). Start here when you do not know what "
        "data exists."
    )
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, client: AsyncDataClient, **arguments: str | int) -> str:
        names = await client.tables()
        return json.dumps(names) if names else "Nothing here is readable by you yet."


class DataDescribe(_DataTool):
    name = "data_describe"
    description = "The columns of one table you may read, by its full name catalog.schema.table."
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"table": {"type": "string", "description": "catalog.schema.table"}},
        "required": ["table"],
        "additionalProperties": False,
    }

    async def run(self, client: AsyncDataClient, **arguments: str | int) -> str:
        info = await client.describe(str(arguments["table"]))
        return json.dumps(
            {
                "table": info.full_name,
                "columns": [{"name": c.name, "type": c.type} for c in info.columns],
            }
        )


class DataSql(_DataTool):
    name = "data_sql"
    description = (
        "Run one read-only SELECT over tables you may read, each named in full as catalog.schema.table. Aggregate and filter "
        f"in SQL (GROUP BY, WHERE, LIMIT): at most {MAX_ROWS} rows come back."
    )
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }

    async def run(self, client: AsyncDataClient, **arguments: str | int) -> str:
        result = await client.rows(str(arguments["sql"]), limit=MAX_ROWS)
        return json.dumps(
            {
                "columns": [c.name for c in result.columns],
                "rows": [list(r) for r in result.rows],
                "truncated": result.truncated,
            },
            default=str,
        )


def data_tools(connection: DataConnection) -> list[_DataTool]:
    """The tools that let an agent look at and query a person's data."""
    return [DataTables(connection), DataDescribe(connection), DataSql(connection)]


__all__ = ["DataConnection", "DataDescribe", "DataSql", "DataTables", "data_tools"]
