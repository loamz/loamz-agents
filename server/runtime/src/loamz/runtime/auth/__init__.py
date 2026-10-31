"""How a call is authorized, separate from who is asking.

``UserCtx`` says who; ``Auth`` says how this call proves it:

- ``AsUser(token)``: the person's own access token from the application's sign-in. Whatever the call reaches, it reaches
  as that person, with the agent recorded as the actor.
- ``AsServicePrincipal()``: the deployment's own identity, for public or shared data with no person behind the call.

A ``CredentialBroker`` turns the two into a short-lived credential for one audience. It runs on the host, per request: a
sandbox never holds a credential.
"""

from loamz.runtime.auth.brokers import OidcTokenExchange, StaticBroker
from loamz.runtime.auth.types import AsServicePrincipal, AsUser, Auth, CredentialBroker

__all__ = [
    "AsServicePrincipal",
    "AsUser",
    "Auth",
    "CredentialBroker",
    "OidcTokenExchange",
    "StaticBroker",
]
