"""The registry products whose tools agents may use (see loamz-deploy `products/`).

The operator's registry file says which products exist, their audience and where their tools are. Each product's tools manifest is read
(best effort: a product that is down has no tools until the next refresh), and each tool then acts for the person of the run through the
credential broker. Adding a product is therefore a registry file: nothing in this server names it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.toolkit.mcp.product import (
    Manifest,
    ProductEndpoint,
    ProductTool,
    build_tools,
    fetch_manifest,
)
from loamz.server.agents.bootstrap.settings import ProductsSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductEntry:
    name: str
    endpoint: ProductEndpoint
    manifest: Manifest
    tools: tuple[ProductTool, ...]


def read_registry(path: str) -> list[tuple[str, ProductEndpoint]]:
    """(display name, endpoint) for each product in the exported registry file."""
    rows = json.loads(Path(path).read_text())
    return [
        (
            row["name"],
            ProductEndpoint(
                id=row["id"],
                audience=row["audience"],
                mcp_url=row["mcp_url"],
                manifest_url=row["manifest_url"],
                safe_tools=frozenset(row.get("safe_tools", [])),
            ),
        )
        for row in rows
    ]


class ProductTools:
    def __init__(
        self,
        settings: ProductsSettings,
        broker: CredentialBroker | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings, self.broker, self.transport = settings, broker, transport
        self.entries: dict[str, ProductEntry] = {}
        self._task: asyncio.Task[None] | None = None

    async def refresh(self) -> None:
        """Read every product's manifest again; one that fails keeps what it had."""
        if not self.settings.file:
            return
        if self.broker is None:
            logger.warning(
                "products are listed but no token exchange client is configured: agents get no product tools"
            )
            return
        for name, endpoint in read_registry(self.settings.file):
            try:
                manifest = await fetch_manifest(endpoint, transport=self.transport)
            except (httpx.HTTPError, ValueError) as error:
                logger.warning("tools of %s not read: %s", endpoint.id, error)
                continue
            tools = tuple(build_tools(endpoint, manifest, self.broker))
            self.entries[endpoint.id] = ProductEntry(name, endpoint, manifest, tools)

    def tools_of(self, product_id: str) -> list[ProductTool]:
        entry = self.entries.get(product_id)
        return list(entry.tools) if entry else []

    def start(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(self.settings.refresh_seconds)
                await self.refresh()

        self._task = asyncio.get_running_loop().create_task(loop(), name="product-tools-refresh")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
