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


# --- a conversation narrowed to some catalogs -------------------------------------------------------------------------------


def two_catalogs(statements: list[httpx.Request]):  # type: ignore[no-untyped-def]
    """A workspace with catalogs `nyc` and `shop`, one schema and one table each; every statement it is asked to run is kept."""
    tree = {
        "": [
            {"id": "c1", "kind": "catalog", "name": "nyc"},
            {"id": "c2", "kind": "catalog", "name": "shop"},
        ],
        "c1": [{"id": "s1", "kind": "schema", "name": "main"}],
        "c2": [{"id": "s2", "kind": "schema", "name": "main"}],
        "s1": [{"id": "t1", "kind": "table", "name": "yellow_trips"}],
        "s2": [{"id": "t2", "kind": "table", "name": "sales"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"{BASE}/securables":
            return httpx.Response(
                200, json={"items": tree[request.url.params.get("parent_id", "")]}
            )
        statements.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r",
                "state": "succeeded",
                "result": {"columns": [], "rows": [], "truncated": False},
            },
        )

    return handler


NARROWED = Ctx(
    RunScope.from_user(
        UserCtx.of(
            "ana", "acme", attributes={"loamz.data/workspace": WS, "loamz.data/catalogs": "shop"}
        )
    ),
    AsUser("ana-token"),
)


async def test_a_narrowed_conversation_lists_only_the_chosen_catalogs() -> None:
    kept: list[httpx.Request] = []
    everything = await tools(two_catalogs(kept))["data_tables"].execute(ctx=RUN, call_id="c")
    narrowed = await tools(two_catalogs(kept))["data_tables"].execute(ctx=NARROWED, call_id="c")
    assert json.loads(everything.content[0].text) == ["nyc.main.yellow_trips", "shop.main.sales"]
    assert json.loads(narrowed.content[0].text) == ["shop.main.sales"]


async def test_a_table_outside_the_chosen_catalogs_is_refused_by_describe_and_by_sql_before_anything_is_asked() -> (
    None
):
    kept: list[httpx.Request] = []
    described = await tools(two_catalogs(kept))["data_describe"].execute(
        ctx=NARROWED, call_id="c", table="nyc.main.yellow_trips"
    )
    queried = await tools(two_catalogs(kept))["data_sql"].execute(
        ctx=NARROWED, call_id="c", sql='SELECT count(*) FROM "nyc"."main"."yellow_trips"'
    )
    assert described.is_error and "outside the catalogs chosen" in described.content[0].text
    assert queried.is_error and "catalog nyc is outside" in queried.content[0].text
    assert kept == []  # no statement was sent


async def test_sql_inside_the_chosen_catalogs_runs_and_an_unnarrowed_conversation_is_not_restricted() -> (
    None
):
    kept: list[httpx.Request] = []
    inside = await tools(two_catalogs(kept))["data_sql"].execute(
        ctx=NARROWED, call_id="c", sql="SELECT * FROM shop.main.sales"
    )
    everywhere = await tools(two_catalogs(kept))["data_sql"].execute(
        ctx=RUN, call_id="c", sql="SELECT * FROM nyc.main.yellow_trips"
    )
    assert not inside.is_error and not everywhere.is_error
    assert len(kept) == 2
