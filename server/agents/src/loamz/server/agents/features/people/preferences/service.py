from __future__ import annotations

from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.preferences.model import Preferences
from loamz.server.agents.features.people.preferences.store import PreferencesStore


class PreferencesService:
    """The public API of this feature: other features (chat reads `instructions_for`) use this, never the store."""

    def __init__(self, store: PreferencesStore) -> None:
        self._store = store

    async def get(self, caller: Caller) -> Preferences:
        return await self._store.get(caller.tenant_id, caller.user_id) or Preferences()

    async def zone(self, caller: Caller) -> str:
        """The person's time zone (an IANA name), or UTC when they have not chosen one. Times are stored as UTC moments; this is the zone to
        note beside them and to read them in."""
        return (await self.get(caller)).timezone or "UTC"

    async def agents_paused(self, caller: Caller) -> bool:
        return (await self.get(caller)).agents_paused

    async def set_agents_paused(self, caller: Caller, paused: bool) -> Preferences:
        """Switches all of this person's agents off or on. Their schedules keep their place and run again once it is off."""
        updated = (await self.get(caller)).model_copy(update={"agents_paused": paused})
        await self._store.put(caller.tenant_id, caller.user_id, updated)
        return updated

    async def replace(self, caller: Caller, preferences: Preferences) -> Preferences:
        # Saving the other choices never switches agents on or off: only `set_agents_paused` does.
        kept = preferences.model_copy(update={"agents_paused": await self.agents_paused(caller)})
        await self._store.put(caller.tenant_id, caller.user_id, kept)
        return kept

    async def instructions_for(self, caller: Caller) -> str:
        return (await self.get(caller)).instructions()

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def preferences(container: Container) -> PreferencesService:
    """Provider: how the container builds this feature's service."""
    return PreferencesService(PreferencesStore(container.database))
