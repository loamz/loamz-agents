"""The family's token conventions (loamz-deploy `spec/`): every case in the shared vector file must give the outcome it names.

The file is a copy of `spec/vectors/token/loamz-agents.json`; regenerate and copy it when the spec changes.
This verifier does not expose the client (`azp`) and has no `groups` claim of its own to compare beyond what Identity holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from loamz.server.agents.auth import (
    AmbiguousOrganization,
    IdentityError,
    InvalidToken,
    JwksSource,
    OrganizationError,
    PrimaryIssuer,
    TokenVerifier,
    UntrustedIssuer,
)

DOC: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "vectors" / "token-loamz-agents.json").read_text()
)
# A token from another issuer is refused as UntrustedIssuer here (still a 401), which is how this verifier says "invalid".
ERRORS: dict[str, type[IdentityError] | tuple[type[IdentityError], ...]] = {
    "invalid": (InvalidToken, UntrustedIssuer),
    "unknown_key": InvalidToken,
    "ambiguous_organization": AmbiguousOrganization,
    "no_organization": OrganizationError,
}


def verifier() -> TokenVerifier:
    keys = JwksSource(
        "https://keys.example.test/jwks",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=DOC["jwks"]))
        ),
    )
    primary = PrimaryIssuer(issuer=DOC["issuer"], audience=DOC["audience"], leeway=0)
    return TokenVerifier(primary, primary_keys=keys)


@pytest.mark.parametrize("case", DOC["cases"], ids=[c["name"] for c in DOC["cases"]])
def test_the_verifier_gives_the_outcome_the_vectors_name(case: dict[str, Any]) -> None:
    expect = case.get("expect_by_audience", {}).get(DOC["audience"], case["expect"])
    if not expect["ok"]:
        with pytest.raises(ERRORS[expect["error"]]):
            verifier().verify_token(case["token"])
        return
    who = verifier().verify_token(case["token"])
    assert who.subject == expect["subject"]
    assert who.kind == ("service" if expect["kind"] == "service" else "user")
    assert who.organization == (expect["orgs"][0] if expect["orgs"] else None)
    assert who.groups == frozenset(g.lstrip("/") for g in expect["groups"])
    assert who.actor == expect["actor"]
