import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from loamz.engine.errors import InvalidRequestError, NotFoundError
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


async def test_an_agent_gets_a_connectors_tools_only_when_it_was_given_that_connector(service):
    await sign_in(service, ALICE)
    assert await service.tools(ALICE, set()) == []
    assert {t.name for t in await service.tools(ALICE, {"fake"})} == {
        "fake__search",
        "fake__create_item",
        "fake__fill",
    }


# ── connectors added by address ────────────────────────────────────────────────
@pytest.fixture
def open_guard(monkeypatch):
    """The test server is on this machine, so let the address check through, and note what it was asked."""
    from loamz.server.agents.features.people.connections import guard

    asked: list[str] = []

    async def allow(url: str):
        asked.append(url)
        parts = urlparse(url)
        return parts.scheme, parts.hostname, parts.port or 443, parts.hostname

    monkeypatch.setattr(guard, "public_address", allow)
    monkeypatch.setattr(guard, "HTTPS_ONLY", False)
    return asked


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.example.com/mcp",
        "ftp://example.com/x",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/mcp",
        "https://user:pw@example.com/mcp",
        "https://example.com:8443/mcp",
        "not a url",
        "https://" + "a" * 600 + ".com/mcp",
    ],
)
async def test_a_custom_connector_must_be_a_public_https_address(service, url):
    with pytest.raises(InvalidRequestError):
        await service.add_custom(ALICE, "Mine", url)
    assert await service.custom_listings(ALICE) == []


async def test_the_guarded_connection_refuses_inside_addresses_and_pins_the_checked_one(
    monkeypatch,
):
    from loamz.server.agents.features.people.connections import guard

    seen = []

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(
                (str(request.url), request.headers["host"], request.extensions["sni_hostname"])
            )
            return httpx.Response(200, json={})

    async def resolve(url):
        host = urlparse(url).hostname
        if host == "inside.test":
            raise guard.Refused("That address is not on the public internet, so it is not opened.")
        return "https", host, 443, "93.184.216.34"

    monkeypatch.setattr(guard, "public_address", resolve)
    async with httpx.AsyncClient(transport=guard.PublicOnly(Inner())) as client:
        assert (await client.get("https://public.test/mcp?a=1")).status_code == 200
        with pytest.raises(httpx.ConnectError):
            await client.get("https://inside.test/mcp")
    assert seen == [("https://93.184.216.34/mcp?a=1", "public.test", "public.test")]


async def test_a_person_adds_their_own_connector_signs_in_and_its_tools_ask_first(
    service, server, open_guard
):
    mine = await service.add_custom(ALICE, "  Team   notes ", server.url)
    assert mine.id.startswith("custom-") and mine.name == "Team notes" and mine.trust == "community"
    assert [c.id for c in await service.custom_listings(ALICE)] == [mine.id]
    assert await service.custom_listings(BOB) == []  # theirs alone

    started = await service.start(ALICE, mine.id)
    async with httpx.AsyncClient() as browser:
        back = await browser.get(started["authorization_url"], follow_redirects=False)
    query = parse_qs(urlparse(back.headers["location"]).query)
    assert await service.finish(query["state"][0], query["code"][0], None) == (mine.id, True)

    tools = {t.name: t for t in await service.tools(ALICE)}
    assert set(tools) == {f"{mine.id}__search", f"{mine.id}__create_item", f"{mine.id}__fill"}
    assert {t.risk for t in tools.values()} == {
        ToolRisk.HIGH
    }  # nobody vetted these tools: each asks
    assert open_guard  # every request went through the address check

    await service._market.change(ALICE, mine.id, InstallPatch(permissions={"write": "allow"}))  # noqa: SLF001
    assert {t.risk for t in await service.tools(ALICE)} == {
        ToolRisk.SAFE
    }  # the person's own say-so
    await service._market.change(ALICE, mine.id, InstallPatch(permissions={"write": "block"}))  # noqa: SLF001
    assert await service.tools(ALICE) == []

    assert await service.tools(BOB) == []
    await service.disconnect(ALICE, mine.id)
    assert await service.custom_listings(ALICE) == [] and await service.status(ALICE) == []


