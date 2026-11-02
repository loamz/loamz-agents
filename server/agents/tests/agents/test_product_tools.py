"""A registry product's tools: found from its manifest, offered in the marketplace, built per the person's choices, and called over
real MCP with the credential the broker exchanged for that product's audience (never the person's own token)."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from loamz.runtime.auth.types import AsUser, Auth
from loamz.runtime.kernel.tools import ToolRisk
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx
from loamz.server.agents.bootstrap.products import ProductTools
from loamz.server.agents.bootstrap.settings import ProductsSettings
from loamz.server.agents.features.conversations.chat.builtin import tools_for
from loamz.server.agents.features.people.marketplace.products import product_listings
from loamz.server.agents.features.people.marketplace.service import MarketplaceService

MANIFEST = {
    "tools": [
        {
            "name": "say",
            "description": "Repeat a word.",
            "group": "read",
            "input_schema": {
                "type": "object",
                "properties": {"word": {"type": "string"}},
                "required": ["word"],
            },
        },
        {
            "name": "shout",
            "description": "Repeat a word loudly and remember it.",
            "group": "write",
            "input_schema": {
                "type": "object",
                "properties": {"word": {"type": "string"}},
                "required": ["word"],
            },
        },
    ],
    "listing": {"tagline": "Say it back", "icon": "Repeat"},
}


class Seen:
    def __init__(self, app: ASGIApp, seen: list[str]) -> None:
        self.app, self.seen = app, seen

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            self.seen.append(headers.get(b"authorization", b"").decode())
        await self.app(scope, receive, send)


class Echo:
    """A real MCP server (streamable HTTP) that records the Authorization header of every request."""

    def __init__(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.seen: list[str] = []
        mcp = FastMCP(
            "echo",
            host="127.0.0.1",
            port=self.port,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

        @mcp.tool()
        def say(word: str) -> str:
            return f"echo: {word}"

        self.server = uvicorn.Server(
            uvicorn.Config(
                Seen(mcp.streamable_http_app(), self.seen),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> Echo:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(5)


class Broker:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def credential(
        self, user: UserCtx | None, auth: Auth, *, audience: str, scopes: frozenset[str], ttl_s: int
    ) -> str:
        assert isinstance(auth, AsUser)
        self.asked.append((audience, auth.token))
        return f"exchanged-for-{audience}"


class Ctx:
    def __init__(self, scope: RunScope, auth: Auth | None) -> None:
        self.scope, self.auth = scope, auth


class Container:
    def __init__(self, products: ProductTools) -> None:
        self.products = products


def registry(tmp_path: Path, port: int) -> Path:
    path = tmp_path / "products.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "echo",
                    "name": "Loamz Echo",
                    "audience": "loamz-echo",
                    "state": "beta",
                    "mcp_url": f"http://127.0.0.1:{port}/mcp",
                    "manifest_url": "http://echo.test/tools",
                    "safe_tools": ["say"],
                }
            ]
        )
    )
    return path


async def products(tmp_path: Path, port: int, broker: Broker | None) -> ProductTools:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=MANIFEST))
    found = ProductTools(
        ProductsSettings(file=str(registry(tmp_path, port))), broker, transport=transport
    )  # type: ignore[arg-type]
    await found.refresh()
    return found


ANA = UserCtx.of("ana", "acme")
RUN = Ctx(RunScope.from_user(ANA), AsUser("ana-token"))


async def test_tools_come_from_the_manifest_and_a_call_uses_the_exchanged_credential(tmp_path):
    broker = Broker()
    with Echo() as echo:
        found = await products(tmp_path, echo.port, broker)
        [say, shout] = found.tools_of("echo")
        assert (say.name, shout.name) == ("echo__say", "echo__shout")
        assert (
            say.risk is ToolRisk.SAFE and shout.risk is ToolRisk.HIGH
        )  # only what the operator listed is safe
        result = await say.execute(ctx=RUN, call_id="c1", word="hi")
    assert not result.is_error and "echo: hi" in result.content[0].text
    assert broker.asked == [("loamz-echo", "ana-token")]
    assert "Bearer exchanged-for-loamz-echo" in echo.seen
    assert not any(
        "ana-token" in header for header in echo.seen
    )  # the person's own token is never forwarded


async def test_a_run_without_a_person_has_no_access_and_nothing_is_asked_of_the_broker(tmp_path):
    broker = Broker()
    found = await products(tmp_path, 1, broker)
    [say, _] = found.tools_of("echo")
    nobody = await say.execute(ctx=None, call_id="c")
    assert nobody.is_error and "signed-in person" in nobody.content[0].text
    assert broker.asked == []


async def test_a_product_that_is_down_has_no_tools_and_one_that_returns_junk_is_refused(tmp_path):
    down = ProductTools(
        ProductsSettings(file=str(registry(tmp_path, 1))),
        Broker(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    await down.refresh()
    assert down.tools_of("echo") == []
    junk = ProductTools(
        ProductsSettings(file=str(registry(tmp_path, 1))),
        Broker(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"tools": "no"})),
    )
    await junk.refresh()
    assert junk.tools_of("echo") == []


async def test_without_an_exchange_client_a_product_gives_no_tools(tmp_path):
    assert (await products(tmp_path, 1, None)).tools_of("echo") == []


async def test_tools_for_applies_the_choice_of_each_tools_own_group(tmp_path):
    found = await products(tmp_path, 1, Broker())
    container = Container(found)
    built = tools_for(container, {"echo": {"read": "allow", "write": "ask"}})  # type: ignore[arg-type]
    assert {t.name: t.risk for t in built} == {
        "echo__say": ToolRisk.SAFE,
        "echo__shout": ToolRisk.HIGH,
    }
    only_reading = tools_for(container, {"echo": {"read": "allow", "write": "block"}})  # type: ignore[arg-type]
    assert [t.name for t in only_reading] == ["echo__say"]
    assert tools_for(container, {"echo": {"read": "block", "write": "block"}}) == []  # type: ignore[arg-type]
    assert tools_for(container, {"not-a-product": {"read": "allow"}}) == []  # type: ignore[arg-type]


async def test_the_marketplace_lists_a_product_without_the_catalog_naming_it(tmp_path):
    found = await products(tmp_path, 1, Broker())
    [listing] = product_listings(found)
    assert (listing.id, listing.kind, listing.tagline, listing.icon) == (
        "echo",
        "tool",
        "Say it back",
        "Repeat",
    )
    assert {p.group: p.default for p in listing.permissions} == {"read": "allow", "write": "ask"}
    service = MarketplaceService(None, lambda: product_listings(found))  # type: ignore[arg-type]
    assert service.listing("echo").name == "Loamz Echo"
    assert "Loamz products" in service.catalog().categories
    assert any(i.id == "echo" for i in service.catalog(q="echo").items)
