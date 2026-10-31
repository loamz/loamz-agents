"""Tests for integrations/tools/chain/ — bridge, prelude, ToolChainTool."""

from __future__ import annotations

from typing import Any

import pytest

from loamz.runtime.kernel.runtime.tool_invoker import ToolInvoker
from loamz.runtime.kernel.tools import Toolbox, ToolCallRequest, ToolExecutionResult, ToolRisk
from loamz.runtime.kernel.types.content import TextBlock
from loamz.runtime.toolkit.chain.bridge import BridgeSession, ChainBridgeRegistry
from loamz.runtime.toolkit.chain.prelude import build_prelude

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class EchoTool:
    idempotent = True
    name = "echo"
    description = "Echoes the input."
    risk = ToolRisk.SAFE
    input_schema: dict[str, Any] = {}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text=kwargs.get("msg", "echoed"))])


class FakeInterpreter:
    """Records injected code; returns configurable results."""

    name = "code_interpreter"
    session_id = "default"

    def __init__(self, output: str = "", is_error: bool = False) -> None:
        self.last_code: str = ""
        self._output = output
        self._is_error = is_error

    async def execute(self, *, code: str, **kwargs: Any) -> ToolExecutionResult:
        self.last_code = code
        return ToolExecutionResult(
            content=[TextBlock(text=self._output)],
            is_error=self._is_error,
        )


def make_registry(*tools: Any) -> Toolbox:
    tb = Toolbox()
    for t in tools:
        tb.add(t)
    return tb


# ---------------------------------------------------------------------------
# Prelude generation
# ---------------------------------------------------------------------------


def test_prelude_contains_tool_names():
    prelude = build_prelude(
        tool_names=["echo", "query_db"],
        bridge_url="http://localhost:8001",
        chain_id="chain_abc",
        chain_token="tok123",
    )
    assert "echo" in prelude
    assert "query_db" in prelude


def test_prelude_contains_chain_id():
    prelude = build_prelude(
        tool_names=[],
        bridge_url="http://localhost:8001",
        chain_id="my_chain_id",
        chain_token="tok",
    )
    assert "my_chain_id" in prelude


def test_prelude_defines_tool_result_class():
    prelude = build_prelude(tool_names=["t"], bridge_url="http://x", chain_id="c", chain_token="k")
    assert "class ToolResult" in prelude


def test_prelude_defines_tools_namespace():
    prelude = build_prelude(tool_names=["t"], bridge_url="http://x", chain_id="c", chain_token="k")
    assert "tools = _ToolsNamespace()" in prelude


def test_prelude_defines_artifacts():
    prelude = build_prelude(tool_names=[], bridge_url="http://x", chain_id="c", chain_token="k")
    assert "artifacts = _Artifacts()" in prelude


# ---------------------------------------------------------------------------
# BridgeSession / ChainBridgeRegistry
# ---------------------------------------------------------------------------


async def test_bridge_session_dispatch():
    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as inv_session:
        session = BridgeSession(
            chain_id="c1",
            invoker=invoker,
            invoker_session=inv_session,
        )
        call = ToolCallRequest(name="echo", arguments={"msg": "hi"})
        result = await session.dispatch(call)
    assert result.status == "ok"
    assert result.text == "hi"


async def test_bridge_session_invalidated():
    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as inv_session:
        session = BridgeSession(
            chain_id="c2",
            invoker=invoker,
            invoker_session=inv_session,
        )
        session.invalidate()
        call = ToolCallRequest(name="echo", arguments={})
        result = await session.dispatch(call)
    assert result.status == "error"
    assert "closed" in result.text.lower()


def test_bridge_registry_register_deregister():
    registry = ChainBridgeRegistry()
    invoker = ToolInvoker(registry=make_registry())

    class FakeInvSession:
        _call_count = 0
        _trace = []  # type: ignore[var-annotated]
        _pinned_refs = []  # type: ignore[var-annotated]

    session = BridgeSession(
        chain_id="chain_x",
        invoker=invoker,
        invoker_session=FakeInvSession(),  # type: ignore[arg-type]
    )
    registry.register(session)
    assert registry.get("chain_x") is session

    registry.deregister("chain_x")
    assert registry.get("chain_x") is None
    assert not session._active