async def test_an_access_token_is_sent_instead_of_the_sign_in_and_kept_sealed(
    service, db, open_guard
):
    mine = await service.add_custom(ALICE, "Keyed", "https://mcp.example.com/mcp", " sk-secret ")
    provider = service._provider(ALICE, mine, None, None)  # noqa: SLF001
    async with await service._client(ALICE, mine, provider) as client:  # noqa: SLF001
        assert client.headers["authorization"] == "Bearer sk-secret"
    async with db.transaction() as tx:
        row = await tx.fetchone("SELECT secret FROM people_custom_connectors")
    assert "sk-secret" not in row["secret"]  # sealed at rest


async def test_one_person_can_add_only_so_many_and_erasing_removes_them(service, open_guard):
    for n in range(10):
        await service.add_custom(ALICE, f"c{n}", f"https://mcp{n}.example.com/mcp")
    with pytest.raises(InvalidRequestError):
        await service.add_custom(ALICE, "one too many", "https://more.example.com/mcp")
    await service.erase("acme", "alice")
    assert await service.custom_listings(ALICE) == []


# ── which tools an agent may use ───────────────────────────────────────────────
async def test_the_live_tool_list_names_each_tool_its_group_and_the_servers_hint(service):
    with pytest.raises(InvalidRequestError):
        await service.list_tools(ALICE, "fake")  # not connected yet
    await sign_in(service, ALICE)
    listed = {t["name"]: t for t in await service.list_tools(ALICE, "fake")}
    assert set(listed) == {"search", "create_item", "fill"}
    assert listed["search"]["group"] == "read" and listed["search"]["read_hint"] is True
    assert listed["create_item"]["group"] == "write" and listed["create_item"]["read_hint"] is False
    assert [t["name"] for t in await service.list_tools(ALICE, "fake")][
        0
    ] == "search"  # reads first


async def test_an_agent_with_ticked_tools_gets_only_those_and_never_more_than_the_person_allowed(
    service,
):
    await sign_in(service, ALICE)
    market = service._market  # noqa: SLF001
    ticked = {"fake": {"search": "allow", "create_item": "ask"}}
    tools = {t.name: t for t in await service.tools(ALICE, {"fake"}, ticked)}
    assert set(tools) == {"fake__search", "fake__create_item"}  # fill was not ticked
    assert (
        tools["fake__search"].risk == ToolRisk.SAFE
        and tools["fake__create_item"].risk == ToolRisk.HIGH
    )
    # "ask" at the agent narrows a read the person allowed.
    asking = {t.name: t for t in await service.tools(ALICE, {"fake"}, {"fake": {"search": "ask"}})}
    assert asking["fake__search"].risk == ToolRisk.HIGH
    # The person's block wins over the agent's tick, and an allow at the agent cannot lift a write above asking.
    await market.change(ALICE, "fake", InstallPatch(permissions={"write": "block"}))
    assert [t.name for t in await service.tools(ALICE, {"fake"}, ticked)] == ["fake__search"]
    await market.change(ALICE, "fake", InstallPatch(permissions={"write": "allow"}))
    risky = {
        t.name: t for t in await service.tools(ALICE, {"fake"}, {"fake": {"create_item": "allow"}})
    }
    assert risky["fake__create_item"].risk == ToolRisk.HIGH
    # No entry for the connector: everything the person allows, as before. Tick choices for another connector change nothing here.
    assert len(await service.tools(ALICE, {"fake"}, {"other": {"x": "allow"}})) == 3
    assert len(await service.tools(ALICE, {"fake"}, None)) == 3
    # Not an agent (no `only`): the plain assistant ignores any choices.
    assert len(await service.tools(ALICE, None, ticked)) == 3


async def test_where_nobody_can_be_asked_only_tools_that_run_without_asking_are_offered(service):
    await sign_in(service, ALICE)
    names = {t.name for t in await service.tools(ALICE, {"fake"}, None, asking=False)}
    assert names == {"fake__search"}  # reads the person allowed; every write would need an answer


async def test_a_server_that_needs_no_sign_in_still_counts_as_connected_and_offers_its_tools(
    db, open_guard
):
    """A keyless server has no tokens, so no row until the connection is recorded: it must still show as connected and hand over its tools."""
    from loamz.server.agents.features.people.connections.store import ConnectionStore

    store = ConnectionStore(db, Reverse())
    assert await store.list("acme", "alice") == []
    await store.set_status("acme", "alice", "custom-x", "connected")
    await store.set_status("acme", "alice", "custom-x", "attention")
    assert [(c["id"], c["status"]) for c in await store.list("acme", "alice")] == [
        ("custom-x", "attention")
    ]
