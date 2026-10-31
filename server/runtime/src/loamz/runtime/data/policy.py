"""What a sandbox may ask the data service for: held on the host, per run, never by anything the sandbox says."""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_ID = r"[A-Za-z0-9_.-]{1,128}"
_WORKSPACE = re.compile(r"^[0-9a-fA-F-]{36}$")


@runtime_checkable
class RequestPolicy(Protocol):
    """Decides whether one request from a sandbox may be forwarded."""

    def allows(self, method: str, path: str) -> bool: ...


class AllowAll:
    """Forwards everything. For tests of the relay itself; never for a sandbox that runs a person's code."""

    def allows(self, method: str, path: str) -> bool:
        return True


class WorkspaceReadPolicy:
    """Read-only requests to Loamz Data for one workspace: run a statement, follow it, and look at what may be read.

    The workspace is the run's own, fixed when the run was granted its socket. Storage credentials never pass through
    a sandbox, so the Iceberg catalog (which hands them out) is not on the list.
    """

    def __init__(self, workspace: str) -> None:
        if not _WORKSPACE.match(workspace):
            raise ValueError("a workspace id is a UUID")
        base = re.escape(f"/api/v1/workspaces/{workspace}")
        self._allowed: tuple[tuple[str, re.Pattern[str]], ...] = (
            ("POST", re.compile(rf"^{base}/sql/statements$")),
            ("GET", re.compile(rf"^{base}/runs/{_ID}$")),
            ("POST", re.compile(rf"^{base}/runs/{_ID}/cancel$")),
            ("GET", re.compile(rf"^{base}/securables(/{_ID})?$")),
            ("GET", re.compile(rf"^{base}/tables/{_ID}$")),
        )

    def allows(self, method: str, path: str) -> bool:
        if {".", ".."} & set(path.split("/")):
            return False
        return any(m == method and rx.match(path) for m, rx in self._allowed)


__all__ = ["AllowAll", "RequestPolicy", "WorkspaceReadPolicy"]
