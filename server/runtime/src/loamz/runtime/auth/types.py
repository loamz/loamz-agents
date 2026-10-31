"""The contracts: how a call is authorized, and the broker that turns that into a credential."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from loamz.runtime.kernel.types.user import UserCtx


@dataclass(frozen=True, slots=True)
class AsUser:
    """Act as the person: their access token, from the application's sign-in. Never printed."""

    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AsServicePrincipal:
    """Act as the deployment itself (its service account), for data no single person owns."""


type Auth = AsUser | AsServicePrincipal


@runtime_checkable
class CredentialBroker(Protocol):
    """Turns who is asking and how they prove it into a credential for one audience.

    ``audience`` names the service the credential is for; ``scopes`` what it may do there; ``ttl_s`` the longest it should
    live. A broker refuses (``ForbiddenError``) rather than returning something weaker than what was asked.
    """

    async def credential(
        self,
        user: UserCtx | None,
        auth: Auth,
        *,
        audience: str,
        scopes: frozenset[str],
        ttl_s: int,
    ) -> str: ...


__all__ = ["AsServicePrincipal", "AsUser", "Auth", "CredentialBroker"]
