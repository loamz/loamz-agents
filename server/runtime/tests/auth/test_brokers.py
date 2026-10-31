"""CredentialBroker: how a call's authority becomes a credential for one audience, per call, never held by the sandbox."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from loamz.engine.errors import ForbiddenError
from loamz.runtime.auth import (
    AsServicePrincipal,
    AsUser,
    CredentialBroker,
    OidcTokenExchange,
    StaticBroker,
)
from loamz.runtime.kernel.types.user import UserCtx

ANA = UserCtx.of("ana", "acme")


class _Issuer:
    """A token endpoint: records what it was asked and answers like an OIDC provider."""

    def __init__(self, *, deny: bool = False) -> None:
        self.requests: list[dict[str, str]] = []
        self.deny = deny
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "https://idp.test/token"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append({**form, "_auth": request.headers.get("authorization", "")})
        if self.deny:
            return httpx.Response(403, json={"error": "access_denied"})
        self.issued += 1
        return httpx.Response(200, json={"access_token": f"tok-{self.issued}", "expires_in": 300})


def _broker(issuer: _Issuer) -> OidcTokenExchange:
    return OidcTokenExchange(
        issuer="https://idp.test/realms/x",
        client_id="loamz-agents",
        client_secret="s3cret",
        transport=httpx.MockTransport(issuer.handler),
    )


def test_every_broker_satisfies_the_contract() -> None:
    assert isinstance(StaticBroker("t"), CredentialBroker)
    assert isinstance(_broker(_Issuer()), CredentialBroker)


async def test_as_user_exchanges_the_persons_token_for_one_audience_with_the_agent_as_the_client() -> (
    None
):
    issuer = _Issuer()
    credential = await _broker(issuer).credential(
        ANA,
        AsUser("ana-access-token"),
        audience="loamz-data",
        scopes=frozenset({"data:read"}),
        ttl_s=120,
    )
    assert credential == "tok-1"
    (sent,) = issuer.requests
    assert sent["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert sent["subject_token"] == "ana-access-token" and sent["audience"] == "loamz-data"
    assert sent["scope"] == "data:read"
    assert sent["_auth"].startswith("Basic "), (
        "the agent authenticates as its own client: it is the actor"
    )


async def test_as_service_principal_uses_the_agents_own_identity_and_needs_no_person() -> None:
    issuer = _Issuer()
    credential = await _broker(issuer).credential(
        None, AsServicePrincipal(), audience="loamz-data", scopes=frozenset(), ttl_s=60
    )
    assert credential == "tok-1"
    assert issuer.requests[0]["grant_type"] == "client_credentials"
    assert "subject_token" not in issuer.requests[0]


async def test_a_user_call_without_a_person_is_refused_and_a_provider_refusal_is_forbidden() -> (
    None
):
    with pytest.raises(ForbiddenError):
        await _broker(_Issuer()).credential(
            None, AsUser("t"), audience="a", scopes=frozenset(), ttl_s=60
        )
    with pytest.raises(ForbiddenError):
        await _broker(_Issuer(deny=True)).credential(
            ANA, AsUser("t"), audience="a", scopes=frozenset(), ttl_s=60
        )


async def test_credentials_are_reused_until_near_expiry_but_never_across_people_or_audiences() -> (
    None
):
    issuer = _Issuer()
    broker = _broker(issuer)
    args = {"scopes": frozenset({"data:read"}), "ttl_s": 120}
    first = await broker.credential(ANA, AsUser("a"), audience="loamz-data", **args)
    again = await broker.credential(ANA, AsUser("a"), audience="loamz-data", **args)
    other_audience = await broker.credential(ANA, AsUser("a"), audience="other", **args)
    bob = await broker.credential(
        UserCtx.of("bob", "acme"), AsUser("b"), audience="loamz-data", **args
    )
    assert first == again and len({first, other_audience, bob}) == 3 and issuer.issued == 3


async def test_the_static_broker_hands_back_its_token_and_the_secret_is_not_printed() -> None:
    broker = StaticBroker("fixed")
    assert (
        await broker.credential(ANA, AsUser("x"), audience="a", scopes=frozenset(), ttl_s=60)
        == "fixed"
    )
    assert "s3cret" not in repr(_broker(_Issuer())) and "ana-access-token" not in repr(
        AsUser("ana-access-token")
    )
    assert json.dumps({"ok": True})
