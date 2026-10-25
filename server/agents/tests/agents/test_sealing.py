"""Chat content and stored tokens are sealed in the database: a dump holds no text, and a moved value does not open."""

from __future__ import annotations

import json

import httpx
import pytest

from loamz.engine.agents.kernel.runtime.sealing import SealError
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.bootstrap.sealing import AesSealer, new_key, sealer_from
from loamz.server.agents.features.governance.connector_tokens.model import TokenPush
from loamz.server.agents.features.governance.connector_tokens.store import ConnectorTokenStore
from loamz.server.agents.features.governance.workspace_oauth.store import WorkspaceTokenStore
from loamz.server.agents.http import create_app
from tests.agents.test_groups_and_feed import ALICE, Directory, create


def test_a_value_opens_only_where_it_was_sealed_and_with_its_key():
    sealer = AesSealer({"k1": new_key()})
    sealed = sealer.seal("group/1", "7:text", "see you at nine")
    assert sealed.startswith("sealed:v1:k1:") and "nine" not in sealed
    assert sealer.open("group/1", "7:text", sealed) == "see you at nine"
    assert sealer.seal("group/1", "7:text", "see you at nine") != sealed  # a fresh nonce each time
    for scope, where in (("group/2", "7:text"), ("group/1", "8:text"), ("group/1", "7:data")):
        with pytest.raises(SealError):
            sealer.open(scope, where, sealed)
    with pytest.raises(SealError):
        AesSealer({"k1": new_key()}).open("group/1", "7:text", sealed)
    with pytest.raises(SealError):
        sealer.open("group/1", "7:text", sealed[:-4] + "AAAA")
    assert sealer.open("group/1", "7:text", "written before sealing") == "written before sealing"


def test_keys_rotate_without_rewriting_everything():
    old, new = new_key(), new_key()
    sealed_then = AesSealer({"k1": old}).seal("a", "b", "old secret")
    ring = AesSealer({"k1": old, "k2": new}, primary="k2")
    assert ring.open("a", "b", sealed_then) == "old secret"
    assert ring.seal("a", "b", "new secret").startswith("sealed:v1:k2:")
    with pytest.raises(ValueError):
        AesSealer({"k1": "c2hvcnQ"})  # too short to be a key
    with pytest.raises(ValueError):
        AesSealer({"k1": new_key()}, primary="missing")
    assert sealer_from({}).seal("a", "b", "plain") == "plain"


async def test_a_chat_is_sealed_in_the_database_and_readable_through_the_api(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "agents.db")}, sealing={"keys": {"k1": new_key()}}
        )
    )
    container.__dict__["directory"] = Directory()
    app = create_app(container, roles={"all"})
    app.dependency_overrides[caller_dependency] = lambda: ALICE
    shown = [{"version": "v0.9", "createSurface": {"surfaceId": "plan", "catalogId": "c"}}]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            group = await create(client)
            path = f"/api/v1/groups/{group['id']}"
            sent = (
                await client.post(path + "/messages", json={"text": "the launch is on the 9th"})
            ).json()
            await client.patch(
                f"{path}/messages/{sent['seq']}", json={"text": "the launch moved to the 12th"}
            )
            await container.runtime.store.channel_append(
                f"group/{group['id']}",
                sender=__import__(
                    "loamz.engine.agents.kernel.types.identity", fromlist=["Actor"]
                ).Actor("member", "bot@g"),
                text="a plan",
                data={"ui": shown},
            )
            async with container.database.transaction() as tx:
                rows = [
                    dict(r)
                    for r in await tx.fetchall("SELECT text, data_json FROM rt_channel_entries")
                ]
            dump = json.dumps(rows)
            assert "launch" not in dump and "createSurface" not in dump and "a plan" not in dump
            assert any(r["text"].startswith("sealed:v1:k1:") for r in rows)
            entries = (await client.get(path + "/messages")).json()["entries"]
            assert entries[0]["text"] == "the launch moved to the 12th"
            assert entries[-1]["text"] == "a plan" and entries[-1]["ui"] == shown


async def test_tokens_are_sealed_per_person_and_do_not_open_for_another(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    await container.database.start()
    sealer = AesSealer({"k1": new_key()})
    workspace = WorkspaceTokenStore(container.database, sealer)
    connectors = ConnectorTokenStore(container.database, sealer)
    await workspace.put("acme", "alice", "ya29.alice-secret", 60)
    await connectors.put(
        "acme",
        "github",
        "alice",
        TokenPush(
            project_id="p1",
            access_token="gho_access",
            refresh_token="ghr_refresh",
            expires_at=2_000_000_000,
        ),
        60,
    )
    async with container.database.transaction() as tx:
        raw = json.dumps(
            [dict(r) for r in await tx.fetchall("SELECT * FROM governance_workspace_tokens")]
            + [dict(r) for r in await tx.fetchall("SELECT * FROM governance_connector_tokens")],
            default=str,
        )
    assert "alice-secret" not in raw and "gho_access" not in raw and "ghr_refresh" not in raw
    assert await workspace.get("acme", "alice") == "ya29.alice-secret"
    got = await connectors.get("acme", "github", "p1")
    assert (got.access_token, got.refresh_token) == ("gho_access", "ghr_refresh")
    async with container.database.transaction() as tx:  # a token copied to another person's row
        await tx.execute("UPDATE governance_workspace_tokens SET user_id = 'bob'")
    with pytest.raises(SealError):
        await workspace.get("acme", "bob")
