"""The agent's own data tools: look at what a person may read in a Loamz Data workspace, and query it.

They run on the host, not in a sandbox. Each call acts for the person the run is for, with the authority the host gave the
run (``ctx.auth``): a credential is asked of the broker for that person at the moment of the call, so these tools hold none.
What a person may read is decided by Loamz Data; a table they may not read is simply not there.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import ClassVar

import httpx

from loamz.core.exceptions import HttpResponseError
from loamz.data import AsyncDataClient
from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.data.source import CATALOGS_ATTRIBUTE, WORKSPACE_ATTRIBUTE
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.run import scope_of
from loamz.runtime.kernel.types.user import UserCtx

MAX_CHARS = 24_000
MAX_ROWS = 200
SCOPES: frozenset[str] = (
    frozenset()
)  # Loamz Data decides from who the person is; the audience says where it is for


# `catalog.schema.table`, each part bare or in double quotes: the catalog is the first.
_CATALOG_IN_SQL = re.compile(r'"?([A-Za-z_]\w*)"?\s*\.\s*"?[A-Za-z_]\w*"?\s*\.\s*"?[A-Za-z_]\w*"?')


def _catalog(full_name: str) -> str:
    return full_name.split(".", 1)[0].strip('"')


def _outside(catalog: str, scope: frozenset[str]) -> str:
    return f"catalog {catalog} is outside the catalogs chosen for this conversation ({', '.join(sorted(scope))})"


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
        code_backend: str = "local",
        schema: str = "main.agents",
    ) -> None:
        self.url, self.broker, self.audience = url, broker, audience
        self.workspace, self.transport = workspace, transport
        self.code_backend = code_backend
        self.schema = schema

    def workspace_of(self, user: UserCtx | None) -> str | None:
        return (user.attribute(WORKSPACE_ATTRIBUTE) if user else None) or self.workspace

    @staticmethod
    def catalogs_of(user: UserCtx | None) -> frozenset[str] | None:
        """The catalogs the conversation was narrowed to, or None for every catalog the person may read.

        This narrows what the agent is shown and may name in its tools; it is not a boundary. What a person may read is decided by
        Loamz Data, and a narrowing can only take away from that, never add to it.
        """
        raw = user.attribute(CATALOGS_ATTRIBUTE) if user else None
        names = frozenset(c.strip() for c in (raw or "").split(",") if c.strip())
        return names or None

    async def client(self, user: UserCtx | None, auth: Auth, workspace: str) -> AsyncDataClient:
        credential = await self.broker.credential(
            user, auth, audience=self.audience, scopes=SCOPES, ttl_s=120
        )
        return AsyncDataClient(
            self.url, credential, workspace=workspace, transport=self.transport, retry=None
        )


def safe_thread_id(thread_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id)
    return cleaned[:64] or "default"


_volume_cache: dict[tuple[str, str, str], str] = {}


async def _get_or_create_volume(
    base_url: str,
    ws: str,
    user_key: str,
    token: str,
    *,
    schema_spec: str = "main.agents",
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    cache_key = (base_url.rstrip("/"), ws, user_key)
    cached_id = _volume_cache.get(cache_key)
    if cached_id:
        return cached_id

    if "." in schema_spec:
        catalog_name, schema_name = schema_spec.split(".", 1)
    else:
        catalog_name, schema_name = None, schema_spec

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        cat_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id=",
            headers=headers,
        )
        cat_resp.raise_for_status()
        catalogs = cat_resp.json().get("items", [])
        catalog_id: str | None = None
        for cat in catalogs:
            if cat.get("kind") == "catalog" and (
                catalog_name is None or cat.get("name") == catalog_name
            ):
                catalog_id = cat.get("id")
                break

        if not catalog_id:
            if catalogs:
                catalog_id = catalogs[0].get("id")
            else:
                raise RuntimeError(f"No catalog found in Loamz Data workspace {ws}")

        sch_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={catalog_id}",
            headers=headers,
        )
        sch_resp.raise_for_status()
        schemas = sch_resp.json().get("items", [])
        schema_id: str | None = None
        for sch in schemas:
            if sch.get("kind") == "schema" and sch.get("name") == schema_name:
                schema_id = sch.get("id")
                break

        if not schema_id:
            create_sch = await client.post(
                f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables",
                json={"kind": "schema", "parent_id": catalog_id, "name": schema_name},
                headers=headers,
            )
            if create_sch.status_code == 201:
                schema_id = create_sch.json().get("id")
            else:
                sch_resp2 = await client.get(
                    f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={catalog_id}",
                    headers=headers,
                )
                for sch in sch_resp2.json().get("items", []):
                    if sch.get("kind") == "schema" and sch.get("name") == schema_name:
                        schema_id = sch.get("id")
                        break

        if not schema_id:
            raise RuntimeError(f"Could not find or create schema {schema_name}")

        vol_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={schema_id}",
            headers=headers,
        )
        vol_resp.raise_for_status()
        volumes = vol_resp.json().get("items", [])
        volume_id: str | None = None
        for vol in volumes:
            if vol.get("kind") == "volume" and vol.get("name") == "agent_files":
                volume_id = vol.get("id")
                break

        if not volume_id:
            create_vol = await client.post(
                f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables",
                json={"kind": "volume", "parent_id": schema_id, "name": "agent_files"},
                headers=headers,
            )
            if create_vol.status_code == 201:
                volume_id = create_vol.json().get("id")
            else:
                vol_resp2 = await client.get(
                    f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={schema_id}",
                    headers=headers,
                )
                for vol in vol_resp2.json().get("items", []):
                    if vol.get("kind") == "volume" and vol.get("name") == "agent_files":
                        volume_id = vol.get("id")
                        break

        if not volume_id:
            raise RuntimeError("Could not find or create volume agent_files")

        _volume_cache[cache_key] = volume_id
        return volume_id


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
                text = await self.run(client, self.connection.catalogs_of(user), **arguments)
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

    async def run(
        self, client: AsyncDataClient, scope: frozenset[str] | None, **arguments: str | int
    ) -> str:
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

    async def run(
        self, client: AsyncDataClient, scope: frozenset[str] | None, **arguments: str | int
    ) -> str:
        names = [n for n in await client.tables() if scope is None or _catalog(n) in scope]
        if names:
            return json.dumps(names)
        return (
            "Nothing in the chosen catalogs is readable by you."
            if scope
            else "Nothing here is readable by you yet."
        )


class DataDescribe(_DataTool):
    name = "data_describe"
    description = "The columns of one table you may read, by its full name catalog.schema.table."
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"table": {"type": "string", "description": "catalog.schema.table"}},
        "required": ["table"],
        "additionalProperties": False,
    }

    async def run(
        self, client: AsyncDataClient, scope: frozenset[str] | None, **arguments: str | int
    ) -> str:
        table = str(arguments["table"])
        if scope is not None and _catalog(table) not in scope:
            raise ValueError(_outside(_catalog(table), scope))
        info = await client.describe(table)
        return json.dumps(
            {
                "table": info.full_name,
                "columns": [{"name": c.name, "type": c.type} for c in info.columns],
            }
        )


class DataSql(_DataTool):
    name = "data_sql"
    input_schema: ClassVar[dict[str, object]] = {
        "type": "object",
        "properties": {"sql": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }

    def __init__(self, connection: DataConnection) -> None:
        super().__init__(connection)
        if connection.code_backend == "loamz-data":
            self.description = (
                "Run one read-only SELECT over tables you may read, each named in full as catalog.schema.table. "
                "The full result is saved as Parquet to volume agent_files and can be read in code with "
                "pl.scan_parquet(loamz.path('agent_files', path)). Returns columns, a 20-row preview, and the row count."
            )
        else:
            self.description = (
                "Run one read-only SELECT over tables you may read, each named in full as catalog.schema.table. Aggregate and filter "
                f"in SQL (GROUP BY, WHERE, LIMIT): at most {MAX_ROWS} rows come back."
            )

    async def execute(
        self, *, ctx: object = None, call_id: str = "", **arguments: str | int
    ) -> ToolExecutionResult:
        if self.connection.code_backend != "loamz-data":
            return await super().execute(ctx=ctx, call_id=call_id, **arguments)

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

        sql = str(arguments["sql"])
        scope = self.connection.catalogs_of(user)
        if scope is not None:
            readable_catalogs = {
                _catalog(n)
                for n in await (await self.connection.client(user, auth, workspace)).tables()
            }
            for named in _CATALOG_IN_SQL.findall(sql):
                if named in readable_catalogs and named not in scope:
                    return self._reply(call_id, _outside(named, scope), True)

        try:
            token = await self.connection.broker.credential(
                user, auth, audience=self.connection.audience, scopes=SCOPES, ttl_s=120
            )
            vol_id = await _get_or_create_volume(
                self.connection.url,
                workspace,
                user.subject if user else (run.user_id or "user"),
                token,
                schema_spec=self.connection.schema,
                transport=self.connection.transport,
            )
            thread = safe_thread_id(run.thread_id or "default")
            body: dict[str, object] = {
                "sql": sql,
                "save": {
                    "volume": vol_id,
                    "thread": thread,
                },
                "idempotency_key": call_id or uuid.uuid4().hex,
                "wait_s": 60,
            }
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(
                transport=self.connection.transport, timeout=120.0
            ) as client:
                resp = await client.post(
                    f"{self.connection.url.rstrip('/')}/api/v1/workspaces/{workspace}/sql/statements",
                    json=body,
                    headers=headers,
                )
                if resp.status_code not in (200, 202):
                    detail = resp.text
                    try:
                        detail = resp.json().get("detail", detail)
                    except Exception:
                        pass
                    return self._reply(call_id, f"SQL error ({resp.status_code}): {detail}", True)

                run_data: dict[str, object] = resp.json()
                run_id = str(run_data.get("id", ""))
                while run_data.get("state") in ("queued", "running"):
                    await asyncio.sleep(0.5)
                    poll_resp = await client.get(
                        f"{self.connection.url.rstrip('/')}/api/v1/workspaces/{workspace}/runs/{run_id}",
                        headers=headers,
                    )
                    if poll_resp.status_code == 200:
                        run_data = poll_resp.json()

                if run_data.get("state") != "succeeded":
                    err = run_data.get("error") or f"Statement {run_data.get('state')}"
                    return self._reply(call_id, f"SQL error: {err}", True)

                result_data: dict[str, object] = (
                    run_data.get("result") if isinstance(run_data.get("result"), dict) else {}
                )
                raw_cols = result_data.get("columns", [])
                columns = [c.get("name", "") if isinstance(c, dict) else str(c) for c in raw_cols]
                raw_rows = result_data.get("rows", [])
                rows = [list(r) for r in raw_rows if isinstance(r, list)]
                raw_export = result_data.get("export")
                export: dict[str, object] = raw_export if isinstance(raw_export, dict) else {}
                row_count = export.get("rows", len(rows))
                path = str(export.get("path") or f"threads/{thread}/results/{run_id}.parquet")
                sentence = (
                    f"The file is at {path} in the volume agent_files and is read in code with "
                    f'loamz.path("agent_files", "{path}") (Polars, DuckDB and pyarrow read it by range).'
                )
                payload: dict[str, object] = {
                    "columns": columns,
                    "rows": rows,
                    "row_count": row_count,
                    "message": sentence,
                }
                return self._reply(call_id, json.dumps(payload, default=str), False)
        except HttpResponseError as error:
            return self._reply(call_id, error.detail or str(error), True)
        except Exception as error:  # noqa: BLE001
            return self._reply(call_id, f"Query error: {error}", True)

    async def run(
        self, client: AsyncDataClient, scope: frozenset[str] | None, **arguments: str | int
    ) -> str:
        sql = str(arguments["sql"])
        if scope is not None:
            # Only a catalog the person can read is worth refusing by name; anything else is not a table of theirs.
            readable = {_catalog(n) for n in await client.tables()}
            for named in _CATALOG_IN_SQL.findall(sql):
                if named in readable and named not in scope:
                    raise ValueError(_outside(named, scope))
        result = await client.rows(sql, limit=MAX_ROWS)
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
