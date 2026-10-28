"""Loamz's own tools: the page reader only opens public addresses, an agent gets exactly the tools it was given, and acting tools ask."""

from __future__ import annotations

import httpx
import pytest

from loamz.runtime.kernel.tools import ToolRisk
from loamz.server.agents.features.conversations.chat.builtin import BUILDERS, tools_for
from loamz.server.agents.features.conversations.chat.webtools import (
    ReadPageTool,
    Refused,
    public_address,
    relevant,
)
from loamz.server.agents.features.people.marketplace.catalog import CATALOG


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://example.com:22/",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "not a url",
    ],
)
async def test_the_page_reader_refuses_anything_that_is_not_the_public_web(url):
    with pytest.raises(Refused):
        await public_address(url)


async def test_a_public_address_is_accepted_and_a_name_that_resolves_inside_is_not(monkeypatch):
    async def fake(host, port, **_):
        return {
            "public.test": [(2, 1, 6, "", ("93.184.216.34", port))],
            "sneaky.test": [
                (2, 1, 6, "", ("93.184.216.34", port)),
                (2, 1, 6, "", ("10.0.0.7", port)),
            ],
        }[host]

    loop = __import__("asyncio").get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake)
    assert await public_address("https://public.test/a?b=1") == (
        "https",
        "public.test",
        443,
        "93.184.216.34",
    )
    with pytest.raises(Refused):
        await public_address(
            "https://sneaky.test/"
        )  # one inside address among public ones is enough to refuse


async def test_the_page_reader_checks_every_redirect_and_returns_the_relevant_text(monkeypatch):
    html = "<html><head><title>Tea</title></head><body><nav>menu</nav><main><p>Green tea is picked in spring and steamed soon after.</p><p>Black tea is fully oxidised.</p><p>Unrelated words about gardening and weather and more filler text.</p></main></body></html>"

    async def fake(host, port, **_):
        return [
            (2, 1, 6, "", ({"public.test": "93.184.216.34", "inside.test": "10.0.0.7"}[host], port))
        ]

    monkeypatch.setattr(__import__("asyncio").get_running_loop(), "getaddrinfo", fake)

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.headers["host"] in {"public.test", "inside.test"}
            and request.url.host == "93.184.216.34"
        )  # the checked address is the one connected to
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://inside.test/secret"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    tool = ReadPageTool(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    good = await tool.execute(url="https://public.test/tea", query="green tea spring")
    text = good.content[0].text
    assert (
        not good.is_error
        and "Source: https://public.test/tea" in text
        and "Green tea is picked in spring" in text
        and "menu" not in text
    )
    bad = await tool.execute(url="https://public.test/hop")
    assert (
        bad.is_error and "not on the public internet" in bad.content[0].text
    )  # a redirect to an inside address is refused
    assert (await tool.execute(url="http://127.0.0.1:8080/admin")).is_error


def test_relevant_keeps_reading_order_and_the_limit():
    text = "alpha beta\n\ngamma tea tea\n\ndelta tea"
    assert relevant(text, "tea", 100) == "gamma tea tea\n\ndelta tea"
    assert relevant(text, None, 5) == "alpha"


def test_every_built_in_tool_in_the_catalog_can_be_built_and_only_those():
    tool_ids = {i.id for i in CATALOG if i.kind == "tool"}
    assert set(BUILDERS) <= tool_ids and tool_ids - set(BUILDERS) == {
        "documents",
        "code-interpreter",
    }  # those two are built elsewhere, for every agent
    assert {
        "web-search",
        "read-page",
        "wikipedia",
        "image-generation",
        "loamz-data",
        "weather",
        "currency",
        "papers",
        "packages",
    } == set(BUILDERS)


def test_tools_are_built_as_the_person_allowed_them():
    from types import SimpleNamespace

    container = SimpleNamespace(settings=SimpleNamespace(llm=SimpleNamespace(openai_api_key="k")))
    built = tools_for(
        container,
        {
            "currency": {},
            "web-search": {"read": "allow"},
            "wikipedia": {"read": "block"},
            "image-generation": {"write": "ask"},
        },
    )  # type: ignore[arg-type]
    by_name = {t.name: t for t in built}
    assert set(by_name) == {
        "currency",
        "web_search",
        "image_generator",
    }  # a blocked tool is never built
    image = by_name["image_generator"]
    assert image.risk == ToolRisk.HIGH  # creating something that costs money asks each time
    allowed = tools_for(container, {"image-generation": {"write": "allow"}})  # type: ignore[arg-type]
    assert allowed[0].risk == ToolRisk.SAFE


