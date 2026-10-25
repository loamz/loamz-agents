import asyncio

from loamz.engine.agents.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container


class PeopleService:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def search(self, caller: Caller, query: str) -> dict:
        rows = await asyncio.to_thread(self._c.directory.search_people, caller.tenant_id, query)
        return {
            "items": [
                {
                    "user_id": r["id"],
                    "name": " ".join(filter(None, [r.get("firstName"), r.get("lastName")]))
                    or r.get("username", r["id"]),
                    "email": r.get("email"),
                    "avatar": None,
                }
                for r in rows
            ]
        }

    async def invite(self, caller: Caller, email: str) -> None:
        await asyncio.to_thread(self._c.directory.invite, caller.tenant_id, email)


def people(container: Container) -> PeopleService:
    return PeopleService(container)
