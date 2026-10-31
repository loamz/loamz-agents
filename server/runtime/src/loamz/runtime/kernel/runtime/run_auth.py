"""The authority each live run was given by the host that submitted it.

It is held in memory only, for as long as the run is live: a credential is never written to the journal, so a run resumed
by another process after a restart has none until its host gives it one, and acts with nothing rather than with a guess.
"""

from __future__ import annotations

from loamz.runtime.kernel.types.auth import Auth


class RunAuths:
    """In-memory map from run id to the authority it acts with."""

    def __init__(self) -> None:
        self._by_run: dict[str, Auth] = {}

    def put(self, run_id: str, auth: Auth | None) -> None:
        if auth is not None:
            self._by_run[run_id] = auth

    def get(self, run_id: str) -> Auth | None:
        return self._by_run.get(run_id)

    def forget(self, run_id: str) -> None:
        self._by_run.pop(run_id, None)


__all__ = ["RunAuths"]
