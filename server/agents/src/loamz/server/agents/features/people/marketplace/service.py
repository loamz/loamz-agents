from __future__ import annotations

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.marketplace.catalog import CATALOG, CATEGORIES
from loamz.server.agents.features.people.marketplace.model import (
    CUSTOM_PREFIX,
    Catalog,
    Install,
    InstallBody,
    InstallList,
    InstallPatch,
    Listing,
)
from loamz.server.agents.features.people.marketplace.shipped import shipped_instructions
from loamz.server.agents.features.people.marketplace.store import InstallStore

SKILLS_INTRO = (
    "\n\nThe person has these skills. When a task matches one, call use_skill with its name to load the full instructions, "
    "then follow them:\n"
)


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

    def bundled(self, plugin: Listing) -> list[Listing]:
        """What a plugin bundles, in the order it lists them."""
        return [self.listing(i) for i in plugin.includes if any(c.id == i for c in CATALOG)]

    async def installed(self, caller: Caller) -> InstallList:
        return InstallList(items=await self._store.list(caller.tenant_id, caller.user_id))

    async def install(
        self, caller: Caller, item_id: str, body: InstallBody, listing: Listing | None = None
    ) -> Install:
        """Add an item. `listing` is the person's own connector, which is not in the catalog."""
        item = listing or self.listing(item_id)
        if item.needs:
            raise InvalidRequestError(
                f"{item.name} needs a connected account first, and connecting is not available yet."
            )
        # Reading may start as allowed; writing and deleting never do unless the person says so in the consent sheet.
        chosen = {p.group: body.permissions.get(p.group, p.default) for p in item.permissions}
        record = Install(id=item.id, permissions=chosen)
        await self._store.put(caller.tenant_id, caller.user_id, record)
        if item.kind == "plugin":
            # A plugin adds what it bundles, each with the safe starting choices. Apps wait for their sign-in, so they are not added here.
            have = {i.id for i in await self._store.list(caller.tenant_id, caller.user_id)}
            for inner in self.bundled(item):
                if inner.kind != "connector" and inner.id not in have:
                    start = Install(
                        id=inner.id, permissions={p.group: p.default for p in inner.permissions}
                    )
                    await self._store.put(caller.tenant_id, caller.user_id, start)
        return record

    async def _skills_on(self, caller: Caller, only: set[str] | None) -> list[Listing]:
        if only is not None:
            # An agent was given these by name: that is enough, whether or not the person switched them on for themselves.
            return [i for i in CATALOG if i.kind == "skill" and i.id in only]
        on = {i.id for i in (await self._store.list(caller.tenant_id, caller.user_id)) if i.enabled}
        return [i for i in CATALOG if i.kind == "skill" and i.id in on]

    async def skill_lines(self, caller: Caller, only: set[str] | None = None) -> list[str]:
        """One line per shipped skill this person switched on: its name and description. With `only` (an agent's own abilities), just those."""
        return [f"- {i.name}: {i.description}" for i in await self._skills_on(caller, only)]

    async def skills_text(self, caller: Caller, only: set[str] | None = None) -> str:
        """What the assistant is told about the skills this person switched on (nothing when none)."""
        lines = await self.skill_lines(caller, only)
        return SKILLS_INTRO + "\n".join(lines) if lines else ""

    async def skill_instructions(
        self, caller: Caller, name: str, only: set[str] | None = None
    ) -> str | None:
        """The full instructions of one of the shipped skills this person switched on, by its name; None when there is no such skill."""
        wanted = name.strip().lower()
        for skill in await self._skills_on(caller, only):
            if wanted in {skill.name.lower(), skill.id}:
                return shipped_instructions(skill.id)
        return None

    async def tools_on(
        self, caller: Caller, only: set[str] | None = None
    ) -> dict[str, dict[str, str]]:
        """The tools this person switched on (not blocked entirely), with the choice for each group. With `only`, just those."""
        out: dict[str, dict[str, str]] = {}
        kinds = {i.id: i.kind for i in CATALOG}
        for record in await self._store.list(caller.tenant_id, caller.user_id):
            if (
                (only is None or record.id in only)
                and record.enabled
                and kinds.get(record.id) == "tool"
                and any(c != "block" for c in record.permissions.values())
            ):
                out[record.id] = dict(record.permissions)
        return out

    async def tool_choices(
        self, caller: Caller, only: set[str] | None = None
    ) -> dict[str, dict[str, str]]:
        """The tools to build and what the person allows each: for an agent, those it was given (the person's own choices where they
        added one, the safe starting choices otherwise); for the plain assistant, those switched on."""
        if only is None:
            return await self.tools_on(caller)
        have = {i.id: i for i in await self._store.list(caller.tenant_id, caller.user_id)}
        out: dict[str, dict[str, str]] = {}
        for item in CATALOG:
            if item.kind != "tool" or item.id not in only:
                continue
            record = have.get(item.id)
            if record is not None and not record.enabled:
                continue
            out[item.id] = (
                dict(record.permissions)
                if record
                else {p.group: p.default for p in item.permissions}
            )
        return out

    async def change(self, caller: Caller, item_id: str, body: InstallPatch) -> Install:
        """Switch an added item on or off, or change what it may do, without adding it again."""
        current = {i.id: i for i in await self._store.list(caller.tenant_id, caller.user_id)}.get(
            item_id
        )
        if current is None:
            raise NotFoundError("That item has not been added.")
        if body.enabled is not None:
            current.enabled = body.enabled
        if body.permissions is not None:
            allowed = (
                {"write"}
                if item_id.startswith(CUSTOM_PREFIX)
                else {p.group for p in self.listing(item_id).permissions}
            )
            current.permissions = {g: c for g, c in body.permissions.items() if g in allowed} | {
                g: c for g, c in current.permissions.items() if g not in body.permissions
            }
        await self._store.put(caller.tenant_id, caller.user_id, current)
        return current

    async def remove(self, caller: Caller, item_id: str) -> None:
        await self._store.remove(caller.tenant_id, caller.user_id, item_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def marketplace(container: Container) -> MarketplaceService:
    """Provider: how the container builds this feature's service."""
    return MarketplaceService(InstallStore(container.database))
