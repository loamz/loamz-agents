"""The tools a vendor client advertises to its model, shared by every client.

A vendor client sends plain function tools. A provider-hosted or provider-defined tool is a different thing on each
vendor's API, and none is implemented yet: handing one to a client is refused by name, not silently left out. A tool the
agent's author asked for must not just disappear.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from loamz.runtime.kernel.errors import UnsupportedToolError
from loamz.runtime.kernel.tools import Tool, is_hosted_tool, is_provider_defined_tool


def function_tools(tools: Sequence[Any] | None, *, client: str) -> list[Tool]:
    """The function tools in ``tools``; raises if any is hosted or provider-defined, which ``client`` cannot send."""
    if not tools:
        return []
    for t in tools:
        if is_hosted_tool(t) or is_provider_defined_tool(t):
            raise UnsupportedToolError(
                f"{getattr(t, 'name', t)!r} is a provider-hosted tool, which the {client} client does not send yet"
            )
    return [cast(Tool, t) for t in tools]


def function_specs(tools: Sequence[Any] | None, *, client: str) -> list[dict[str, Any]] | None:
    """``{"name", "description", "parameters"}`` for each function tool, or None when there are none."""
    chosen = function_tools(tools, client=client)
    if not chosen:
        return None
    return [
        {"name": t.name, "description": t.description, "parameters": t.input_schema} for t in chosen
    ]
