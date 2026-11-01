"""The sandbox gateway: one socket per run, the identity lives on the host side, requests are held to the run's workspace, results are budgeted."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from loamz.runtime.auth import AsServicePrincipal, AsUser, StaticBroker
from loamz.runtime.data import DataGateway, LoamzData, WorkspaceReadPolicy
from loamz.runtime.data.policy import AllowAll
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx
from loamz.runtime.toolkit.code.data_access import DataGrant


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


WS = "9cce7e59-e1c0-4f8b-ab20-ff93a79161ca"
OTHER = "11111111-2222-3333-4444-555555555555"


def _token(value: str):  # type: ignore[no-untyped-def]
    async def token() -> str:
        return value

    return token


async def test_the_gateway_adds_the_runs_identity_and_ignores_the_one_the_sandbox_sends(root):
    upstream = Upstream()
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    channel = await gateway.open(root / "r1", _token("host-side-token"), policy=AllowAll())
    try:
        async with sandbox_client(channel.directory) as client:
            reply = await client.post(
                "/anything",
                json={"query": "select 1"},
                headers={"Authorization": "Bearer forged-by-the-model"},
            )
        assert reply.status_code == 200 and reply.content == b"answer"
        assert upstream.seen[0].headers["authorization"] == "Bearer host-side-token"
    finally:
        await gateway.stop()


async def test_only_read_requests_for_the_runs_own_workspace_get_through(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    channel = await gateway.open(root / "r1", _token("t"), policy=WorkspaceReadPolicy(WS))
    base = f"/api/v1/workspaces/{WS}"
    try:
        async with sandbox_client(channel.directory) as client:
            assert (await client.post(f"{base}/sql/statements", json={})).status_code == 200
            assert (await client.get(f"{base}/runs/abc-123")).status_code == 200
            assert (await client.get(f"{base}/securables")).status_code == 200
            for method, path in (
                ("POST", f"/api/v1/workspaces/{OTHER}/sql/statements"),  # another workspace
                ("DELETE", f"{base}/securables/x"),
                (
                    "GET",
                    f"{base}/iceberg/v1/config",
                ),  # hands out storage credentials: never through a sandbox
                ("PUT", f"{base}/volumes/v/files/a"),
                ("GET", f"{base}/securables/../../orgs"),
                ("GET", "/api/v1/me"),
            ):
                refused = await client.request(method, path)
                assert refused.status_code == 403, (method, path)
                assert refused.headers["content-type"] == "application/problem+json"
    finally:
        await gateway.stop()


async def test_a_run_cannot_receive_more_than_its_budget_and_is_told_what_to_do(root):
    upstream = Upstream(body=b"x" * 1000)
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    channel = await gateway.open(root / "r1", _token("t"), policy=AllowAll(), budget_bytes=1500)
    try:
        async with sandbox_client(channel.directory) as client:
            assert (await client.get("/x")).status_code == 200  # 1000 bytes
            assert (await client.get("/x")).status_code == 200  # 2000: spent, cut short
            refused = await client.get("/x")
        assert refused.status_code == 429
        assert "GROUP BY" in refused.json()["detail"]
        assert len(upstream.seen) == 2  # the third never reached the data service
        assert channel.stats()["refused"] == 1
    finally:
        await gateway.stop()


async def test_each_run_has_its_own_socket_and_closing_it_ends_the_access(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    a = await gateway.open(root / "a", _token("token-a"), policy=AllowAll())
    b = await gateway.open(root / "b", _token("token-b"), policy=AllowAll())
    try:
        assert a.socket_path != b.socket_path
        await gateway.close(a)
        assert not a.socket_path.exists()
        async with sandbox_client(b.directory) as client:
            assert (await client.get("/x")).status_code == 200
        with pytest.raises(httpx.ConnectError):
            async with sandbox_client(a.directory) as client:
                await client.get("/x")
    finally:
        await gateway.stop()


class _Spy(StaticBroker):
    """A broker that records what each call asked for."""

    def __init__(self) -> None:
        super().__init__("exchanged-token")
        self.asked: list[tuple[UserCtx | None, object, str, frozenset[str]]] = []

    async def credential(self, user, auth, *, audience, scopes, ttl_s):  # type: ignore[no-untyped-def]
        self.asked.append((user, auth, audience, scopes))
        return await super().credential(user, auth, audience=audience, scopes=scopes, ttl_s=ttl_s)


ANA = UserCtx.of("ana", "acme", groups=["sales"], attributes={"loamz.data/workspace": WS})


async def test_a_grant_asks_as_the_person_for_the_data_audience_and_ends_with_the_run(root):
    upstream = Upstream()
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(upstream))
    broker = _Spy()
    source = LoamzData(gateway, broker, root)
    grant = await source.grant(
        RunScope.from_user(ANA, thread_id="t1"), AsUser("ana-token"), timeout_s=60
    )
    assert isinstance(grant, DataGrant)
    mount = grant.mount
    try:
        assert dict(grant.env)["LOAMZ_DATA_WORKSPACE"] == WS
        async with sandbox_client(Path(mount.socket_dir)) as client:
            reply = await client.post(f"/api/v1/workspaces/{WS}/sql/statements", json={})
            other = await client.post(f"/api/v1/workspaces/{OTHER}/sql/statements", json={})
        assert (reply.status_code, other.status_code) == (200, 403)
        assert upstream.seen[0].headers["authorization"] == "Bearer exchanged-token"
        user, auth, audience, scopes = broker.asked[0]
        assert user == ANA and auth == AsUser("ana-token")
        assert (audience, scopes) == ("loamz-data", frozenset())
    finally:
        await source.release(grant)
    assert not Path(mount.socket_dir).exists()
    await gateway.stop()


async def test_a_service_principal_run_needs_no_person_but_still_a_workspace(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    source = LoamzData(gateway, _Spy(), root, workspace=WS)
    grant = await source.grant(RunScope(), AsServicePrincipal(), timeout_s=60)
    assert grant is not None and dict(grant.env)["LOAMZ_DATA_WORKSPACE"] == WS
    await source.release(grant)
    await gateway.stop()


async def test_no_authority_no_person_or_no_workspace_means_no_data(root):
    gateway = DataGateway("http://127.0.0.1:1", transport=httpx.MockTransport(Upstream()))
    source = LoamzData(gateway, _Spy(), root)
    scope = RunScope.from_user(ANA)
    assert await source.grant(scope, None, timeout_s=60) is None  # nothing proves who is asking
    assert (
        await source.grant(RunScope(user_id="ana"), AsUser("t"), timeout_s=60) is None
    )  # no organization
    no_workspace = RunScope.from_user(UserCtx.of("ana", "acme"))
    assert await source.grant(no_workspace, AsUser("t"), timeout_s=60) is None
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
