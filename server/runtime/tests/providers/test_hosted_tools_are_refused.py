"""A tool the agent's author asked for must not silently vanish: every vendor client either sends it or refuses it."""

from __future__ import annotations

from typing import Any

import pytest

from loamz.runtime.kernel.errors import UnsupportedToolError
from loamz.runtime.providers.llm._tools import function_specs


class _Local:
    name = "add"
    description = "adds"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}


class _Hosted:
    name = "web_search"
    description = "searches"
    provider_specs = {"openai": {"type": "web_search"}}


def test_function_tools_are_sent_and_a_hosted_tool_is_refused_by_name() -> None:
    assert function_specs([_Local()], client="x") == [
        {"name": "add", "description": "adds", "parameters": _Local.input_schema}
    ]
    assert function_specs(None, client="x") is None
    with pytest.raises(UnsupportedToolError, match="web_search"):
        function_specs([_Local(), _Hosted()], client="openai")


def test_every_vendor_client_uses_the_shared_rule() -> None:
    from loamz.runtime.providers.llm.anthropic import anthropic_client
    from loamz.runtime.providers.llm.gemini import gemini_client
    from loamz.runtime.providers.llm.openai import openai_client
    from loamz.runtime.providers.llm.openai_compatible import _tools_to_dicts

    class Options:
        tools = [_Hosted()]

    for fn in (
        openai_client._tools_from_options,
        anthropic_client._tools_from_options,
        gemini_client._tools_from_options,
    ):
        with pytest.raises(UnsupportedToolError):
            fn(Options())
    with pytest.raises(UnsupportedToolError):
        _tools_to_dicts([_Hosted()])