def test_bridge_registry_deregister_missing():
    registry = ChainBridgeRegistry()
    registry.deregister("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# ToolChainTool
# ---------------------------------------------------------------------------


async def test_tool_chain_tool_registers_and_deregisters_session():
    from loamz.runtime.toolkit.chain.bridge import ChainBridgeRegistry
    from loamz.runtime.toolkit.chain.tool import ToolChainTool

    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    bridge_reg = ChainBridgeRegistry()
    interpreter = FakeInterpreter(output="Chain output")

    tool = ToolChainTool(
        invoker=invoker,
        interpreter=interpreter,
        bridge_registry=bridge_reg,
        bridge_base_url="http://localhost:8001",
    )

    result = await tool.execute(code="result = tools.echo(msg='hello')\nreturn result.text")
    assert not result.is_error
    # No sessions should remain after execution
    assert len(bridge_reg._sessions) == 0


async def test_tool_chain_tool_error_propagates():
    from loamz.runtime.toolkit.chain.tool import ToolChainTool

    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    interpreter = FakeInterpreter(output="RuntimeError: bad input", is_error=True)

    tool = ToolChainTool(
        invoker=invoker,
        interpreter=interpreter,
        bridge_base_url="http://localhost:8001",
    )

    result = await tool.execute(code="raise RuntimeError('bad input')")
    assert result.is_error
    assert result.structured_content.get("status") == "error"


def test_tool_chain_tool_raises_without_interpreter():
    from loamz.runtime.toolkit.chain.tool import ToolChainTool

    registry = make_registry()
    invoker = ToolInvoker(registry=registry)
    with pytest.raises(RuntimeError, match="CodeInterpreterTool"):
        ToolChainTool(invoker=invoker, interpreter=None)


async def test_tool_chain_tool_prelude_includes_registry_names():
    from loamz.runtime.toolkit.chain.tool import ToolChainTool

    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    interpreter = FakeInterpreter(output="ok")

    tool = ToolChainTool(
        invoker=invoker,
        interpreter=interpreter,
        bridge_base_url="http://localhost:8001",
    )

    await tool.execute(code="pass")
    # The injected code should contain the tool name
    assert "echo" in interpreter.last_code


# ---------------------------------------------------------------------------
# What the script may do is what the interpreter may do
# ---------------------------------------------------------------------------


class _ScopedInterpreter(FakeInterpreter):
    """Like the real interpreter: it needs the run's ctx, and it classifies the code it is asked to run."""

    risk = ToolRisk.CRITICAL

    def __init__(self) -> None:
        super().__init__(output="ok")
        self.ctx: Any = "unset"

    async def execute(self, *, code: str, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        self.ctx = ctx
        return await super().execute(code=code)

    async def classify_risk(self, arguments: dict[str, Any]) -> tuple[ToolRisk, str | None]:
        return ToolRisk.CRITICAL, "runs a shell command"


@pytest.mark.asyncio
async def test_a_chain_is_as_risky_as_the_code_it_runs_and_runs_with_the_callers_ctx() -> None:
    from loamz.runtime.toolkit.chain.tool import ToolChainTool

    interpreter = _ScopedInterpreter()
    chain = ToolChainTool(invoker=ToolInvoker(make_registry(EchoTool())), interpreter=interpreter)

    assert chain.risk == ToolRisk.CRITICAL, (
        "arbitrary code needs the approval the interpreter needs"
    )
    risk, why = await chain.classify_risk({"code": "import os; os.system('x')"})
    assert (risk, why) == (ToolRisk.CRITICAL, "runs a shell command")

    ctx = object()
    result = await chain.execute(code="return 1", ctx=ctx)
    assert not result.is_error
    assert interpreter.ctx is ctx, "the interpreter needs the run's scope, or it refuses to run"
