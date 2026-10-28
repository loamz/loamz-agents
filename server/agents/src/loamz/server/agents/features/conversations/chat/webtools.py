"""Tools that reach the open web on a person's behalf. The page reader only ever fetches public addresses: a model can be talked into
asking for anything, so the address is checked here, on every hop, and the connection is made to the address that was checked."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
TIMEOUT = 12.0
PORTS = {80, 443}
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
)
AGENT = "LoamzBot/1.0 (+https://loamz.com; reads one page when a person asks)"


class Refused(ValueError):
    """The address is not one the page reader will open."""


async def public_address(url: str) -> tuple[str, str, int, str]:
    """`(scheme, host, port, ip)` for a URL that points at the public internet; raises `Refused` for anything else.

    Refused: other schemes, credentials in the address, ports other than 80 and 443, and any host that resolves to a private,
    loopback, link-local, multicast, reserved or cloud-metadata address (every address it resolves to must be public).
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise Refused("Only http and https addresses can be opened.")
    if parts.username or parts.password:
        raise Refused("Addresses with a username or password are not opened.")
    host = parts.hostname
    if not host:
        raise Refused("That is not a web address.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in PORTS:
        raise Refused("Only the usual web ports are opened.")
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise Refused("That address could not be found.") from exc
    addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in found}
    if not addresses or not all(a.is_global and not a.is_multicast for a in addresses):
        raise Refused("That address is not on the public internet, so it is not opened.")
    return parts.scheme, host, port, str(sorted(addresses, key=str)[0])


def _text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "form", "iframe", "header", "aside"]
    ):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    blocks = [
        re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        for el in root.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote", "td"])
    ]
    return title, "\n\n".join(b for b in blocks if len(b) > 2)


def relevant(text: str, query: str | None, limit: int) -> str:
    """The paragraphs that share the most words with `query`, kept in reading order, up to `limit` characters."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not query or not paragraphs:
        return text[:limit]
    wanted = set(query.lower().split())
    order = sorted(
        range(len(paragraphs)),
        key=lambda i: len(wanted & set(paragraphs[i].lower().split())),
        reverse=True,
    )
    chosen: list[int] = []
    used = 0
    for i in order:
        if not wanted & set(paragraphs[i].lower().split()):
            break  # the rest share nothing with the question
        if used + len(paragraphs[i]) > limit:
            continue
        chosen.append(i)
        used += len(paragraphs[i])
    return "\n\n".join(paragraphs[i] for i in sorted(chosen)) or paragraphs[order[0]][:limit]


class ReadPageTool:
    """Open one public web page and return its text, or the parts that matter for a question."""

    name = "read_page"
    risk = ToolRisk.SAFE
    idempotent = True
    description = (
        "Open a public web page and return its text. Pass `query` (what you are looking for) to get only the relevant parts. "
        "Use it to read a page you found or were given; it cannot open private or local addresses."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full web address, for example https://example.com/article.",
            },
            "query": {"type": "string", "description": "What you are looking for on the page."},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> tuple[str, str, str]:
        """`(final url, content type, body)` after following redirects by hand, checking each address."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, port, ip = await public_address(current)
            parts = urlsplit(current)
            target = f"{scheme}://{f'[{ip}]' if ':' in ip else ip}:{port}{parts.path or '/'}"
            if parts.query:
                target += f"?{parts.query}"
            async with client.stream(
                "GET",
                target,
                headers={
                    "Host": host if port in (80, 443) else f"{host}:{port}",
                    "User-Agent": AGENT,
                    "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                },
                extensions={"sni_hostname": host},
                follow_redirects=False,
            ) as response:
                if response.is_redirect and (where := response.headers.get("location")):
                    current = urlunsplit(urlsplit(urljoin(current, where))._replace(fragment=""))
                    continue
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if response.status_code >= 400:
                    raise Refused(f"The site answered {response.status_code}.")
                if kind and not kind.startswith(TEXT_TYPES):
                    raise Refused(f"That is a {kind} file, not a page of text.")
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                return (
                    current,
                    kind,
                    body[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace"),
                )
        raise Refused("The page redirects too many times.")

    async def execute(
        self, *, ctx: object = None, url: str = "", query: str = "", **_: object
    ) -> ToolExecutionResult:  # type: ignore[override]
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT, verify=True)
        try:
            final, kind, body = await self._fetch(client, url)
        except Refused as why:
            return ToolExecutionResult(content=[TextBlock(text=str(why))], is_error=True)
        except httpx.HTTPError as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"The page could not be read: {type(exc).__name__}.")],
                is_error=True,
            )
        finally:
            if self._client is None:
                await client.aclose()
        title, text = _text(body) if "html" in kind or "<html" in body[:500].lower() else ("", body)
        out = relevant(text, query or None, 6000)
        head = f"{title}\n" if title else ""
        return ToolExecutionResult(
            content=[TextBlock(text=f"Source: {final}\n{head}\n{out}".strip())]
        )
