"""The picture proxy: a web picture is fetched by the server under strict rules, never by the person's browser."""

from __future__ import annotations

import httpx
import pytest

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.conversations.pictures.service import PictureProxy, public
from loamz.server.agents.http import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PUBLIC = "93.184.216.34"


def resolver(table: dict[str, list[str]]):
    async def lookup(host: str) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return lookup


def proxy(handler, hosts: dict[str, list[str]] | None = None, **options) -> PictureProxy:
    return PictureProxy(
        resolver=resolver(hosts or {"img.example.com": [PUBLIC], "cdn.example.com": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
        **options,
    )


def picture(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})


async def test_a_public_https_picture_is_fetched_from_the_address_that_was_checked():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return picture(request)

    got = await proxy(handler).fetch("https://img.example.com/a.png?x=1")
    assert got.media_type == "image/png" and got.content == PNG
    assert (
        seen[0].url.host == PUBLIC and seen[0].headers["host"] == "img.example.com"
    )  # pinned, so a changed answer is not followed


async def test_a_second_request_is_served_from_the_cache():
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return picture(request)

    p = proxy(handler)
    await p.fetch("https://img.example.com/a.png")
    await p.fetch("https://img.example.com/a.png")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://img.example.com/a.png",
        "ftp://img.example.com/a.png",
        "https://user:pw@img.example.com/a.png",
        "https://img.example.com:8443/a.png",
        "https://127.0.0.1/a.png",
        "https://10.0.0.5/a.png",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/a.png",
        "https://[::ffff:10.0.0.1]/a.png",
        "https://localhost/a.png",
        "https://internal.example.com/a.png",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
async def test_an_address_that_is_not_a_public_https_one_is_refused_before_anything_is_sent(url):
    def handler(request):
        raise AssertionError("nothing may be sent")

    hosts = {
        "localhost": ["127.0.0.1"],
        "internal.example.com": ["10.1.2.3"],
        "img.example.com": [PUBLIC],
    }
    with pytest.raises(InvalidRequestError):
        await proxy(handler, hosts).fetch(url)


async def test_a_host_with_any_private_address_is_refused():
    def handler(request):
        raise AssertionError("nothing may be sent")

    with pytest.raises(InvalidRequestError):
        await proxy(handler, {"img.example.com": [PUBLIC, "192.168.1.1"]}).fetch(
            "https://img.example.com/a.png"
        )


async def test_a_redirect_is_followed_only_to_an_address_that_passes_the_same_checks():
    def handler(request):
        if request.headers["host"] == "img.example.com":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/real.png"})
        return picture(request)

    assert (await proxy(handler).fetch("https://img.example.com/a.png")).content == PNG

    def to_private(request):
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/"})

    with pytest.raises(InvalidRequestError):
        await proxy(to_private).fetch("https://img.example.com/a.png")

    def forever(request):
        return httpx.Response(302, headers={"location": "https://img.example.com/again"})

    with pytest.raises(InvalidRequestError, match="redirects"):
        await proxy(forever).fetch("https://img.example.com/a.png")


async def test_only_small_real_raster_pictures_are_served():
    def typed(body: bytes, kind: str):
        return lambda request: httpx.Response(200, content=body, headers={"content-type": kind})

    with pytest.raises(InvalidRequestError):  # a page that says it is a picture
        await proxy(typed(b"<html>hi</html>", "image/png")).fetch("https://img.example.com/a")
    with pytest.raises(InvalidRequestError):  # a picture that says it is another kind
        await proxy(typed(PNG, "image/jpeg")).fetch("https://img.example.com/a")
    with pytest.raises(InvalidRequestError):  # SVG can carry script
        await proxy(typed(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")).fetch(
            "https://img.example.com/a"
        )
    with pytest.raises(InvalidRequestError, match="large"):
        await proxy(typed(PNG + b"\x00" * 100, "image/png"), max_bytes=64).fetch(
            "https://img.example.com/a"
        )
    for head, kind in (
        (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 20, "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8, "image/webp"),
    ):
        assert (
            await proxy(typed(head, kind)).fetch("https://img.example.com/a")
        ).media_type == kind


async def test_a_missing_host_or_a_failing_site_is_not_found():
    with pytest.raises(NotFoundError):
        await proxy(picture, {}).fetch("https://nowhere.example.com/a.png")
    with pytest.raises(NotFoundError):
        await proxy(lambda r: httpx.Response(404)).fetch("https://img.example.com/a.png")


def test_what_counts_as_public():
    assert public(PUBLIC) and public("2606:4700:4700::1111")
    assert not any(public(a) for a in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.1.1", "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1"))  # fmt: skip


async def test_the_endpoint_refuses_what_the_service_refuses(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    app = create_app(container, roles={"all"})
    app.dependency_overrides[caller_dependency] = lambda: Caller(tenant_id="acme", user_id="alice")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for url in (
                "http://example.com/a.png",
                "https://127.0.0.1/a.png",
                "https://169.254.169.254/",
            ):
                response = await client.get("/api/v1/media/image", params={"url": url})
                assert response.status_code == 400, url
