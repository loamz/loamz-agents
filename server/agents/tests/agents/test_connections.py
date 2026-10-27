import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolRisk
from loamz.server.agents.features.people.connections.service import ConnectionsService
from loamz.server.agents.features.people.connections.store import ConnectionStore
from loamz.server.agents.features.people.marketplace import service as market_module
from loamz.server.agents.features.people.marketplace.catalog import CATALOG
from loamz.server.agents.features.people.marketplace.model import InstallPatch, Listing, Permission
from loamz.server.agents.features.people.marketplace.service import MarketplaceService
from loamz.server.agents.features.people.marketplace.store import InstallStore

from .fake_mcp import FakeConnector

ALICE = Caller(tenant_id="acme", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")


class Reverse:
    """A sealer that is visibly not plain text."""

    def seal(self, scope: str, where: str, value: str) -> str:
        return "sealed:" + value[::-1]

    def open(self, scope: str, where: str, value: str) -> str:
        return value.removeprefix("sealed:")[::-1]


def listing(url: str) -> Listing:
    return Listing(
        id="fake",
        kind="connector",
        name="Fake",
        tagline="A test server",
        description="x",
        icon="Plug",
        category="Productivity",
        publisher="Test",
        trust="publisher",
        mcp_url=url,
        tools={"search": "read", "create_item": "write", "fill": "write"},
        permissions=[
            Permission(group="read", label="Look things up", default="allow"),
            Permission(group="write", label="Make items", default="ask"),
        ],
        added_at="2026-10-27",
    )


@pytest.fixture
def server():
    with FakeConnector() as s:
        yield s


@pytest.fixture
def service(db, server, monkeypatch):
    monkeypatch.setattr(market_module, "CATALOG", (*CATALOG, listing(server.url)))
    container = SimpleNamespace(
        settings=SimpleNamespace(
            http=SimpleNamespace(public_url="http://app.test", frontend_url="http://web.test")
        )
    )
    return ConnectionsService(
        ConnectionStore(db, Reverse()), MarketplaceService(InstallStore(db)), container
    )  # type: ignore[arg-type]


async def sign_in(service: ConnectionsService, caller: Caller) -> None:
    """Does what the person's browser does: opens the vendor's sign-in address, then follows it back to us."""
    started = await service.start(caller, "fake")
    async with httpx.AsyncClient() as browser:
        back = await browser.get(started["authorization_url"], follow_redirects=False)
    query = parse_qs(urlparse(back.headers["location"]).query)
    item, ok = await service.finish(query["state"][0], query["code"][0], None)
    assert (item, ok) == ("fake", True)


async def test_a_person_signs_in_and_the_assistant_gets_their_tools_shaped_by_their_choices(
    service, server
):
    assert await service.tools(ALICE) == []
    await sign_in(service, ALICE)
    assert [(c["id"], c["status"]) for c in await service.status(ALICE)] == [("fake", "connected")]

    tools = {t.name: t for t in await service.tools(ALICE)}
    assert set(tools) == {"fake__search", "fake__create_item", "fake__fill"}
    assert "propertyNames" not in json.dumps(
        tools["fake__fill"].input_schema
    )  # what the model sees is accepted by every provider
    assert tools["fake__search"].risk == ToolRisk.SAFE  # reads on "allow" run without asking
    assert tools["fake__create_item"].risk == ToolRisk.HIGH  # writes ask each time

    result = await tools["fake__search"].execute(query="roses", call_id="1")
    assert not result.is_error and "found roses" in result.content[0].text
    assert server.calls == [("search", {"query": "roses"})]


async def test_blocked_groups_are_never_offered_and_off_switches_everything_off(service):
    await sign_in(service, ALICE)
    market = service._market  # noqa: SLF001
    await market.change(ALICE, "fake", InstallPatch(permissions={"write": "block"}))
    assert sorted(t.name for t in await service.tools(ALICE)) == ["fake__search"]
    await market.change(ALICE, "fake", InstallPatch(enabled=False))
    assert await service.tools(ALICE) == []


async def test_a_curated_entry_offers_only_the_tools_we_chose(service, server, monkeypatch):
    curated = listing(server.url).model_copy(update={"tools": {"search": "read"}})
    monkeypatch.setattr(market_module, "CATALOG", (*CATALOG, curated))
    await sign_in(service, ALICE)
    assert [t.name for t in await service.tools(ALICE)] == [
        "fake__search"
    ]  # create_item and fill exist on the server but were not chosen


async def test_one_person_never_gets_another_persons_connection(service):
    await sign_in(service, ALICE)
    assert await service.tools(BOB) == []
    assert await service.status(BOB) == []
    with pytest.raises(NotFoundError):
        await service.finish("not-a-state", "code", None)


async def test_tokens_are_sealed_at_rest_and_disconnect_removes_them(service, db):
    await sign_in(service, ALICE)
    async with db.transaction() as tx:
        row = await tx.fetchone(
            "SELECT tokens, client_info FROM people_connections WHERE user_id = 'alice'"
        )
    assert row["tokens"].startswith("sealed:") and "access_token" not in row["tokens"]
    await service.disconnect(ALICE, "fake")
    assert await service.status(ALICE) == []
    assert await service.tools(ALICE) == []
    assert (await service._market.installed(ALICE)).items == []  # noqa: SLF001


async def test_a_lost_sign_in_asks_to_connect_again_instead_of_opening_a_page(service, server):
    await sign_in(service, ALICE)
    server.provider.access.clear()
    server.provider.refresh.clear()
    service._cache.clear()  # noqa: SLF001
    assert await service.tools(ALICE) == []
    assert [(c["id"], c["status"]) for c in await service.status(ALICE)] == [("fake", "attention")]


async def test_erasing_a_person_removes_their_connections(service):
    await sign_in(service, ALICE)
    assert await service.erase("acme", "alice") == 1
    assert await service.status(ALICE) == []


def test_a_tool_schema_keeps_only_what_model_providers_accept():
    from loamz.server.agents.features.people.connections.service import model_safe

    schema = {
        "$schema": "x",
        "type": "object",
        "properties": {
            "rich_text": {
                "type": "array",
                "items": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                        {"properties": {"bold": {"type": "boolean"}}},
                    ]
                },
            },
            "options": {
                "type": "object",
                "propertyNames": {"pattern": "^a"},
                "additionalProperties": {"type": "string"},
            },
            "odd": {
                "type": [{"type": "array"}],
                "oneOf": [{"type": "string"}, {"type": "integer"}],
            },
            "properties": {"type": "string", "unknownKeyword": 1},
        },
    }
    out = model_safe(schema)
    item = out["properties"]["rich_text"]["items"]
    assert (
        "allOf" not in item
        and set(item["properties"]) == {"text", "bold"}
        and item["required"] == ["text"]
    )
    assert "propertyNames" not in out["properties"]["options"] and out["properties"]["options"][
        "additionalProperties"
    ] == {"type": "string"}
    assert "type" not in out["properties"]["odd"] and out["properties"]["odd"]["anyOf"][1] == {
        "type": "integer"
    }
    assert set(out["properties"]) == {
        "rich_text",
        "options",
        "odd",
        "properties",
    }  # a property that is named like a keyword stays
    assert out["properties"]["properties"] == {"type": "string"} and "$schema" not in out


def test_every_tool_of_the_live_notion_server_is_reduced_to_accepted_keywords():
    """The same check as the one a provider makes: only whitelisted keywords remain anywhere in the schema."""
    import json
    from pathlib import Path

    from loamz.server.agents.features.people.connections.service import _KEEP, model_safe

    path = Path(__file__).with_name("notion_tools.json")
    for tool in json.loads(path.read_text()):

        def walk(node, names=False, tool=tool):  # noqa: ANN001, ANN202
            if isinstance(node, dict):
                for key, value in node.items():
                    if names:
                        walk(value, tool=tool)
                    else:
                        assert key in _KEEP, (tool["name"], key)
                        walk(value, names=key in {"properties", "$defs", "definitions"}, tool=tool)
            elif isinstance(node, list):
                for v in node:
                    walk(v, tool=tool)

        walk(model_safe(tool["inputSchema"]))
