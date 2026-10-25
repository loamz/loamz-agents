"""The picture proxy: an agent can put a web address in a card, but the person's browser never contacts it.

The server fetches the picture once, checks it, caches it and serves it from the console's own origin. So a slow site is slow once,
nothing about the person (their address, their cookies, that they are looking) reaches the site, and an address an agent chose cannot
point the server at anything inside the deployment. The rules, all enforced here:

- https only, port 443, no credentials in the address;
- the host's addresses are looked up and every one must be a public address (no loopback, private, link-local, reserved or multicast
  range, which includes cloud metadata services); the connection is then made to that address, so a host that answers differently the
  second time (DNS rebinding) is not followed anywhere else;
- a redirect is followed (three at most) only to an address that passes the same checks;
- at most `max_bytes`, and a picture: PNG, JPEG, GIF or WebP by the header and by the first bytes (SVG can carry script, so it is not);
- answers are cached for `ttl_s` (bounded in count and total size).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.server.agents.bootstrap.container import Container

MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_REDIRECTS = 3

Resolver = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True)
class Picture:
    content: bytes
    media_type: str


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def public(address: str) -> bool:
    """Whether a public host could have this address: not loopback, private, link-local, reserved or multicast (an IPv4 address written
    as IPv6 is judged as the IPv4 address it is)."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


def sniff(head: bytes) -> str | None:
    """The picture type the first bytes say it is."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


class PictureProxy:
    def __init__(
        self,
        *,
        max_bytes: int = 5_000_000,
        timeout_s: float = 8.0,
        ttl_s: float = 3600.0,
        max_entries: int = 256,
        max_total_bytes: int = 64_000_000,
        resolver: Resolver = resolve,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 8,
    ) -> None:
        self._max_bytes, self._timeout, self._ttl = max_bytes, timeout_s, ttl_s
        self._max_entries, self._max_total = max_entries, max_total_bytes
        self._resolve, self._transport = resolver, transport
        self._cache: OrderedDict[str, tuple[float, Picture]] = OrderedDict()
        self._slots = asyncio.Semaphore(concurrency)

    async def fetch(self, url: str) -> Picture:
        if (hit := self._cached(url)) is not None:
            return hit
        async with self._slots:
            picture = await self._fetch(url)
        self._remember(url, picture)
        return picture

    # ── the cache ────────────────────────────────────────────────────────────
    def _cached(self, url: str) -> Picture | None:
        entry = self._cache.get(url)
        if entry is None:
            return None
        stored_at, picture = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._cache[url]
            return None
        self._cache.move_to_end(url)
        return picture

    def _remember(self, url: str, picture: Picture) -> None:
        self._cache[url] = (time.monotonic(), picture)
        self._cache.move_to_end(url)
        while (
            len(self._cache) > self._max_entries
            or sum(len(p.content) for _, p in self._cache.values()) > self._max_total
        ):
            self._cache.popitem(last=False)

    # ── the fetch ────────────────────────────────────────────────────────────
    async def _fetch(self, url: str) -> Picture:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                host, address = await self._vet(url)
                try:
                    async with client.stream(
                        "GET",
                        pinned(url, address),
                        headers={
                            "Host": host,
                            "Accept": "image/png,image/jpeg,image/gif,image/webp",
                            "User-Agent": "loamz-picture-proxy/1",
                        },
                        extensions={"sni_hostname": host},
                    ) as response:
                        if response.is_redirect and (location := response.headers.get("location")):
                            url = urljoin(url, location)
                            continue
                        return await self._read(response)
                except httpx.HTTPError as error:
                    raise NotFoundError("The picture could not be fetched.") from error
        raise InvalidRequestError("Too many redirects.")

    async def _vet(self, url: str) -> tuple[str, str]:
        """(host, the public address to connect to) for an address that may be fetched; otherwise the reason it may not."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise InvalidRequestError("Only https pictures can be shown.")
        if parts.username or parts.password:
            raise InvalidRequestError("An address with a login in it cannot be fetched.")
        if parts.port not in (None, 443):
            raise InvalidRequestError("Only the standard https port is allowed.")
        host = parts.hostname
        try:
            addresses = [str(ipaddress.ip_address(host))]  # an address written as an address
        except ValueError:
            try:
                addresses = await self._resolve(host)
            except OSError as error:
                raise NotFoundError("That address could not be found.") from error
        if not addresses or not all(public(a) for a in addresses):
            raise InvalidRequestError("That address is not a public one.")
        return host, addresses[0]

    async def _read(self, response: httpx.Response) -> Picture:
        if response.status_code != 200:
            raise NotFoundError("The picture could not be fetched.")
        declared = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if declared not in MEDIA_TYPES:
            raise InvalidRequestError("That is not a PNG, JPEG, GIF or WebP picture.")
        if int(response.headers.get("content-length") or 0) > self._max_bytes:
            raise InvalidRequestError("That picture is too large.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > self._max_bytes:
                raise InvalidRequestError("That picture is too large.")
        found = sniff(bytes(body[:16]))
        if found is None or found != declared:
            raise InvalidRequestError("That is not the picture it says it is.")
        return Picture(bytes(body), found)


def pinned(url: str, address: str) -> str:
    """The same address with the host replaced by the public address that was checked (IPv6 in brackets)."""
    parts = urlsplit(url)
    ip = ipaddress.ip_address(address)
    netloc = f"[{ip}]" if ip.version == 6 else str(ip)
    return parts._replace(netloc=netloc).geturl()


def pictures(container: Container) -> PictureProxy:
    return PictureProxy()
