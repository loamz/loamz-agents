import httpx

from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.people.marketplace.catalog import CATALOG
from loamz.server.agents.features.people.marketplace.model import InstallBody
from loamz.server.agents.features.people.marketplace.service import MarketplaceService
from loamz.server.agents.features.people.marketplace.store import InstallStore
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")


def test_the_catalog_never_starts_writing_or_deleting_as_allowed():
    for item in CATALOG:
        for permission in item.permissions:
            assert permission.group == "read" or permission.default != "allow", item.id
        assert len(item.tagline) <= 80, item.id


async def test_installs_belong_to_one_person_and_connectors_need_an_account(db):
    service = MarketplaceService(InstallStore(db))
    record = await service.install(ALICE, "documents", InstallBody(permissions={"write": "allow"}))
    assert record.permissions == {"read": "allow", "write": "allow", "delete": "ask"}
    assert [i.id for i in (await service.installed(ALICE)).items] == ["documents"]
    assert (await service.installed(BOB)).items == []
    await service.remove(ALICE, "documents")
    assert (await service.installed(ALICE)).items == []
    try:
        await service.install(ALICE, "gmail", InstallBody())
    except Exception as e:  # noqa: BLE001 - the typed error is what the API turns into a problem document
        assert "connected account" in str(e)
    else:
        raise AssertionError("a connector installed without an account")


def test_the_catalog_can_be_searched_and_filtered(db=None):
    service = MarketplaceService(InstallStore(None))  # type: ignore[arg-type]
    assert {i.kind for i in service.catalog(kind="connector").items} == {"connector"}
    assert [i.id for i in service.catalog(q="calendar").items] == ["google-calendar"]
    assert [i.name for i in service.catalog(sort="name").items] == sorted(i.name for i in CATALOG)


async def test_the_api_lists_installs_and_removes(tmp_path):
    app = create_app(Container(Settings(database={"path": str(tmp_path / "agents.db")})))
    app.dependency_overrides[caller_dependency] = lambda: ALICE
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            catalog = (await client.get("/api/v1/marketplace?kind=skill")).json()
            assert catalog["items"] and all(i["kind"] == "skill" for i in catalog["items"])
            assert (await client.put("/api/v1/me/installs/summarize", json={})).status_code == 200
            assert [i["id"] for i in (await client.get("/api/v1/me/installs")).json()["items"]] == [
                "summarize"
            ]
            assert (await client.put("/api/v1/me/installs/nope", json={})).status_code == 404
            assert (await client.delete("/api/v1/me/installs/summarize")).status_code == 204
            assert (await client.get("/api/v1/me/installs")).json()["items"] == []
