"""How a run gets governed data without a network and without a credential: the contract between a sandbox and a data source.

A host supplies a ``DataSource``. For each run it grants one socket (a ``DataMount``) and a little non-secret context. The
sandbox holds no credential: the host side of the socket does, and what the code may read is decided by the data service from
that identity, never by anything the model says. Anyone can write a ``DataSource`` for their own data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.run import RunScope


@dataclass(frozen=True, slots=True)
class DataMount:
    """Governed-data access for one run, with no network and no credential.

    ``socket_dir`` is a host directory holding this run's gateway socket (``api.sock``); it is shown read-only at
    ``/run/loamz-data``. The sandbox holds no token: the gateway behind the socket owns the run's identity, adds it to
    each request and forwards a short allow-list of read requests to the (remote) data service. What the code may read
    is decided there from that identity, never by anything the code or the model sends.
    """

    socket_dir: str

    def __post_init__(self) -> None:
        # ``:`` separates the two halves of a bind mount, so a path holding one could name a destination of its own.
        if not self.socket_dir.startswith("/") or ":" in self.socket_dir:
            raise ValueError("DataMount needs an absolute socket_dir without ':'")


@dataclass(frozen=True, slots=True)
class DataGrant:
    """One run's access: the socket it is shown, and non-secret variables its code needs (a workspace id, never a token)."""

    mount: DataMount
    env: tuple[tuple[str, str], ...] = ()


@runtime_checkable
class DataSource(Protocol):
    """Issues each run its own data access, or none."""

    guidance: str
    """What the model is told about the data it can reach: how to use it from code. Written by the source, not the tool."""

    async def grant(self, run: RunScope, auth: Auth | None, *, timeout_s: int) -> DataGrant | None:
        """Access valid for about ``timeout_s`` seconds for ``run``, or None when this run may have none."""
        ...

    async def release(self, grant: DataGrant) -> None:
        """End the run's access: its socket goes away and its identity can no longer be used."""
        ...

    async def stop(self) -> None: ...


__all__ = ["DataGrant", "DataMount", "DataSource"]
