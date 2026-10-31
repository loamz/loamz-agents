from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.toolkit.mcp.servers import exposed
from loamz.runtime.toolkit.mcp.tool import MCPTool
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.connections.guard import PublicOnly, check_url
from loamz.server.agents.features.people.connections.store import ConnectionStore
from loamz.server.agents.features.people.marketplace.model import (
    CUSTOM_PREFIX,
    InstallBody,
    Listing,
    Permission,
)
from loamz.server.agents.features.people.marketplace.service import MarketplaceService, marketplace

logger = logging.getLogger(__name__)

SIGN_IN_WAIT = 300.0  # how long a person has to finish signing in on the vendor's page
LISTING_TTL = 600.0  # how long a connector's tool list is reused
MAX_RESULT_CHARS = 20_000  # what the model reads from one tool call
MAX_PARAM_TEXT = 240  # the longest description of one parameter shown to the model
MAX_TOOL_TEXT = 600  # the longest description of one tool shown to the model
MAX_CUSTOM = 10  # connectors one person can add by address


# What a model provider accepts in a tool's parameters is a small part of JSON Schema (OpenAI answers 400 for the whole request when one
# tool uses anything else). A connector's own server validates real input, so the model is shown only this part.
_KEEP = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "description",
        "title",
        "default",
        "additionalProperties",
        "anyOf",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "multipleOf",
        "$ref",
        "$defs",
        "definitions",
    }
)
_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})


