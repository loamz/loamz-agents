"""loamz.runtime.stores.workspace — Per-thread workspaces: branching, snapshots, content-addressed files."""

from __future__ import annotations

from loamz.runtime.kernel.ports.workspace import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)
from loamz.runtime.stores.workspace.branching import (
    delete_branch_workspace,
    fork_branch,
    resolve_workspace_snapshot_id,
)
from loamz.runtime.stores.workspace.cas import BlobCAS
from loamz.runtime.stores.workspace.scope import WorkspaceScope, workspace_scope
from loamz.runtime.stores.workspace.snapshots import checkout_branch, commit_turn
from loamz.runtime.stores.workspace.tables import Workspaces

__all__ = [
    "BlobCAS",
    "ContentRef",
    "Workspaces",
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceScope",
    "WorkspaceSnapshot",
    "WorkspaceStore",
    "checkout_branch",
    "commit_turn",
    "delete_branch_workspace",
    "fork_branch",
    "resolve_workspace_snapshot_id",
    "workspace_scope",
]
