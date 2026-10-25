"""Data tools: an agent queries as the person it works for, sees only a bounded answer, and is told why when refused."""

from __future__ import annotations

import httpx
import jwt

from loamz.engine.agents.data import DataServiceConnection, DataTokenMinter, data_tools
from loamz.engine.agents.kernel.types.run import RunScope

SCOPE = RunScope(tenant_id="acme", user_id="priya", thread_id="t1", groups=("sales",))


class Ctx:
    scope = SCOPE


def build(tmp_path, handler):
    connection = DataServiceConnection("http://data.test", transport=httpx.MockTransport(handler))
    minter = DataTokenMinter(tmp_path / "k.pem", issuer="agents", audience="data")
    return {t.name: t for t in data_tools(connection, minter)}


def answer(columns, rows, truncated=False):
    return httpx.Response(
        200,
        json={
            "columns": [{"name": c, "type": "VARCHAR"} for c in columns],
            "rows": rows,
            "truncated": truncated,
        },
    )


async def test_a_query_runs_as_the_person_and_their_teams_and_comes_back_as_a_table(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(["borough", "avg_fare"], [["Manhattan", 18.4], ["Queens", 41.2]])

    tools = build(tmp_path, handler)
    result = await tools["data_sql"].execute(ctx=Ctx(), query="select 1", call_id="c1")

    assert not result.is_error and result.call_id == "c1"
    text = result.content[0].text
    assert "| borough | avg_fare |" in text and "| Queens | 41.2 |" in text
    claims = jwt.decode(
        seen[0].headers["authorization"].removeprefix("Bearer "),
        options={"verify_signature": False},
    )
    assert (claims["tenant_id"], claims["user_id"], claims["groups"]) == (
        "acme",
        "priya",
        ["sales"],
    )
    assert claims["scope"] == "data:read"


async def test_an_answer_that_is_cut_off_tells_the_model_to_aggregate(tmp_path):
    tools = build(tmp_path, lambda r: answer(["n"], [[1]], truncated=True))
    text = (await tools["data_sql"].execute(ctx=Ctx(), query="select n from t")).content[0].text
    assert "aggregate" in text


async def test_an_empty_answer_is_not_presented_as_a_zero(tmp_path):
    tools = build(tmp_path, lambda r: answer(["n"], []))
    text = (await tools["data_sql"].execute(ctx=Ctx(), query="select n from t")).content[0].text
    assert text.startswith("0 rows") and "does not show" in text


async def test_a_refusal_from_the_data_service_reaches_the_model_as_its_own_words(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "type": "https://loamz.com/problems/forbidden",
                "status": 403,
                "detail": "You may not read main.hr.salaries",
            },
            headers={"content-type": "application/problem+json"},
        )

    tools = build(tmp_path, handler)
    result = await tools["data_sql"].execute(ctx=Ctx(), query="select * from main.hr.salaries")
    assert result.is_error and "may not read main.hr.salaries" in result.content[0].text


async def test_a_run_without_a_signed_in_person_gets_no_data_and_makes_no_request(tmp_path):
    calls: list[int] = []
    tools = build(tmp_path, lambda r: calls.append(1) or answer(["n"], [[1]]))

    class Anonymous:
        scope = RunScope()

    result = await tools["data_sql"].execute(ctx=Anonymous(), query="select 1")
    assert result.is_error and not calls


async def test_a_chart_is_drawn_from_the_engines_answer_and_shown_as_an_image(tmp_path):
    import pytest

    pytest.importorskip("matplotlib")
    tools = build(
        tmp_path,
        lambda r: answer(
            ["month", "avg_fare"], [["2025-01", 17.1], ["2025-02", 16.8], ["2025-03", 17.8]]
        ),
    )
    result = await tools["data_chart"].execute(
        ctx=Ctx(),
        query="select 1",
        kind="bar",
        x="month",
        y=["avg_fare"],
        title="Average fare",
        call_id="c1",
    )
    assert not result.is_error
    image = next(b for b in result.content if getattr(b, "type", "") == "image")
    assert image.data.startswith(b"\x89PNG") and image.media_type == "image/png"


async def test_a_chart_of_a_missing_column_says_which_columns_exist(tmp_path):
    import pytest

    pytest.importorskip("matplotlib")
    tools = build(tmp_path, lambda r: answer(["month", "avg_fare"], [["2025-01", 17.1]]))
    result = await tools["data_chart"].execute(
        ctx=Ctx(), query="select 1", kind="line", x="month", y=["fare"]
    )
    assert result.is_error and "month, avg_fare" in result.content[0].text
