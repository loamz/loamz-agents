"""Verify Keycloak access tokens (and tokens of explicitly trusted issuers) for every Loamz service."""

from loamz.server.agents.auth.errors import (
    AmbiguousOrganization,
    Forbidden,
    IdentityError,
    IdentityProviderUnavailable,
    InsufficientScope,
    InvalidToken,
    MissingToken,
    OrganizationError,
    UntrustedIssuer,
)
from loamz.server.agents.auth.identity import Identity
from loamz.server.agents.auth.jwks import ALLOWED_ALGORITHMS, JwksSource
from loamz.server.agents.auth.verifier import (
    PrimaryIssuer,
    TokenVerifier,
    TrustedIssuer,
    organization_of,
)

__all__ = [
    "ALLOWED_ALGORITHMS",
    "AmbiguousOrganization",
    "Forbidden",
    "Identity",
    "IdentityError",
    "IdentityProviderUnavailable",
    "InsufficientScope",
    "InvalidToken",
    "JwksSource",
    "MissingToken",
    "OrganizationError",
    "PrimaryIssuer",
    "TokenVerifier",
    "TrustedIssuer",
    "UntrustedIssuer",
    "organization_of",
]
