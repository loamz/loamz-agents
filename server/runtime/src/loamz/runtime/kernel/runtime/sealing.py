"""Sealing: chat content is stored as an opaque value the database cannot read, and opened only by the process that holds the key.

    sealed = sealer.seal("group/42", "7:text", "see you at nine")     # what the database holds
    sealer.open("group/42", "7:text", sealed)                          # "see you at nine"

`scope` says whose content it is (a channel, a user) and `where` which field of it; both are authenticated, so a sealed value copied
into another row or another chat does not open. `open` hands back a value that was never sealed unchanged: content written before
sealing was switched on stays readable, and is sealed the next time it is written.

This protects content at rest from someone with the database, a backup or a replica. It is not end-to-end encryption: the process that
holds the key reads the content (see docs/security/e2ee-threat-model.md).
"""

from __future__ import annotations

from typing import Protocol


class SealError(ValueError):
    """A sealed value that cannot be opened: the wrong key, a copied row, or damage."""


class Sealer(Protocol):
    def seal(self, scope: str, where: str, value: str) -> str: ...

    def open(self, scope: str, where: str, value: str) -> str: ...


class PlainSealer:
    """Stores values as they are: tests and a deployment that has not configured keys."""

    def seal(self, scope: str, where: str, value: str) -> str:
        return value

    def open(self, scope: str, where: str, value: str) -> str:
        return value
