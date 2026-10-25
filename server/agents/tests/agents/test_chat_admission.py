"""A message is refused before streaming starts; a run left behind by a restart does not block the conversation."""

from types import SimpleNamespace

import pytest

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import ConflictError, InvalidRequestError
from loamz.server.agents.features.conversations.chat.model import ChatRequest
from loamz.server.agents.features.conversations.chat.service import ChatService

CALLER = Caller(tenant_id="acme", user_id="alice")


class Runtime:
    def __init__(self, runs, live):
        self.runs, self.live, self.cancelled = runs, live, []

    async def find_runs(self, *, thread_id, active_only):
        return [r for r in self.runs if r.run_id not in self.cancelled]

    def is_live(self, actor):
        return actor in self.live

    async def cancel(self, run_id, *, reason="cancelled"):
        self.cancelled.append(run_id)
        return [run_id]


class Threads:
    def __init__(self, locked=False):
        self.locked = locked

    async def require(self, caller, thread_id):
        return SimpleNamespace(
            locked_at="now" if self.locked else None, locked_reason=None, agent_id=None
        )


def service(runtime, threads=None):
    return ChatService(SimpleNamespace(runtime=runtime), threads or Threads())


def request(prompt="hello"):
    return ChatRequest(thread_id="t1", prompt=prompt)


async def test_a_run_whose_agent_is_gone_is_closed_and_the_message_goes_ahead():
    runtime = Runtime([SimpleNamespace(run_id="r1", agent="assistant/t1")], live=set())
    await service(runtime).admit(CALLER, request())
    assert runtime.cancelled == ["r1"]


async def test_a_run_still_going_here_refuses_a_second_message():
    runtime = Runtime([SimpleNamespace(run_id="r1", agent="assistant/t1")], live={"assistant/t1"})
    with pytest.raises(ConflictError):
        await service(runtime).admit(CALLER, request())
    assert runtime.cancelled == []


async def test_an_empty_or_locked_conversation_is_refused_up_front():
    with pytest.raises(InvalidRequestError):
        await service(Runtime([], set())).admit(CALLER, request(prompt=""))
    with pytest.raises(InvalidRequestError):
        await service(Runtime([], set()), Threads(locked=True)).admit(CALLER, request())