def _served(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        for part, reply in routes.items():
            if part in str(request.url):
                return reply
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _text(result) -> str:
    return result.content[0].text


async def test_weather_names_the_place_and_the_conditions():
    from loamz.server.agents.features.conversations.chat.lookups import WeatherTool

    client = _served(
        {
            "geocoding": httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Paris",
                            "admin1": "Île-de-France",
                            "country": "France",
                            "latitude": 48.8,
                            "longitude": 2.3,
                        }
                    ]
                },
            ),
            "forecast": httpx.Response(
                200,
                json={
                    "current": {
                        "temperature_2m": 14.2,
                        "apparent_temperature": 13.0,
                        "relative_humidity_2m": 70,
                        "wind_speed_10m": 9.5,
                        "precipitation": 0.0,
                        "weather_code": 3,
                    },
                    "daily": {
                        "time": ["2026-10-28"],
                        "temperature_2m_min": [9.0],
                        "temperature_2m_max": [16.0],
                        "weather_code": [61],
                        "precipitation_sum": [2.4],
                    },
                },
            ),
        }
    )
    result = await WeatherTool(client).execute(place="Paris, France")
    text = _text(result)
    assert (
        "Paris, Île-de-France, France: 14.2°C" in text
        and "overcast" in text
        and "light rain" in text
    )
    unknown = await WeatherTool(_served({"geocoding": httpx.Response(200, json={})})).execute(
        place="Nowhereville"
    )
    assert unknown.is_error and "No place called" in _text(unknown)


async def test_currency_converts_and_refuses_bad_codes():
    from loamz.server.agents.features.conversations.chat.lookups import CurrencyTool

    client = _served(
        {
            "frankfurter": httpx.Response(
                200,
                json={"amount": 100, "base": "USD", "date": "2026-10-27", "rates": {"INR": 8350.5}},
            )
        }
    )
    ok = await CurrencyTool(client).execute(amount=100, to="inr", **{"from": "usd"})
    assert _text(ok) == "100 USD = 8350.5 INR (rate of 2026-10-27, ECB reference)."
    bad = await CurrencyTool(client).execute(to="EUR", **{"from": "dollars"})
    assert bad.is_error
    assert (
        await CurrencyTool(client).execute(to="EUR", date="yesterday", **{"from": "USD"})
    ).is_error


ARXIV = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1706.03762</id>
<title>Attention Is All
 You Need</title><published>2017-06-12T17:57:34Z</published><summary>The dominant sequence models...</summary>
<author><name>A</name></author><author><name>B</name></author><author><name>C</name></author><author><name>D</name></author></entry></feed>"""


async def test_papers_lists_titles_and_rejects_entity_tricks():
    from loamz.server.agents.features.conversations.chat.lookups import PapersTool

    ok = await PapersTool(_served({"arxiv": httpx.Response(200, text=ARXIV)})).execute(
        query="attention transformer"
    )
    text = _text(ok)
    assert (
        "**Attention Is All You Need**" in text
        and "A, B, C et al." in text
        and "2017-06-12" in text
    )
    evil = '<!DOCTYPE x [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"/>'
    assert (
        await PapersTool(_served({"arxiv": httpx.Response(200, text=evil)})).execute(query="x")
    ).is_error


async def test_packages_looks_up_each_ecosystem_and_refuses_odd_names():
    from loamz.server.agents.features.conversations.chat.lookups import PackagesTool

    client = _served(
        {
            "pypi.org": httpx.Response(
                200,
                json={
                    "info": {
                        "name": "requests",
                        "version": "2.32.0",
                        "summary": "HTTP for Humans",
                        "license": "Apache-2.0",
                        "home_page": "https://requests.readthedocs.io",
                    }
                },
            ),
            "registry.npmjs.org/@types%2Fnode": httpx.Response(
                200,
                json={
                    "name": "@types/node",
                    "version": "22.0.0",
                    "description": "types",
                    "license": "MIT",
                },
            ),
            "crates.io": httpx.Response(
                200,
                json={
                    "crate": {
                        "name": "serde",
                        "max_stable_version": "1.0.200",
                        "newest_version": "1.0.201",
                        "description": "serialise",
                    }
                },
            ),
        }
    )
    tool = PackagesTool(client)
    assert "pip install requests" in _text(await tool.execute(ecosystem="pypi", name="requests"))
    assert "requests 2.32.0" in _text(await tool.execute(ecosystem="pypi", name="requests"))
    assert "npm install @types/node" in _text(
        await tool.execute(ecosystem="npm", name="@types/node")
    )
    assert "serde 1.0.200" in _text(await tool.execute(ecosystem="crates", name="serde"))
    for name in ("../etc/passwd", "a b", "x?y=1", ""):
        assert (await tool.execute(ecosystem="pypi", name=name)).is_error


def test_every_builder_has_a_catalog_entry_and_the_data_tool_is_offered():
    ids = {item.id for item in CATALOG}
    assert set(BUILDERS) <= ids
    assert {"loamz-data", "weather", "currency", "papers", "packages"} <= set(BUILDERS)


class _Settings:
    class llm:  # noqa: N801
        openai_api_key = ""


class _FakeContainer:
    settings = _Settings()
    data_tool_set = {"data_search": object(), "data_describe": object(), "data_sql": object()}


def test_loamz_data_brings_search_describe_and_sql_and_nothing_without_a_service():
    tools = tools_for(_FakeContainer(), {"loamz-data": {"read": "allow"}})  # type: ignore[arg-type]
    assert len(tools) == 3
    none = _FakeContainer()
    none.data_tool_set = {}
    assert tools_for(none, {"loamz-data": {"read": "allow"}}) == []  # type: ignore[arg-type]
