"""The agent's data tools act for the person, with the authority the host gave the run, and say why when refused."""

from __future__ import annotations

import json

import httpx

from loamz.runtime.auth import AsUser, StaticBroker
from loamz.runtime.data import DataConnection, data_tools
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx

WS = "9cce7e59-e1c0-4f8b-ab20-ff93a79161ca"
BASE = f"/api/v1/workspaces/{WS}"


class Ctx:
    def __init__(self, scope: RunScope, auth: Auth | None) -> None:
        self.scope, self.auth = scope, auth


def tools(handler):  # type: ignore[no-untyped-def]
    connection = DataConnection(
        "http://data.test", StaticBroker("exchanged"), transport=httpx.MockTransport(handler)
    )
    return {t.name: t for t in data_tools(connection)}


ANA = UserCtx.of("ana", "acme", attributes={"loamz.data/workspace": WS})
RUN = Ctx(RunScope.from_user(ANA), AsUser("ana-token"))


async def test_sql_runs_as_the_person_in_their_workspace_and_returns_a_bounded_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        run = {
            "id": "r",
            "state": "succeeded",
            "result": {
                "columns": [{"name": "n", "type": "BIGINT"}],
                "rows": [[3]],
                "truncated": False,
            },
        }
        return httpx.Response(200, json=run)

    reply = await tools(handler)["data_sql"].execute(
        ctx=RUN, call_id="c1", sql="SELECT count(*) AS n FROM shop.main.sales"
    )
    assert not reply.is_error and json.loads(reply.content[0].text)["rows"] == [[3]]
    assert seen[0].url.path == f"{BASE}/sql/statements"
    assert seen[0].headers["authorization"] == "Bearer exchanged"
    assert json.loads(seen[0].content)["limit"] == 200


async def test_what_the_service_refuses_is_told_to_the_model_in_the_services_words() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"title": "Not Found", "detail": "table shop.main.secret was not found"}
        )

    reply = await tools(handler)["data_sql"].execute(
        ctx=RUN, call_id="c", sql="SELECT * FROM shop.main.secret"
    )
    assert reply.is_error and "was not found" in reply.content[0].text


async def test_a_run_with_no_authority_or_no_workspace_gets_no_data_and_calls_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be asked of the data service")

    t = tools(handler)["data_tables"]
    no_auth = await t.execute(ctx=Ctx(RunScope.from_user(ANA), None), call_id="c")
    no_workspace = await t.execute(
        ctx=Ctx(RunScope.from_user(UserCtx.of("ana", "acme")), AsUser("x")), call_id="c"
    )
    nobody = await t.execute(ctx=None, call_id="c")
    assert all(r.is_error for r in (no_auth, no_workspace, nobody))
