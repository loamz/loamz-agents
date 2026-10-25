"""Data tools: an agent finds, understands and queries governed data as the person it is working for.

    tools = data_tools(connection, minter)       # data_search, data_describe, data_sql

Every call asks the data service with a short-lived token for the run's tenant, person and teams, minted here on the
server, so the service decides what is visible: a sales analyst's agent sees sales tables, a marketing analyst's sees
marketing's, and the model can neither widen nor see that identity. The work happens in the data engine; only a bounded
answer comes back, and a result that would be large is cut off with a note that tells the model to aggregate.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

from loamz.core.exceptions import HttpResponseError
from loamz.engine.agents.data._connection import DataServiceConnection
from loamz.engine.agents.data.tokens import DataTokenMinter
from loamz.engine.agents.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.engine.agents.kernel.types.content import MediaBlock, TextBlock
from loamz.engine.agents.kernel.types.run import scope_of

MAX_ROWS = 200
MAX_CELL = 120
MAX_CHARS = 24_000
DATA_SCOPES = frozenset({"data:read"})


def cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def table(names: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


class DataTool:
    risk = ToolRisk.SAFE  # reads only, and only what the person's teams may read
    idempotent = True

    def __init__(self, connection: DataServiceConnection, minter: DataTokenMinter) -> None:
        self.connection, self.minter = connection, minter

    async def execute(self, *, ctx: object = None, **kwargs: Any) -> ToolExecutionResult:
        call_id = str(kwargs.pop("call_id", ""))
        scope = scope_of(ctx)
        if not scope.tenant_id or not scope.user_id:
            return self._reply(call_id, "Data needs a signed-in person; this run has none.", True)
        token = self.minter.mint(
            scope.tenant_id,
            scope.user_id,
            agent_id=scope.agent_id or None,
            thread_id=scope.thread_id or None,
            scopes=DATA_SCOPES,
            groups=scope.groups,
        )
        try:
            with self.connection.bearer(token):
                text = await self.run(**kwargs)
        except HttpResponseError as error:
            # The service's own explanation is what lets the model correct itself (a missing column, a table it may not read).
            return self._reply(call_id, error.detail or str(error), True)
        except (TypeError, ValueError) as error:
            return self._reply(call_id, f"Bad arguments: {error}", True)
        if isinstance(text, list):
            return ToolExecutionResult(call_id=call_id, content=text, is_error=False)
        return self._reply(call_id, text, False)

    @staticmethod
    def _reply(call_id: str, text: str, is_error: bool) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=call_id, content=[TextBlock(text=text[:MAX_CHARS])], is_error=is_error
        )

    async def run(self, **kwargs: Any) -> str | list[Any]:
        raise NotImplementedError


class DataSearch(DataTool):
    name = "data_search"
    description = (
        "Find tables and views you may read by a word in their name, comment or columns. Leave `text` empty to list "
        "what is available. Start here when you do not know what data exists."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "A word to look for."}},
        "additionalProperties": False,
    }

    async def run(self, text: str = "", **_: Any) -> str:
        found = await self.connection.client.search(text, limit=25)
        if not found:
            return "Nothing you may read matches. Try another word, or an empty search to list everything."
        return json.dumps(found, default=str)


class DataDescribe(DataTool):
    name = "data_describe"
    description = "Columns, types, row count and a few sample rows of `catalog.schema.table_or_view`. Look before you query."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Full name, like main.nyc.yellow_trips."}
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    async def run(self, name: str, **_: Any) -> str:
        info = await self.connection.client.describe(name, sample=0)
        sample = await self.connection.client.sql.rows(f"SELECT * FROM {name} LIMIT 5", max_rows=5)
        return (
            json.dumps(info, default=str) + "\n\nSample rows:\n" + table(sample.names, sample.rows)
        )


class DataQuery(DataTool):
    name = "data_sql"
    description = (
        "Run one read-only SQL SELECT (DuckDB dialect) on the data engine and get the answer back as a table. The engine "
        "does the work on all the rows, so filter and aggregate in SQL (WHERE, GROUP BY, ORDER BY, LIMIT) and bring back "
        f"only the answer: results stop at {MAX_ROWS} rows. Use full table names (catalog.schema.table)."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "A single SELECT statement."}},
        "required": ["query"],
        "additionalProperties": False,
    }

    async def run(self, query: str, **_: Any) -> str:
        result = await self.connection.client.sql.rows(query, max_rows=MAX_ROWS)
        if not result.rows:
            return "0 rows. This does not show that something is zero or absent: check the filter and the table's range."
        out = table(result.names, result.rows)
        if result.truncated:
            out += f"\n\n(Stopped at {MAX_ROWS} rows. The engine has more: aggregate, filter or LIMIT to see the rest.)"
        return out


MAX_POINTS = 2000
BRAND = ("#7c3aed", "#f95716", "#0ea5a4", "#64748b", "#d946ef", "#eab308")


def render_chart(
    kind: str,
    names: list[str],
    rows: list[list[Any]],
    x: str,
    ys: list[str],
    series: str | None,
    title: str,
) -> bytes:
    """A PNG of a query result. Only the plotted values are ever here: the engine already did the aggregation."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    at = {n: i for i, n in enumerate(names)}
    for column in [x, *ys, *([series] if series else [])]:
        if column not in at:
            raise ValueError(
                f"the result has no column {column!r}; its columns are {', '.join(names)}"
            )

    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("nan")

    if series:
        groups: dict[str, list[list[Any]]] = {}
        for row in rows:
            groups.setdefault(str(row[at[series]]), []).append(row)
        lines = [(name, part, ys[0]) for name, part in groups.items()]
    else:
        lines = [(y, rows, y) for y in ys]
    categorical = kind == "bar" or not all(isinstance(r[at[x]], (int, float)) for r in rows)
    labels = [str(r[at[x]]) for r in lines[0][1]]

    figure = Figure(figsize=(9, 4.8), dpi=150, facecolor="white")
    FigureCanvasAgg(figure)
    ax = figure.add_subplot(111)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#cbd5e1")
    ax.grid(axis="y", color="#e2e8f0", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors="#475569", labelsize=9)

    n = len(lines)
    for i, (name, part, column) in enumerate(lines):
        values = [number(r[at[column]]) for r in part]
        color = BRAND[i % len(BRAND)]
        xs = list(range(len(values))) if categorical else [number(r[at[x]]) for r in part]
        if kind == "bar":
            width = 0.8 / n
            ax.bar(
                [p + (i - (n - 1) / 2) * width for p in xs],
                values,
                width=width * 0.92,
                color=color,
                label=name,
            )
        elif kind == "scatter":
            ax.scatter(xs, values, s=14, color=color, alpha=0.75, label=name)
        else:
            ax.plot(xs, values, color=color, linewidth=2.2, marker="o", markersize=4, label=name)
    if categorical:
        step = max(1, len(labels) // 14)
        long = len(labels) > 6
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(
            labels[::step], rotation=30 if long else 0, ha="right" if long else "center"
        )
    ax.set_xlabel(x, color="#475569", fontsize=10)
    ax.set_ylabel(", ".join(ys), color="#475569", fontsize=10)
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _: f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}")
    )
    if title:
        ax.set_title(title, loc="left", fontsize=13, fontweight="bold", color="#0f172a", pad=14)
    if n > 1:
        ax.legend(frameon=False, fontsize=9)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


