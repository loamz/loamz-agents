from __future__ import annotations

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.marketplace.catalog import CATALOG, CATEGORIES
from loamz.server.agents.features.people.marketplace.model import (
    Catalog,
    Install,
    InstallBody,
    InstallList,
    Listing,
)
from loamz.server.agents.features.people.marketplace.store import InstallStore


class MarketplaceService:
    """The catalog (read-only) and each person's installs. Other features read `installed` to know what to offer."""

    def __init__(self, store: InstallStore) -> None:
        self._store = store

    def catalog(
        self,
        *,
        kind: str | None = None,
        category: str | None = None,
        q: str | None = None,
        sort: str = "recommended",
    ) -> Catalog:
        items = [
            i
            for i in CATALOG
            if (not kind or i.kind == kind) and (not category or i.category == category)
        ]
        if q and q.strip():
            needle = q.strip().lower()
            items = [
                i
                for i in items
                if needle in i.name.lower()
                or needle in i.tagline.lower()
                or needle in i.description.lower()
            ]
        order = {
            "newest": lambda i: i.added_at,
            "popular": lambda i: -i.installs,
            "name": lambda i: i.name.lower(),
        }.get(sort)
        if order:
            items = sorted(items, key=order, reverse=sort == "newest")
        return Catalog(items=items, categories=list(CATEGORIES))

    def listing(self, item_id: str) -> Listing:
        for i in CATALOG:
            if i.id == item_id:
                return i
        raise NotFoundError("That listing does not exist.")

    async def installed(self, caller: Caller) -> InstallList:
        return InstallList(items=await self._store.list(caller.tenant_id, caller.user_id))

    async def install(self, caller: Caller, item_id: str, body: InstallBody) -> Install:
        item = self.listing(item_id)
        if item.needs:
            raise InvalidRequestError(
                f"{item.name} needs a connected account first, and connecting is not available yet."
            )
        # Reading may start as allowed; writing and deleting never do unless the person says so in the consent sheet.
        chosen = {p.group: body.permissions.get(p.group, p.default) for p in item.permissions}
        record = Install(id=item.id, permissions=chosen)
        await self._store.put(caller.tenant_id, caller.user_id, record)
        return record

    async def remove(self, caller: Caller, item_id: str) -> None:
        await self._store.remove(caller.tenant_id, caller.user_id, item_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def marketplace(container: Container) -> MarketplaceService:
    """Provider: how the container builds this feature's service."""
    return MarketplaceService(InstallStore(container.database))
