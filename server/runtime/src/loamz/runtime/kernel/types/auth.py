"""How a call proves who it acts for. Never persisted: a credential lives only as long as the host process that was given it."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AsUser:
    """Act as the person: their access token, from the application's sign-in. Never printed."""

    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AsServicePrincipal:
    """Act as the deployment itself (its service account), for data no single person owns."""


type Auth = AsUser | AsServicePrincipal

__all__ = ["AsServicePrincipal", "AsUser", "Auth"]
