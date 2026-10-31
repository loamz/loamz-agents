"""Who is asking: the person (or service) a run acts for, as an application hands it over with each call.

``UserCtx`` carries only what any identity system has: a subject, the tenant (organization) it acts in, its groups, and
namespaced attributes that a connector reads (``"loamz.data/workspace"``). It comes from an authenticated session in the
application and is never taken from model output. It holds no credentials: a ``CredentialBroker`` turns it into one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

MAX_ATTRIBUTES = 32
MAX_FIELD = 256


def _clean(kind: str, value: str) -> str:
    if not value or len(value) > MAX_FIELD or any(ord(c) < 32 for c in value):
        raise ValueError(f"a user context {kind} must be 1 to {MAX_FIELD} printable characters")
    return value


@dataclass(frozen=True, slots=True)
class UserCtx:
    """The caller, validated once at the edge.

    ``attributes`` keys are namespaced (``"<connector>/<name>"``) so two connectors never collide, and values are short
    text: identifiers, never secrets.
    """

    subject: str
    tenant: str
    groups: tuple[str, ...] = ()
    attributes: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        _clean("subject", self.subject)
        _clean("tenant", self.tenant)
        for group in self.groups:
            _clean("group", group)
        if len(self.attributes) > MAX_ATTRIBUTES:
            raise ValueError(f"a user context holds at most {MAX_ATTRIBUTES} attributes")
        for key, value in self.attributes:
            _clean("attribute key", key)
            if "/" not in key:
                raise ValueError(f"attribute {key!r} must be namespaced like '<connector>/<name>'")
            _clean("attribute value", value)

    @classmethod
    def of(
        cls,
        subject: str,
        tenant: str,
        *,
        groups: tuple[str, ...] | list[str] = (),
        attributes: Mapping[str, str] | None = None,
    ) -> UserCtx:
        """Build one from plain values; groups are de-duplicated and attributes sorted, so equal contexts compare equal."""
        return cls(
            subject=subject,
            tenant=tenant,
            groups=tuple(sorted(set(groups))),
            attributes=tuple(sorted((attributes or {}).items())),
        )

    def attribute(self, key: str) -> str | None:
        """The value of a namespaced attribute, or None."""
        return dict(self.attributes).get(key)


__all__ = ["UserCtx"]
