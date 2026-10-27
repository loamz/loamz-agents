from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.toolkit.mcp.tool import MCPTool


@pytest.mark.asyncio
async def test_mcp_tool_wrapping_and_execution():
    # Mock MCPClient
    client = Mock()
    client.is_connected = True

    # Mock native MCP SDK response
    mock_mcp_item = Mock()
    mock_mcp_item.type = "text"
    mock_mcp_item.text = "hello from mcp"

    mock_mcp_response = Mock()
    mock_mcp_response.content = [mock_mcp_item]
    mock_mcp_response.isError = False

    client.call_tool = AsyncMock(return_value=mock_mcp_response)

    schema = {
        "type": "object",
        "properties": {"arg": {"type": "string"}},
        "required": ["arg"],
    }

    tool = MCPTool(
        client=client,
        name="test_mcp_tool",
        description="mcp tool description",
        input_schema=schema,
    )

    assert tool.name == "test_mcp_tool"
    assert tool.description == "mcp tool description"
    assert tool.input_schema == schema

    # Test execute
    res = await tool.execute(arg="value")
    assert res.is_error is False
    assert len(res.content) == 1
    assert isinstance(res.content[0], TextBlock)
    assert res.content[0].text == "hello from mcp"
    client.call_tool.assert_called_once_with("test_mcp_tool", {"arg": "value"})


def test_a_servers_structured_content_reaches_the_result_but_not_the_model_text():
    tool = MCPTool(
        client=Mock(is_connected=True), name="t", description="d", input_schema={"type": "object"}
    )
    item = Mock(type="text", text="the plan, in words")
    reply = Mock(content=[item], isError=False, structuredContent={"card": {"type": "card"}})
    result = tool.to_result("c1", reply)
    assert result.structured_content == {"card": {"type": "card"}}
    assert result.text == "the plan, in words"
    # a server without structured content (here a bare mock) leaves it empty
    assert tool.to_result("c2", Mock(content=[item], isError=False)).structured_content == {}
