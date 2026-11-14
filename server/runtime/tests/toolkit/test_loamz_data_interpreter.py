"""Unit tests for LoamzDataRuntime and DataSql with loamz-data backend against fake Loamz Data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

import httpx
import pytest

from loamz.runtime.auth import AsUser, StaticBroker
from loamz.runtime.data import DataConnection
from loamz.runtime.data.tools import DataSql
from loamz.runtime.data.tools import _volume_cache as data_volume_cache
from loamz.runtime.kernel.types.content import MediaBlock, TextBlock
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx
from loamz.runtime.toolkit.code.runtimes.base import SandboxSpec
from loamz.runtime.toolkit.code.runtimes.loamz_data import (
    LoamzDataRuntime,
    delete_agent_volume_thread,
    safe_thread_id,
)
from loamz.runtime.toolkit.code.runtimes.loamz_data import (
    _volume_cache as runtime_volume_cache,
)
from loamz.runtime.toolkit.code.tool import CodeInterpreterTool

WS: Final[str] = "9cce7e59-e1c0-4f8b-ab20-ff93a79161ca"
BASE_URL: Final[str] = "http://data.test"
VOL_ID: Final[str] = "vol-agent-files-123"
CATALOG_ID: Final[str] = "cat-main-456"
SCHEMA_ID: Final[str] = "sch-agents-789"


class FakeCtx:
    def __init__(self, scope: RunScope, auth: object) -> None:
        self.scope = scope
        self.auth = auth


@pytest.fixture(autouse=True)
def _clear_volume_caches() -> None:
    runtime_volume_cache.clear()
    data_volume_cache.clear()


def make_mock_handler(
    *,
    initial_post_runs_status: int = 202,
    run_state: str = "succeeded",
    run_stdout: str = "hello from loamz data compute",
    run_stderr: str = "",
    run_error: str | None = None,
    committed_files: list[dict[str, object]] | None = None,
    failed_commits: list[str] | None = None,
    export_path: str = "threads/conv_1/results/stmt_1.parquet",
    export_rows: int = 15,
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    requests: list[httpx.Request] = []
    post_run_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_run_calls
        requests.append(request)
        path = request.url.path
        method = request.method

        # 1. Securables discovery
        if path == f"/api/v1/workspaces/{WS}/securables":
            parent_id = request.url.params.get("parent_id", "")
            if parent_id == "":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": CATALOG_ID, "name": "main", "kind": "catalog"},
                        ]
                    },
                )
            if parent_id == CATALOG_ID:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": SCHEMA_ID, "name": "agents", "kind": "schema"},
                        ]
                    },
                )
            if parent_id == SCHEMA_ID:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": VOL_ID, "name": "agent_files", "kind": "volume"},
                        ]
                    },
                )
            if method == "POST":
                body = json.loads(request.content)
                item_name = str(body.get("name", "created"))
                item_kind = str(body.get("kind", "volume"))
                return httpx.Response(
                    201,
                    json={
                        "id": f"id-{item_kind}-{item_name}",
                        "name": item_name,
                        "kind": item_kind,
                    },
                )

        # 2. Runs execution (POST)
        if path == f"/api/v1/workspaces/{WS}/runs" and method == "POST":
            post_run_calls += 1
            if post_run_calls == 1 and initial_post_runs_status != 202:
                return httpx.Response(
                    initial_post_runs_status,
                    json={"title": "Conflict", "detail": "Thread busy"},
                )
            return httpx.Response(202, json={"id": "run-xyz-999", "state": "queued"})

        # 3. Runs status polling (GET)
        if path == f"/api/v1/workspaces/{WS}/runs/run-xyz-999" and method == "GET":
            result_payload: dict[str, object] = {
                "committed": committed_files
                if committed_files is not None
                else [{"path": "threads/conv_1/runs/run-xyz-999/plot.png", "bytes": 8}],
                "failed": failed_commits if failed_commits is not None else [],
            }
            return httpx.Response(
                200,
                json={
                    "id": "run-xyz-999",
                    "state": run_state,
                    "stdout": run_stdout,
                    "stderr": run_stderr,
                    "error": run_error,
                    "result": result_payload,
                },
            )

        # 4. Fetch volume file
        if path.startswith(f"/api/v1/workspaces/{WS}/volumes/{VOL_ID}/files/"):
            # Return fake PNG image bytes
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfakeimagebytes")

        # 5. Delete thread in volume
        if f"/api/v1/workspaces/{WS}/volumes/{VOL_ID}/threads/" in path and method == "DELETE":
            return httpx.Response(204)

        # 6. SQL statement execution
        if path == f"/api/v1/workspaces/{WS}/sql/statements" and method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "stmt-abc-123",
                    "state": "succeeded",
                    "result": {
                        "columns": [
                            {"name": "id", "type": "BIGINT"},
                            {"name": "val", "type": "VARCHAR"},
                        ],
                        "rows": [[1, "alpha"], [2, "beta"]],
                        "export": {
                            "format": "parquet",
                            "volume": VOL_ID,
                            "path": export_path,
                            "rows": export_rows,
                            "bytes": 1024,
                        },
                    },
                },
            )

        return httpx.Response(404, json={"detail": f"Path not handled: {path}"})

    return requests, httpx.MockTransport(handler)


def test_safe_thread_id() -> None:
    assert safe_thread_id("conv-123_45") == "conv-123_45"
    assert safe_thread_id("user@email:123/45#foo") == "user_email_123_45_foo"
    long_id = "a" * 100
    assert len(safe_thread_id(long_id)) == 64
    assert safe_thread_id("!@#$%^") == "______"


@pytest.mark.asyncio
async def test_loamz_data_runtime_clean_run_with_image_artifact() -> None:
    reqs, transport = make_mock_handler(
        run_stdout="Generated chart successfully",
        committed_files=[{"path": "threads/conv_1/runs/run-xyz-999/chart.png", "bytes": 128}],
    )
    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("test-token-alice")
    runtime = LoamzDataRuntime(
        BASE_URL,
        broker,
        workspace=WS,
        schema="main.agents",
        transport=transport,
    )
    tool = CodeInterpreterTool(runtime)

    scope = RunScope.from_user(user, thread_id="conv-1")
    ctx = FakeCtx(scope, AsUser("user-auth"))

    res = await tool.execute(
        ctx=ctx,
        code="import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig(loamz.out('chart.png'))",
    )

    assert not res.is_error
    assert len(res.content) == 2
    text_block = res.content[0]
    assert isinstance(text_block, TextBlock)
    parsed = json.loads(text_block.text)
    assert parsed["success"] is True
    assert "Generated chart successfully" in parsed["output"]
    assert "[Generated chart.png]" in parsed["output"]

    media_block = res.content[1]
    assert isinstance(media_block, MediaBlock)
    assert media_block.media_type == "image/png"
    assert media_block.data == b"\x89PNG\r\n\x1a\nfakeimagebytes"

    # Verify requests
    paths = [r.url.path for r in reqs]
    assert f"/api/v1/workspaces/{WS}/runs" in paths
    assert f"/api/v1/workspaces/{WS}/runs/run-xyz-999" in paths
    assert (
        f"/api/v1/workspaces/{WS}/volumes/{VOL_ID}/files/threads/conv_1/runs/run-xyz-999/chart.png"
        in paths
    )


@pytest.mark.asyncio
async def test_loamz_data_runtime_failed_commit() -> None:
    _reqs, transport = make_mock_handler(
        run_stdout="processing",
        failed_commits=["chart.png: disk quota exceeded"],
    )
    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("test-token-alice")
    runtime = LoamzDataRuntime(
        BASE_URL,
        broker,
        workspace=WS,
        schema="main.agents",
        transport=transport,
    )

    spec = SandboxSpec(
        user_id="alice",
        thread_id="conv-1",
        session_dir="conv-1/main",
        code="loamz.out('chart.png')",
        extra={"user": user, "auth": AsUser("token"), "workspace": WS},
    )
    result = await runtime.execute(spec)

    assert result.exit_code == 1
    assert "Failed to commit files: chart.png: disk quota exceeded" in result.stderr


@pytest.mark.asyncio
async def test_loamz_data_runtime_409_retry_and_success() -> None:
    reqs, transport = make_mock_handler(
        initial_post_runs_status=409,
        run_stdout="success after retry",
        committed_files=[],
    )
    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("test-token-alice")
    runtime = LoamzDataRuntime(
        BASE_URL,
        broker,
        workspace=WS,
        transport=transport,
    )

    spec = SandboxSpec(
        user_id="alice",
        thread_id="conv-1",
        session_dir="conv-1/main",
        code="print('ok')",
        extra={"user": user, "auth": AsUser("token"), "workspace": WS},
    )
    result = await runtime.execute(spec)

    assert result.exit_code == 0
    assert result.stdout == "success after retry"

    post_runs_count = sum(
        1 for r in reqs if r.url.path == f"/api/v1/workspaces/{WS}/runs" and r.method == "POST"
    )
    assert post_runs_count == 2


@pytest.mark.asyncio
async def test_loamz_data_runtime_409_deadline_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    from loamz.runtime.toolkit.code.runtimes import loamz_data

    monkeypatch.setattr(loamz_data, "BUSY_RETRY_DEADLINE_S", 0.05)
    _reqs, transport = make_mock_handler(
        initial_post_runs_status=409,
    )

    # Configure handler to always return 409
    def handler_always_409(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/workspaces/{WS}/runs" and request.method == "POST":
            return httpx.Response(409, json={"title": "Conflict", "detail": "Thread busy"})
        return make_mock_handler()[1].handle_request(request)

    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("test-token-alice")
    runtime = LoamzDataRuntime(
        BASE_URL,
        broker,
        workspace=WS,
        transport=httpx.MockTransport(handler_always_409),
    )

    spec = SandboxSpec(
        user_id="alice",
        thread_id="conv-1",
        session_dir="conv-1/main",
        code="print('ok')",
        extra={"user": user, "auth": AsUser("token"), "workspace": WS},
    )
    result = await runtime.execute(spec)

    assert result.exit_code == 1
    assert "Thread is busy with another active job" in result.stderr


@pytest.mark.asyncio
async def test_loamz_data_runtime_timeout() -> None:
    _reqs, transport = make_mock_handler(
        run_state="timed_out",
        run_stdout="",
        run_stderr="",
        run_error="Job exceeded 60s execution limit",
    )
    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("test-token-alice")
    runtime = LoamzDataRuntime(
        BASE_URL,
        broker,
        workspace=WS,
        transport=transport,
    )

    spec = SandboxSpec(
        user_id="alice",
        thread_id="conv-1",
        session_dir="conv-1/main",
        code="import time; time.sleep(100)",
        extra={"user": user, "auth": AsUser("token"), "workspace": WS},
    )
    result = await runtime.execute(spec)

    assert result.exit_code == 1
    assert "timed out" in result.stderr or "exceeded" in result.stderr


@pytest.mark.asyncio
async def test_conversation_delete_thread() -> None:
    reqs, transport = make_mock_handler()
    user = UserCtx.of("alice", "acme")
    await delete_agent_volume_thread(
        BASE_URL,
        WS,
        user.subject,
        "token-123",
        "thread-conv-99",
        schema_spec="main.agents",
        transport=transport,
    )

    delete_reqs = [
        r
        for r in reqs
        if r.method == "DELETE"
        and r.url.path == f"/api/v1/workspaces/{WS}/volumes/{VOL_ID}/threads/thread-conv-99"
    ]
    assert len(delete_reqs) == 1
    assert delete_reqs[0].headers["authorization"] == "Bearer token-123"


@pytest.mark.asyncio
async def test_data_sql_tool_with_loamz_data_backend() -> None:
    reqs, transport = make_mock_handler(
        export_path="threads/conv_1/results/stmt_test.parquet",
        export_rows=42,
    )
    user = UserCtx.of("alice", "acme", attributes={"loamz.data/workspace": WS})
    broker = StaticBroker("token-sql-alice")
    conn = DataConnection(
        BASE_URL,
        broker,
        workspace=WS,
        code_backend="loamz-data",
        schema="main.agents",
        transport=transport,
    )
    sql_tool = DataSql(conn)

    scope = RunScope.from_user(user, thread_id="conv-1")
    ctx = FakeCtx(scope, AsUser("user-auth"))

    reply = await sql_tool.execute(
        ctx=ctx,
        call_id="call-sql-1",
        sql="SELECT id, val FROM main.sales.items",
    )

    assert not reply.is_error
    text = reply.content[0].text
    payload: Mapping[str, object] = json.loads(text)
    assert payload["columns"] == ["id", "val"]
    assert payload["rows"] == [[1, "alpha"], [2, "beta"]]
    assert payload["row_count"] == 42
    expected_sentence = (
        "The file is at threads/conv_1/results/stmt_test.parquet in the volume agent_files and is read in "
        'code with loamz.path("agent_files", "threads/conv_1/results/stmt_test.parquet") '
        "(Polars, DuckDB and pyarrow read it by range)."
    )
    assert payload["message"] == expected_sentence

    # Check request payload sent to POST /sql/statements
    post_stmt_reqs = [r for r in reqs if r.url.path == f"/api/v1/workspaces/{WS}/sql/statements"]
    assert len(post_stmt_reqs) == 1
    req_body = json.loads(post_stmt_reqs[0].content)
    assert req_body["sql"] == "SELECT id, val FROM main.sales.items"
    assert req_body["save"] == {"volume": VOL_ID, "thread": "conv-1"}
    assert req_body["idempotency_key"] == "call-sql-1"