def model_safe(schema: object) -> object:
    """A copy of a tool's parameter schema reduced to the part every model provider accepts.

    `allOf` is merged into its parent, `oneOf` becomes `anyOf`, a malformed `type` is dropped, and every other keyword is removed.
    """
    if isinstance(schema, list):
        return [model_safe(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    node = dict(schema)
    for part in node.pop("allOf", None) or []:
        if isinstance(part, dict):
            merged = model_safe(part)
            if isinstance(merged, dict):
                for key, value in merged.items():
                    if key == "properties" and isinstance(value, dict):
                        node["properties"] = {**(node.get("properties") or {}), **value}
                    elif key == "required" and isinstance(value, list):
                        node["required"] = [*dict.fromkeys([*(node.get("required") or []), *value])]
                    else:
                        node.setdefault(key, value)
    if "oneOf" in node and "anyOf" not in node:
        node["anyOf"] = node.pop("oneOf")
    kind = node.get("type")
    if isinstance(kind, list):
        kind = [k for k in kind if isinstance(k, str) and k in _TYPES]
        if kind:
            node["type"] = kind if len(kind) > 1 else kind[0]
        else:
            node.pop("type")
    elif kind is not None and kind not in _TYPES:
        node.pop("type")
    out: dict[str, object] = {}
    for key, value in node.items():
        if key not in _KEEP:
            continue
        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            out[key] = {
                k: model_safe(v) for k, v in value.items()
            }  # property names are data, not keywords
        elif key in {"items", "additionalProperties", "anyOf"}:
            out[key] = model_safe(value)
        elif key == "description" and isinstance(value, str):
            out[key] = value[
                :MAX_PARAM_TEXT
            ]  # a long description is paid for in tokens on every message
        else:
            out[key] = value
    return out


class NeedsReconnect(Exception):  # noqa: N818 - reads as a state, not an error class
    """The saved sign-in no longer works and a person must connect again."""


@dataclass
class _Flow:
    caller: Caller
    item: str
    url: asyncio.Future[str]
    code: asyncio.Future[tuple[str, str | None]]
    task: asyncio.Task[None] | None = None


@dataclass
class _Cached:
    at: float
    tools: list[dict[str, object]] = field(default_factory=list)


class ConnectionsService:
    """Signs a person in to a connector's MCP server with the standard OAuth flow and keeps their tokens.

    Registration is automatic (Client ID Metadata Document, or dynamic registration for older servers), so adding a connector
    is a catalog entry. Tokens are the person's own: every call a tool makes uses the token of whoever the run speaks for.
    """

    def __init__(
        self, store: ConnectionStore, market: MarketplaceService, container: Container
    ) -> None:
        self._store, self._market, self._c = store, market, container
        self._flows: dict[str, _Flow] = {}
        self._cache: dict[tuple[str, str, str], _Cached] = {}

    # ── addresses ───────────────────────────────────────────────────────────
    @property
    def _base(self) -> str:
        return self._c.settings.http.public_url.rstrip("/")

    @property
    def redirect_uri(self) -> str:
        return f"{self._base}/api/v1/connections/callback"

    @property
    def client_id_url(self) -> str:
        return f"{self._base}/api/v1/connections/client.json"

    def client_metadata(self) -> dict[str, object]:
        """The document that is our client id (CIMD): servers that support it need no registration."""
        return {
            "client_id": self.client_id_url,
            "client_name": "Loamz",
            "client_uri": self._c.settings.http.frontend_url,
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def _metadata(self) -> OAuthClientMetadata:
        return OAuthClientMetadata(
            client_name="Loamz",
            redirect_uris=[self.redirect_uri],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )

    async def _listing(self, caller: Caller, item_id: str) -> Listing:
        if item_id.startswith(CUSTOM_PREFIX):
            found = {c.id: c for c in await self.custom_listings(caller)}.get(item_id)
            if found is None:
                raise NotFoundError("That connector does not exist.")
            return found
        listing = self._market.listing(item_id)
        if listing.kind != "connector" or not listing.mcp_url:
            raise InvalidRequestError(f"{listing.name} cannot be connected yet.")
        return listing

    # ── connectors added by address ─────────────────────────────────────────
    async def custom_listings(self, caller: Caller) -> list[Listing]:
        """The connectors this person added by address. Each is theirs alone and was not checked by Loamz."""
        out = []
        for row in await self._store.customs(caller.tenant_id, caller.user_id):
            host = urlparse(row["url"]).hostname or row["url"]
            out.append(
                Listing(
                    id=row["id"],
                    kind="connector",
                    name=row["name"],
                    tagline=f"Your own connector at {host}"[:80],
                    description=(
                        f"A server you added at {row['url']}. Loamz has not checked it: it receives what the assistant sends it, "
                        "and what it sends back is treated as untrusted text. Its tools ask before they run unless you allow them."
                    ),
                    icon="Plug",
                    category="Your connectors",
                    publisher="You",
                    trust="community",
                    permissions=[Permission(group="write", label="Use its tools", default="ask")],
                    data_seen_by=["Loamz", "OpenAI", host],
                    mcp_url=row["url"],
                    added_at=time.strftime("%Y-%m-%d"),
                )
            )
        return out

    async def add_custom(
        self, caller: Caller, name: str, url: str, token: str | None = None
    ) -> Listing:
        """Add a connector by the address of its MCP server. Only public https addresses; the optional access token is kept sealed."""
        name = " ".join(name.split())
        if not 1 <= len(name) <= 40:
            raise InvalidRequestError("Give it a name of up to 40 characters.")
        if len(await self._store.customs(caller.tenant_id, caller.user_id)) >= MAX_CUSTOM:
            raise InvalidRequestError(f"You can add up to {MAX_CUSTOM} connectors of your own.")
        clean = await check_url(url)
        item = f"{CUSTOM_PREFIX}{uuid4().hex[:10]}"
        secret = (token or "").strip() or None
        if secret and len(secret) > 4000:
            raise InvalidRequestError("That access token is too long.")
        await self._store.add_custom(caller.tenant_id, caller.user_id, item, name, clean, secret)
        return next(c for c in await self.custom_listings(caller) if c.id == item)

    async def _client(
        self, caller: Caller, listing: Listing, provider: OAuthClientProvider
    ) -> httpx.AsyncClient:
        """The web client for a connector. One a person added by address goes only to the public internet and, when they gave an
        access token, uses it instead of the sign-in."""
        if not listing.id.startswith(CUSTOM_PREFIX):
            return httpx.AsyncClient(auth=provider, timeout=30, follow_redirects=True)
        secret = await self._store.custom_secret(caller.tenant_id, caller.user_id, listing.id)
        return httpx.AsyncClient(
            auth=None if secret else provider,
            headers={"Authorization": f"Bearer {secret}"} if secret else None,
            transport=PublicOnly(),
            timeout=30,
            follow_redirects=True,
        )

    def _provider(
        self, caller: Caller, listing: Listing, redirect, callback
    ) -> OAuthClientProvider:  # noqa: ANN001
        return OAuthClientProvider(
            server_url=listing.mcp_url or "",
            client_metadata=self._metadata(),
            storage=self._store.storage(caller.tenant_id, caller.user_id, listing.id),
            redirect_handler=redirect,
            callback_handler=callback,
            timeout=SIGN_IN_WAIT,
            client_metadata_url=self.client_id_url
            if self.client_id_url.startswith("https://")
            else None,
        )

    # ── signing in ──────────────────────────────────────────────────────────
    async def start(self, caller: Caller, item_id: str) -> dict[str, str]:
        """Begin (or refresh) a connection. Returns the vendor's sign-in address, or `connected` when the saved sign-in still works."""
        listing = await self._listing(caller, item_id)
        loop = asyncio.get_running_loop()
        flow = _Flow(caller, item_id, loop.create_future(), loop.create_future())
        flow.task = loop.create_task(self._run(flow, listing), name=f"connect-{item_id}")
        done, _ = await asyncio.wait(
            {flow.url, flow.task}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if flow.url in done:
            return {"authorization_url": flow.url.result()}
        if flow.task in done:
            flow.task.result()  # raises when the server could not be reached
            return {"status": "connected"}
        flow.task.cancel()
        raise InvalidRequestError(f"{listing.name} did not answer. Try again in a moment.")

    async def _run(self, flow: _Flow, listing: Listing) -> None:
        caller = flow.caller

        async def redirect(url: str) -> None:
            state = (parse_qs(urlparse(url).query).get("state") or [""])[0]
            if not state:
                raise InvalidRequestError("The sign-in page did not give a state value.")
            self._flows[state] = flow
            if not flow.url.done():
                flow.url.set_result(url)

        async def callback() -> tuple[str, str | None]:
            return await asyncio.wait_for(flow.code, SIGN_IN_WAIT)

        provider = self._provider(caller, listing, redirect, callback)
        try:
            async with (
                await self._client(caller, listing, provider) as http,
                streamable_http_client(listing.mcp_url or "", http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
            self._cache[(caller.tenant_id, caller.user_id, listing.id)] = _Cached(
                time.monotonic(), [t.model_dump() for t in listed.tools]
            )
            await self._store.set_status(caller.tenant_id, caller.user_id, listing.id, "connected")
            with contextlib.suppress(
                Exception
            ):  # already added: the choices the person made are kept
                await self._market.install(caller, listing.id, InstallBody(), listing=listing)
        except BaseException as error:
            if not flow.url.done():
                flow.url.set_exception(
                    InvalidRequestError(f"Could not reach {listing.name}: {error}")
                )
            raise
        finally:
            for state, f in list(self._flows.items()):
                if f is flow:
                    del self._flows[state]

    async def finish(self, state: str, code: str | None, error: str | None) -> tuple[str, bool]:
        """The vendor sent the person back. Hands the code to the waiting sign-in and reports whether it worked."""
        flow = self._flows.pop(state, None)
        if flow is None:
            raise NotFoundError("This sign-in has expired. Start again from the Marketplace.")
        if error or not code:
            flow.code.set_exception(InvalidRequestError(error or "The sign-in was cancelled."))
        else:
            flow.code.set_result((code, state))
        assert flow.task is not None
        try:
            await asyncio.wait_for(asyncio.shield(flow.task), 30)
        except Exception:  # noqa: BLE001 - reported as not connected
            logger.warning("connector sign-in failed for %s", flow.item, exc_info=True)
            return flow.item, False
        return flow.item, True

    # ── status ──────────────────────────────────────────────────────────────
    async def status(self, caller: Caller) -> list[dict[str, object]]:
        return await self._store.list(caller.tenant_id, caller.user_id)

    async def disconnect(self, caller: Caller, item_id: str) -> None:
        self._cache.pop((caller.tenant_id, caller.user_id, item_id), None)
        await self._store.remove(caller.tenant_id, caller.user_id, item_id)
        await self._store.remove_custom(caller.tenant_id, caller.user_id, item_id)
        await self._market.remove(caller, item_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        for key in [
            k for k in self._cache if k[0] == tenant_id and (user_id is None or k[1] == user_id)
        ]:
            del self._cache[key]
        return await self._store.erase(tenant_id, user_id)

    # ── using a connection ──────────────────────────────────────────────────
    @asynccontextmanager
    async def session(self, caller: Caller, item_id: str) -> AsyncIterator[ClientSession]:
        """An MCP session signed in as this person. It never opens a sign-in page: a lost sign-in means "connect again"."""
        listing = await self._listing(caller, item_id)

        async def no_redirect(url: str) -> None:  # noqa: ARG001
            raise NeedsReconnect

        async def no_callback() -> tuple[str, str | None]:
            raise NeedsReconnect

        provider = self._provider(caller, listing, no_redirect, no_callback)
        try:
            async with (
                await self._client(caller, listing, provider) as http,
                streamable_http_client(listing.mcp_url or "", http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
        except* NeedsReconnect:
            await self._store.set_status(caller.tenant_id, caller.user_id, item_id, "attention")
            raise NeedsReconnect from None

    async def _tools_of(self, caller: Caller, item_id: str) -> list[dict[str, object]]:
        key = (caller.tenant_id, caller.user_id, item_id)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit.at < LISTING_TTL:
            return hit.tools
        async with self.session(caller, item_id) as s:
            listed = await asyncio.wait_for(s.list_tools(), 20)
        tools = [t.model_dump() for t in listed.tools]
        self._cache[key] = _Cached(time.monotonic(), tools)
        return tools

    async def list_tools(self, caller: Caller, item_id: str) -> list[dict[str, object]]:
        """What one connector offers, for the page where a person picks the tools an agent may use: each tool's name, what it says it does, the
        group we file it under (read, write, delete) and whether the server itself calls it read-only (a hint to pre-tick, never a reason to trust)."""
        listing = await self._listing(caller, item_id)
        if item_id not in {
            c["id"]
            for c in await self._store.list(caller.tenant_id, caller.user_id)
            if c["status"] == "connected"
        }:
            raise InvalidRequestError(f"Connect {listing.name} first to see its tools.")
        try:
            listed = await self._tools_of(caller, item_id)
        except NeedsReconnect as why:
            raise InvalidRequestError(f"{listing.name} needs to be signed in again.") from why
        except Exception as why:  # noqa: BLE001
            raise InvalidRequestError(
                f"{listing.name} did not answer. Try again in a moment."
            ) from why
        out: list[dict[str, object]] = []
        for tool in listed:
            name = str(tool["name"])
            if listing.tools and name not in listing.tools:
                continue
            note = tool.get("annotations") or {}
            out.append(
                {
                    "name": name,
                    "description": str(tool.get("description") or "")[:MAX_TOOL_TEXT],
                    "group": listing.tools.get(name, "write"),
                    "read_hint": bool(note.get("readOnlyHint"))
                    if isinstance(note, dict)
                    else False,
                }
            )
        return sorted(out, key=lambda t: (str(t["group"]) != "read", str(t["name"])))

    async def tools(
        self,
        caller: Caller,
        only: set[str] | None = None,
        access: dict[str, dict[str, str]] | None = None,
        *,
        asking: bool = True,
    ) -> list[MCPTool]:
        """The tools of every connector this person connected and switched on, shaped by their choices.

        Blocked groups are left out. A tool whose group is on `allow` and reads only runs without asking; anything else asks
        each time. When the catalog lists tools, only those are offered; otherwise every tool counts as writing, because what a server says
        about itself is never trusted. With `only` (an agent's own abilities), just those connectors.

        `access` is what the agent's owner ticked for it (`{connector: {tool: allow | ask}}`): a connector with an entry offers only those
        tools, and "ask" makes a tool ask even where the person's own choice would let it run. It can narrow what the person allowed, never widen it.
        With `asking=False` (a place where nobody can be asked, such as a group chat) only the tools that run without asking are offered.
        """
        out: list[MCPTool] = []
        connected = {
            c["id"]
            for c in await self._store.list(caller.tenant_id, caller.user_id)
            if c["status"] == "connected"
        }
        for install in (await self._market.installed(caller)).items:
            if (
                not install.enabled
                or install.id not in connected
                or (only is not None and install.id not in only)
            ):
                continue
            try:
                listing = await self._listing(caller, install.id)
                listed = await self._tools_of(caller, install.id)
            except NeedsReconnect:
                continue
            except Exception:  # noqa: BLE001 - one vendor being down must not stop the run
                logger.warning("connector %s unavailable", install.id, exc_info=True)
                continue
            ticked = access.get(install.id) if access and only is not None else None
            for tool in listed:
                remote = str(tool["name"])
                if listing.tools and remote not in listing.tools:
                    continue  # a curated entry offers only the tools we chose; a server cannot add its own
                if ticked is not None and remote not in ticked:
                    continue  # this agent's owner did not tick it
                group = listing.tools.get(remote, "write")
                choice = install.permissions.get(group, "ask")
                if choice == "block":
                    continue
                # A connector the person added themselves has no curated list, so it asks each time unless they allowed it.
                safe = (
                    choice == "allow"
                    and (group == "read" or install.id.startswith(CUSTOM_PREFIX))
                    and (ticked is None or ticked.get(remote) == "allow")
                )
                risk = ToolRisk.SAFE if safe else ToolRisk.HIGH
                if risk is not ToolRisk.SAFE and not asking:
                    continue
                out.append(
                    ConnectionTool(
                        self,
                        caller,
                        install.id,
                        remote,
                        exposed(install.id, remote),
                        str(tool.get("description") or "")[:MAX_TOOL_TEXT],
                        model_safe(tool.get("inputSchema") or {}),  # type: ignore[arg-type]
                        risk,  # type: ignore[arg-type]
                    )
                )
        return out


def shortened(result: ToolExecutionResult) -> ToolExecutionResult:
    """The result with any text longer than what the model should read cut short and marked. Blocks are immutable, so
    the cut ones are replaced."""
    content = [
        TextBlock(text=b.text[:MAX_RESULT_CHARS] + "\n[shortened]")
        if isinstance(b, TextBlock) and len(b.text) > MAX_RESULT_CHARS
        else b
        for b in result.content
    ]
    return result.model_copy(update={"content": content})


class ConnectionTool(MCPTool):
    """One tool of a connector, called with the signed-in person's own token."""

    idempotent = False

    def __init__(
        self,
        service: ConnectionsService,
        caller: Caller,
        item: str,
        remote: str,
        name: str,
        description: str,
        schema: dict[str, object],
        risk: ToolRisk,
    ) -> None:
        super().__init__(None, name, description, schema, risk=risk, remote_name=remote)  # type: ignore[arg-type]
        self._svc, self._caller, self._item = service, caller, item

    async def execute(self, *, ctx: object = None, **kwargs: object) -> ToolExecutionResult:  # type: ignore[override]
        call_id = str(kwargs.pop("call_id", ""))
        try:
            async with self._svc.session(self._caller, self._item) as s:
                result = await asyncio.wait_for(s.call_tool(self.remote_name, kwargs), 90)
            return shortened(self.to_result(call_id, result))
        except NeedsReconnect:
            message = "This connection needs to be signed in again. Ask the person to reconnect it in the Marketplace."
        except Exception as error:  # noqa: BLE001 - the model is told, the run goes on
            message = f"The connector failed: {error}"
        return ToolExecutionResult(
            call_id=call_id, content=[TextBlock(text=message)], is_error=True
        )


def connections(container: Container) -> ConnectionsService:
    """Provider: how the container builds this feature's service."""
    from loamz.server.agents.features.people.connections.store import ConnectionStore

    return ConnectionsService(
        ConnectionStore(container.database, container.sealer), container.get(marketplace), container
    )
