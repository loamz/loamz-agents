"""`GatewayDataAccess`: gives each code-interpreter run its own gateway socket and data identity.

    access = GatewayDataAccess(gateway, minter, Path("/run/loamz/data"))
    tool = CodeInterpreterTool(runtime, data=access)

For each run it opens a `Channel` that speaks for the run's tenant and user, and returns a `DataMount` that shows only that
socket to the sandbox. The identity is minted on the host for each request, so the sandbox never holds a token, and it ends
with the run: `release` closes the socket. What the run moved is logged when it ends.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from loamz.runtime.data import DataGateway, DataTokenMinter
from loamz.runtime.data.relay import DEFAULT_BUDGET_BYTES, Channel
from loamz.runtime.data.tokens import MAX_TOKEN_SECONDS
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.toolkit.code.runtimes.base import DataMount

logger = logging.getLogger(__name__)

SANDBOX_SCOPES = frozenset({"data:read"})


class GatewayDataAccess:
    def __init__(
        self,
        gateway: DataGateway,
        minter: DataTokenMinter,
        root: Path,
        *,
        scopes: frozenset[str] = SANDBOX_SCOPES,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        socket_owner: int | None = None,
    ) -> None:
        self.gateway, self.minter, self.root = gateway, minter, root
        self.socket_owner = (
            socket_owner  # the id the sandbox runs as; None when it runs as this process's own
        )
        self.scopes, self.budget_bytes = scopes, budget_bytes
        self.runs: dict[str, tuple[str, Channel]] = {}

    async def grant(self, scope: RunScope, *, timeout_s: int) -> DataMount | None:
        if not scope.tenant_id:
            return None  # data is always someone's: no tenant, no access
        run_id = uuid.uuid4().hex[:12]
        tenant, user = scope.tenant_id, scope.user_id
        ttl = min(timeout_s + 30, MAX_TOKEN_SECONDS)

        def token() -> str:
            return self.minter.mint(
                tenant,
                user,
                agent_id=scope.agent_id or None,
                run_id=run_id,
                thread_id=scope.thread_id or None,
                scopes=self.scopes,
                groups=scope.groups,
                ttl_seconds=ttl,
            )

        channel = await self.gateway.open(
            self.root / run_id, token, budget_bytes=self.budget_bytes, owner=self.socket_owner
        )
        directory = str(channel.directory)
        self.runs[directory] = (run_id, channel)
        return DataMount(directory)

    async def release(self, mount: DataMount) -> None:
        run = self.runs.pop(mount.socket_dir, None)
        if run is None:
            return
        run_id, channel = run
        stats = channel.stats()
        await self.gateway.close(channel)
        shutil.rmtree(channel.directory, ignore_errors=True)
        logger.info("data run %s moved %s", run_id, stats)

    async def stop(self) -> None:
        for directory in list(self.runs):
            await self.release(DataMount(directory))
        await self.gateway.stop()
