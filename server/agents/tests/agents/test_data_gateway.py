"""The sandbox gateway: one socket per run, the identity lives on the host side, results are budgeted."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import jwt
import pytest

from loamz.runtime.data import DataGateway, DataTokenMinter
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.toolkit.code.runtimes.base import DataMount
from loamz.server.agents.bootstrap.data_access import GatewayDataAccess


@pytest.fixture
def root() -> Iterator[Path]:
    path = Path(
        tempfile.mkdtemp(prefix="gw-")
    )  # short: a Unix socket path is limited to ~100 bytes
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Upstream:
    """Stands in for the data service: records what reached it and answers with `body`."""

    def __init__(self, body: bytes = b"answer") -> None:
        self.seen: list[httpx.Request] = []
        self.body = body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(200, content=self.body, headers={"content-type": "text/plain"})


def sandbox_client(directory: Path) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=str(directory / "api.sock")),
        base_url="http://loamz-data",
    )


async def test_the_gateway_adds_the_runs_identity_and_ignores_the_one_the_sandbox_sends(root):
    upstream = Upstream()
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    channel = await gateway.open(root / "r1", lambda: "host-side-token")
    try:
        async with sandbox_client(channel.directory) as client:
            reply = await client.post(
                "/api/v1/statements",
                json={"query": "select 1"},
                headers={"Authorization": "Bearer forged-by-the-model"},
            )
        assert reply.status_code == 200 and reply.content == b"answer"
        assert upstream.seen[0].headers["authorization"] == "Bearer host-side-token"
    finally:
        await gateway.stop()


async def test_only_read_requests_on_the_allow_list_get_through(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    channel = await gateway.open(root / "r1", lambda: "t")
    try:
        async with sandbox_client(channel.directory) as client:
            assert (await client.get("/api/v1/search")).status_code == 200
            for method, path in (
                ("DELETE", "/api/v1/catalogs/main"),
                ("GET", "/api/v1/access-tokens"),
                ("GET", "/api/v1/catalogs/../tenants"),
            ):
                refused = await client.request(method, path)
                assert refused.status_code == 403
                assert refused.headers["content-type"] == "application/problem+json"
    finally:
        await gateway.stop()


async def test_a_run_cannot_receive_more_than_its_budget_and_is_told_what_to_do(root):
    upstream = Upstream(body=b"x" * 1000)
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    channel = await gateway.open(root / "r1", lambda: "t", budget_bytes=1500)
    try:
        async with sandbox_client(channel.directory) as client:
            assert (await client.get("/api/v1/search")).status_code == 200  # 1000 bytes
            assert (await client.get("/api/v1/search")).status_code == 200  # 2000: spent, cut short
            refused = await client.get("/api/v1/search")
        assert refused.status_code == 429
        assert "GROUP BY" in refused.json()["detail"]
        assert len(upstream.seen) == 2  # the third never reached the data service
        assert channel.stats()["refused"] == 1
    finally:
        await gateway.stop()


async def test_each_run_has_its_own_socket_and_closing_it_ends_the_access(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    a = await gateway.open(root / "a", lambda: "token-a")
    b = await gateway.open(root / "b", lambda: "token-b")
    try:
        assert a.socket_path != b.socket_path
        await gateway.close(a)
        assert not a.socket_path.exists()
        async with sandbox_client(b.directory) as client:
            assert (await client.get("/api/v1/search")).status_code == 200
        with pytest.raises(httpx.ConnectError):
            async with sandbox_client(a.directory) as client:
                await client.get("/api/v1/search")
    finally:
        await gateway.stop()


async def test_a_grant_speaks_for_the_runs_person_and_their_teams_and_ends_with_the_run(
    root, tmp_path
):
    upstream = Upstream()
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    minter = DataTokenMinter(tmp_path / "k.pem", issuer="agents", audience="data")
    access = GatewayDataAccess(gateway, minter, root)
    scope = RunScope(
        tenant_id="acme", user_id="priya", thread_id="t1", agent_id="analyst", groups=("sales",)
    )
    mount = await access.grant(scope, timeout_s=60)
    assert isinstance(mount, DataMount)
    try:
        async with sandbox_client(Path(mount.socket_dir)) as client:
            await client.get("/api/v1/search")
        claims = jwt.decode(
            upstream.seen[0].headers["authorization"].removeprefix("Bearer "),
            options={"verify_signature": False},
        )
        assert (claims["tenant_id"], claims["user_id"], claims["groups"]) == (
            "acme",
            "priya",
            ["sales"],
        )
        assert claims["scope"] == "data:read" and claims["act"] == {"sub": "agent:analyst"}
        assert claims["thread_id"] == "t1" and claims["run_id"]
    finally:
        await access.release(mount)
    assert not Path(mount.socket_dir).exists()
    await gateway.stop()


async def test_a_run_with_no_organization_gets_no_data(root, tmp_path):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    minter = DataTokenMinter(tmp_path / "k.pem")
    access = GatewayDataAccess(gateway, minter, root)
    assert await access.grant(RunScope(user_id="priya"), timeout_s=60) is None
    await gateway.stop()


def test_run_scope_carries_the_teams_to_children_and_ignores_malformed_ones():
    scope = RunScope.from_metadata(
        {"tenant_id": "acme", "user_id": "u", "groups": ["sales", "sales", "", "emea"]},
        thread_id="t",
        agent_id="a",
        agent_label="A",
    )
    assert scope.groups == ("emea", "sales")
    assert (
        RunScope.from_metadata(
            {"groups": "sales"}, thread_id="t", agent_id="a", agent_label="A"
        ).groups
        == ()
    )
    child = RunScope.from_metadata(
        scope.child_metadata(), thread_id="t", agent_id="b", agent_label="B"
    )
    assert child.groups == scope.groups
