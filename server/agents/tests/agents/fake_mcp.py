"""A small hosted MCP server with its own OAuth sign-in (dynamic registration, PKCE, refresh), for connector tests."""

from __future__ import annotations

import secrets
import socket
import threading
import time

import uvicorn
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class _Provider:
    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.issued = 0

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or ["read"],
            expires_at=time.time() + 300,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, authorization_code: str):  # noqa: ANN001, ANN201
        return self.codes.get(authorization_code)

    def _mint(self, client_id: str, scopes: list[str]) -> OAuthToken:
        self.issued += 1
        access, refresh = (
            f"at-{self.issued}-{secrets.token_hex(4)}",
            f"rt-{self.issued}-{secrets.token_hex(4)}",
        )
        self.access[access] = AccessToken(
            token=access, client_id=client_id, scopes=scopes, expires_at=int(time.time()) + 3600
        )
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code):  # noqa: ANN001, ANN201
        del self.codes[authorization_code.code]
        return self._mint(client.client_id or "", authorization_code.scopes)

    async def load_refresh_token(self, client, refresh_token: str):  # noqa: ANN001, ANN201
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):  # noqa: ANN001, ANN201
        del self.refresh[refresh_token.token]
        return self._mint(client.client_id or "", scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.access.get(token)

    async def revoke_token(self, token) -> None:  # noqa: ANN001
        self.access.pop(getattr(token, "token", ""), None)


class FakeConnector:
    """Start with `with FakeConnector() as server:`; `server.url` is the MCP address."""

    def __init__(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        base = f"http://127.0.0.1:{self.port}"
        self.url = f"{base}/mcp"
        self.provider = _Provider()
        self.calls: list[tuple[str, dict[str, object]]] = []
        mcp = FastMCP(
            "fake",
            auth_server_provider=self.provider,
            auth=AuthSettings(
                issuer_url=base,  # type: ignore[arg-type]
                resource_server_url=self.url,  # type: ignore[arg-type]
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=["read", "write"], default_scopes=["read"]
                ),
                required_scopes=["read"],
            ),
            host="127.0.0.1",
            port=self.port,
        )

        @mcp.tool()
        def search(query: str) -> str:
            """Look things up."""
            self.calls.append(("search", {"query": query}))
            return f"found {query}"

        @mcp.tool()
        def create_item(title: str) -> str:
            """Make a new item."""
            self.calls.append(("create_item", {"title": title}))
            return f"created {title}"

        self._server = uvicorn.Server(
            uvicorn.Config(
                mcp.streamable_http_app(), host="127.0.0.1", port=self.port, log_level="warning"
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> FakeConnector:
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("the fake connector did not start")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(5)
