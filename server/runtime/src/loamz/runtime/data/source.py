"""``LoamzData``: a code-interpreter data source backed by a Loamz Data server.

For each run it opens one socket whose far end is on this host. The host side asks the credential broker for a credential
as the person (or as the service principal) for the data audience, adds it to each request, and forwards only read
requests for the run's own workspace. The sandbox sees a socket and a workspace id; it never sees a credential.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.data.policy import WorkspaceReadPolicy
from loamz.runtime.data.relay import DEFAULT_BUDGET_BYTES, Channel, DataGateway
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.data import DataGrant, DataMount
from loamz.runtime.kernel.types.run import RunScope

logger = logging.getLogger(__name__)

WORKSPACE_ATTRIBUTE = "loamz.data/workspace"
# The catalogs a conversation was narrowed to, comma separated; absent means every catalog the person may read.
CATALOGS_ATTRIBUTE = "loamz.data/catalogs"
WORKSPACE_ENV = "LOAMZ_DATA_WORKSPACE"
MAX_TOKEN_SECONDS = 330

GUIDANCE = (
    "Governed warehouse data is available without network access: `import loamz.data as sd`, then "
    "`sd.sql('SELECT ... FROM catalog.schema.table')` returns a Polars DataFrame, and `sd.tables()` lists the tables you "
    "may read. Name every table in full as catalog.schema.table; a table you may not read is simply not there. Do the "
    "filtering and aggregation in SQL (GROUP BY, WHERE, LIMIT) and bring back only the answer; large results are refused. "
    "To report, save charts with matplotlib (`plt.savefig('chart.png')`) and spreadsheets with "
    "`df.write_excel('report.xlsx')` into the working directory, then link them."
)


class LoamzData:
    """A ``DataSource`` for a Loamz Data server.

    The workspace comes from the person's ``UserCtx`` attribute ``loamz.data/workspace``; a service-principal run, which has no
    person, is given a fixed ``workspace`` instead. A run with neither has no data.
    """

    guidance = GUIDANCE

    def __init__(
        self,
        gateway: DataGateway,
        broker: CredentialBroker,
        root: Path,
        *,
        audience: str = "loamz-data",
        # Loamz Data decides what a person may read from who they are; the credential names the audience, not a scope.
        scopes: frozenset[str] = frozenset(),
        workspace: str | None = None,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        socket_owner: int | None = None,
    ) -> None:
        self._gateway, self._broker, self._root = gateway, broker, root
        self._audience, self._scopes, self._workspace = audience, scopes, workspace
        self._budget_bytes, self._socket_owner = budget_bytes, socket_owner
        self._runs: dict[str, tuple[str, Channel]] = {}

    async def grant(self, run: RunScope, auth: Auth | None, *, timeout_s: int) -> DataGrant | None:
        if auth is None:
            return None  # nothing proves who is asking: no access
        user = run.user
        if user is None and self._workspace is None:
            return None  # data is always someone's: no organization and user, no access
        workspace = (user.attribute(WORKSPACE_ATTRIBUTE) if user else None) or self._workspace
        if not workspace:
            return None
        policy = WorkspaceReadPolicy(workspace)
        ttl = min(timeout_s + 30, MAX_TOKEN_SECONDS)
        broker, audience, scopes = self._broker, self._audience, self._scopes

        async def token() -> str:
            return await broker.credential(user, auth, audience=audience, scopes=scopes, ttl_s=ttl)

        run_id = uuid.uuid4().hex[:12]
        channel = await self._gateway.open(
            self._root / run_id,
            token,
            policy=policy,
            budget_bytes=self._budget_bytes,
            owner=self._socket_owner,
        )
        directory = str(channel.directory)
        self._runs[directory] = (run_id, channel)
        return DataGrant(DataMount(directory), ((WORKSPACE_ENV, workspace),))

    async def release(self, grant: DataGrant) -> None:
        run = self._runs.pop(grant.mount.socket_dir, None)
        if run is None:
            return
        run_id, channel = run
        stats = channel.stats()
        await self._gateway.close(channel)
        shutil.rmtree(channel.directory, ignore_errors=True)
        logger.info("data run %s moved %s", run_id, stats)

    async def stop(self) -> None:
        for directory in list(self._runs):
            await self.release(DataGrant(DataMount(directory)))
        await self._gateway.stop()


__all__ = ["CATALOGS_ATTRIBUTE", "GUIDANCE", "WORKSPACE_ATTRIBUTE", "LoamzData"]
