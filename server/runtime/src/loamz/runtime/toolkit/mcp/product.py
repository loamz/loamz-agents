"""A Loamz product's tools, reached over MCP with the credential of the person the run is for.

A product that agents may use publishes a tools manifest (descriptive, public) and an MCP endpoint. The manifest is read once to know
which tools exist; each call then asks the credential broker for a token for the product's audience (RFC 8693 exchange: the person's
rights, with this server as the party acting) and opens a connection with it. So the product enforces what that person may do, and
this server holds no credential of its own. Nothing here names a product.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.kernel.types.run import scope_of
from loamz.runtime.toolkit.mcp.client import MCPClient
from loamz.runtime.toolkit.mcp.tool import MCPTool

MAX_MANIFEST_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProductEndpoint:
    """Where a product's tools are, from the registry (the operator's word, not the product's)."""

    id: str  # lower-case, digits, hyphens: prefixes every tool as <id with _>__<tool>
    audience: str
    mcp_url: str
    manifest_url: str
    safe_tools: frozenset[str] = field(default_factory=frozenset)

    @property
    def prefix(self) -> str:
        return self.id.replace("-", "_")


@dataclass(frozen=True)
class ManifestTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    group: str  # "read" or "write": what the product says the tool does; only used to word the permission, never to trust it


@dataclass(frozen=True)
class Manifest:
    tools: tuple[ManifestTool, ...]
    listing: Mapping[str, Any]


def parse_manifest(document: object) -> Manifest:
    """The tools (and optional listing text) of a manifest document; anything malformed is an error, not a guess."""
    if not isinstance(document, dict) or not isinstance(document.get("tools"), list):
        raise ValueError("a tools manifest is an object with a list of tools")
    tools: list[ManifestTool] = []
    for entry in document["tools"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("every tool in a manifest has a name")
        schema = entry.get("input_schema", {"type": "object", "properties": {}})
        if not isinstance(schema, dict):
            raise ValueError(f"tool {entry['name']}: input_schema must be an object")
        tools.append(
            ManifestTool(
                entry["name"],
                str(entry.get("description", "")),
                schema,
                "read" if entry.get("group") == "read" else "write",
            )
        )
    listing = document.get("listing")
    return Manifest(tuple(tools), listing if isinstance(listing, dict) else {})


async def fetch_manifest(
    endpoint: ProductEndpoint, *, transport: httpx.AsyncBaseTransport | None = None
) -> Manifest:
    async with httpx.AsyncClient(transport=transport, timeout=5.0, follow_redirects=False) as http:
        reply = await http.get(endpoint.manifest_url, headers={"Accept": "application/json"})
    reply.raise_for_status()
    if len(reply.content) > MAX_MANIFEST_BYTES:
        raise ValueError("tools manifest is too large")
    return parse_manifest(reply.json())


class ProductTool(MCPTool):
    """One tool of a product. A call acts for the person of the run; a run without a person has no access."""

    def __init__(
        self,
        endpoint: ProductEndpoint,
        tool: ManifestTool,
        broker: CredentialBroker,
        *,
        client_factory: Callable[[], MCPClient] = MCPClient,
    ) -> None:
        risk = ToolRisk.SAFE if tool.name in endpoint.safe_tools else ToolRisk.HIGH
        super().__init__(
            client=None,  # type: ignore[arg-type]  # a connection is made per call, with that call's credential
            name=f"{endpoint.prefix}__{tool.name}"[:64],
            description=tool.description,
            input_schema=tool.input_schema,
            risk=risk,
            remote_name=tool.name,
        )
        self.endpoint, self.group, self.broker, self.client_factory = (
            endpoint,
            tool.group,
            broker,
            client_factory,
        )

    async def execute(self, *, ctx: object = None, **kwargs: object) -> ToolExecutionResult:  # type: ignore[override]
        call_id = str(kwargs.pop("call_id", ""))
        user = scope_of(ctx).user
        auth: Auth | None = getattr(ctx, "auth", None)
        if user is None or auth is None:
            return self._failed(call_id, f"{self.endpoint.id} needs a signed-in person; this run has none.")
        client = self.client_factory()
        try:
            token = await self.broker.credential(
                user, auth, audience=self.endpoint.audience, scopes=frozenset(), ttl_s=120
            )
            await client.connect_http(self.endpoint.mcp_url, headers={"Authorization": f"Bearer {token}"})
            return self.to_result(call_id, await client.call_tool(self.remote_name, kwargs))
        except Exception as error:  # noqa: BLE001 - the model is told, the run goes on
            return self._failed(call_id, f"Tool execution failed: {error}")
        finally:
            await client.disconnect()

    @staticmethod
    def _failed(call_id: str, text: str) -> ToolExecutionResult:
        return ToolExecutionResult(call_id=call_id, content=[TextBlock(text=text)], is_error=True)


def build_tools(
    endpoint: ProductEndpoint,
    manifest: Manifest,
    broker: CredentialBroker,
    *,
    client_factory: Callable[[], MCPClient] = MCPClient,
) -> list[ProductTool]:
    return [ProductTool(endpoint, t, broker, client_factory=client_factory) for t in manifest.tools]