class DataChart(DataTool):
    name = "data_chart"
    description = (
        "Draw a chart of a SQL result and show it to the person. The engine runs the query on all the rows, so aggregate in SQL "
        f"first (GROUP BY, ORDER BY) and chart only the answer: at most {MAX_POINTS} points. `x` and `y` are column names of the "
        "result. Use `series` to draw one line or bar group per value of another column. kind is bar, line or scatter."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single SELECT whose result is what to plot, in plot order.",
            },
            "kind": {"type": "string", "enum": ["bar", "line", "scatter"]},
            "x": {"type": "string", "description": "Column for the horizontal axis."},
            "y": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column(s) for the vertical axis.",
            },
            "series": {
                "type": "string",
                "description": "Optional column whose values become separate series (then give one y).",
            },
            "title": {"type": "string"},
        },
        "required": ["query", "kind", "x", "y"],
        "additionalProperties": False,
    }

    async def run(
        self,
        query: str,
        kind: str,
        x: str,
        y: list[str] | str,
        series: str | None = None,
        title: str = "",
        **_: Any,
    ) -> list[Any]:
        if kind not in {"bar", "line", "scatter"}:
            raise ValueError("kind is bar, line or scatter")
        ys = [y] if isinstance(y, str) else list(y)
        result = await self.connection.client.sql.rows(query, max_rows=MAX_POINTS)
        if not result.rows:
            raise ValueError("the query returned no rows, so there is nothing to draw")
        if result.truncated:
            raise ValueError(
                f"the result has more than {MAX_POINTS} rows: aggregate it in SQL, then chart that"
            )
        png = await asyncio.to_thread(
            render_chart, kind, result.names, result.rows, x, ys, series, title
        )
        return [
            TextBlock(
                text=f"Drew a {kind} chart of {len(result.rows)} rows ({x} against {', '.join(ys)}). It is shown to the person."
            ),
            MediaBlock.image(data=png, media_type="image/png"),
        ]


def data_tools(connection: DataServiceConnection, minter: DataTokenMinter) -> list[DataTool]:
    return [
        DataSearch(connection, minter),
        DataDescribe(connection, minter),
        DataQuery(connection, minter),
        DataChart(connection, minter),
    ]
