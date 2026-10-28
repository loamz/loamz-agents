"""Custom connectors point at an address a person typed, so every request to it is checked: only `https` and only the public internet.

The check runs on every request (and so on every redirect and every sign-in discovery step, which share the connection) and the
connection is made to the address that was checked, so a name that later resolves to something inside cannot be used.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from loamz.engine.errors import InvalidRequestError
from loamz.server.agents.features.conversations.chat.webtools import Refused, public_address

MAX_URL = 500
HTTPS_ONLY = True  # only tests, which talk to a server on this machine, turn it off


async def check_url(url: str) -> str:
    """The address cleaned up, or `InvalidRequestError` saying why it cannot be a connector."""
    url = url.strip()
    if len(url) > MAX_URL or (HTTPS_ONLY and urlsplit(url).scheme != "https"):
        raise InvalidRequestError(
            "Use the server's full https address, like https://mcp.example.com/mcp."
        )
    try:
        await public_address(url)
    except Refused as why:
        raise InvalidRequestError(str(why)) from why
    return url


class PublicOnly(httpx.AsyncBaseTransport):
    """Sends each request to the public address its host resolves to, and refuses anything else."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            scheme, host, port, ip = await public_address(str(request.url))
        except Refused as why:
            raise httpx.ConnectError(f"Blocked: {why}") from why
        if HTTPS_ONLY and scheme != "https":
            raise httpx.ConnectError("Blocked: connectors use https only.")
        request.headers["Host"] = host if port == 443 else f"{host}:{port}"
        request.extensions = {**request.extensions, "sni_hostname": host}
        request.url = request.url.copy_with(host=ip)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
