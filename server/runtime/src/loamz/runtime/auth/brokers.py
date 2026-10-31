"""Credential brokers: a fixed one for tests and local use, and OIDC token exchange (RFC 8693) for real deployments."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from loamz.engine.errors import ForbiddenError
from loamz.runtime.auth.types import AsServicePrincipal, AsUser, Auth
from loamz.runtime.kernel.types.user import UserCtx

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
# A cached credential is not handed out in its last seconds: it could expire on the way.
_LEEWAY_S = 30


class StaticBroker:
    """Always the same credential. For tests, and for a service that already holds the one token it needs."""

    def __init__(self, token: str) -> None:
        self._token = token

    def __repr__(self) -> str:
        return "StaticBroker()"

    async def credential(
        self,
        user: UserCtx | None,
        auth: Auth,
        *,
        audience: str,
        scopes: frozenset[str],
        ttl_s: int,
    ) -> str:
        return self._token


@dataclass(slots=True)
class _Cached:
    token: str
    expires_at: float


@dataclass(slots=True)
class OidcTokenExchange:
    """Exchanges at an OpenID Connect provider.

    ``AsUser`` sends the person's token as the subject of an RFC 8693 exchange, authenticating as the agent's own client:
    the credential is the person's, for one audience, with the agent as the party acting. ``AsServicePrincipal`` uses the
    client credentials grant. Endpoints come from the provider's discovery document. A credential is reused until just
    before it expires, but only for the same person, audience and scopes.
    """

    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    timeout_s: float = 10.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _endpoint: str | None = field(default=None, init=False, repr=False)
    _cache: dict[str, _Cached] = field(default_factory=dict, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def credential(
        self,
        user: UserCtx | None,
        auth: Auth,
        *,
        audience: str,
        scopes: frozenset[str],
        ttl_s: int,
    ) -> str:
        if isinstance(auth, AsUser) and user is None:
            raise ForbiddenError("acting as a user needs the user: none was given")
        key = self._key(user, auth, audience, scopes)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at - self.clock() > _LEEWAY_S:
                return cached.token
            token, lifetime = await self._request(auth, audience, scopes)
            # Never keep it longer than the caller asked, nor than the provider allows.
            self._cache[key] = _Cached(token, self.clock() + min(lifetime, ttl_s))
            return token

    @staticmethod
    def _key(user: UserCtx | None, auth: Auth, audience: str, scopes: frozenset[str]) -> str:
        who = (
            f"user:{user.tenant}/{user.subject}:{hashlib.sha256(auth.token.encode()).hexdigest()}"
            if isinstance(auth, AsUser) and user is not None
            else "service"
        )
        return "|".join((who, audience, ",".join(sorted(scopes))))

    async def _token_endpoint(self, client: httpx.AsyncClient) -> str:
        if self._endpoint is None:
            reply = await client.get(f"{self.issuer.rstrip('/')}/.well-known/openid-configuration")
            reply.raise_for_status()
            self._endpoint = str(reply.json()["token_endpoint"])
        return self._endpoint

    async def _request(
        self, auth: Auth, audience: str, scopes: frozenset[str]
    ) -> tuple[str, float]:
        form: dict[str, str] = {"audience": audience}
        if scopes:
            form["scope"] = " ".join(sorted(scopes))
        match auth:
            case AsUser(token=subject):
                form |= {
                    "grant_type": TOKEN_EXCHANGE,
                    "subject_token": subject,
                    "subject_token_type": ACCESS_TOKEN,
                    "requested_token_type": ACCESS_TOKEN,
                }
            case AsServicePrincipal():
                form["grant_type"] = "client_credentials"
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s) as client:
            try:
                endpoint = await self._token_endpoint(client)
                reply = await client.post(
                    endpoint, data=form, auth=httpx.BasicAuth(self.client_id, self.client_secret)
                )
            except httpx.HTTPError as error:
                raise ForbiddenError(
                    f"the identity provider could not be reached: {error}"
                ) from error
        if reply.status_code >= 400:
            raise ForbiddenError("the identity provider refused to issue this credential")
        body = reply.json()
        return str(body["access_token"]), float(body.get("expires_in", 60))


__all__ = ["OidcTokenExchange", "StaticBroker"]
