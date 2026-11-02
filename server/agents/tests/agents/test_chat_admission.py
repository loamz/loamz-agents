"""A message is refused before streaming starts; a run left behind by a restart does not block the conversation."""

from types import SimpleNamespace

import pytest

from loamz.engine.errors import ConflictError, InvalidRequestError, RateLimitedError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.features.conversations.chat.model import ChatRequest
from loamz.server.agents.features.conversations.chat.service import ChatService

CALLER = Caller(tenant_id="acme", user_id="alice")


class Runtime:
    def __init__(self, runs, live):
        self.runs, self.live, self.cancelled = runs, live, []

    async def find_runs(self, *, thread_id=None, active_only=True):
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


class Preferences:
    """Whether this person has paused their agents."""

    def __init__(self, paused=False):
        self.paused = paused

    async def agents_paused(self, caller):
        return self.paused


class Counters:
    """A day counter, as the real one: `add_day` counts, then says whether the count is within `limit`."""

    def __init__(self):
        self.days = {}

    async def add_day(self, key, *, limit, count=1):
        self.days[key] = (
            self.days.get(key, 0) + count
        )  # counted first, as the real counters do; the caller gives it back
        return SimpleNamespace(allowed=self.days[key] <= limit)

    async def release_day(self, key, *, count=1):
        self.days[key] = max(0, self.days.get(key, 0) - count)


class Plan:
    """The tenant's plan: refuses with `error` once its daily limit is spent."""

    def __init__(self, error=None):
        self.error = error

    async def enforce_daily_messages(self, tenant_id):
        if self.error:
            raise self.error


def service(runtime, threads=None, paused=False, allowance=100, plan=None, counters=None):
    counters = counters or Counters()
    chat = ChatService(
        SimpleNamespace(
            runtime=runtime,
            require_model=lambda model=None: None,
            get=lambda provider: (
                plan or Plan() if provider.__name__ == "entitlements" else Preferences(paused)
            ),
            counters=counters,
            settings=SimpleNamespace(
                rate_limit=SimpleNamespace(
                    enabled=True, user_daily_messages=allowance, fail_open=False
                )
            ),
        ),
        threads or Threads(),
    )
    chat.counters = counters
    return chat


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


async def test_a_person_may_start_only_as_many_turns_a_day_as_their_allowance_and_is_told_when_it_comes_back():
    chat = service(Runtime([], set()), allowance=2)
    await chat.admit(CALLER, request())
    await chat.admit(CALLER, request())
    with pytest.raises(RateLimitedError) as refused:
        await chat.admit(CALLER, request())
    assert "2 messages" in str(refused.value) and refused.value.retry_after > 0
    assert chat.counters.days["usermsgs:acme:alice"] == 2  # the refused third did not count
    await chat.admit(
        Caller(tenant_id="acme", user_id="bob"), request()
    )  # another person has their own


async def test_a_message_refused_for_another_reason_costs_nothing_of_the_allowance():
    chat = service(Runtime([], set()), allowance=1)
    with pytest.raises(InvalidRequestError):
        await chat.admit(CALLER, request(prompt=""))
    await chat.admit(CALLER, request())  # the empty one was not counted, so this is still the first


async def test_the_tenants_plan_limit_refuses_too_and_gives_the_persons_count_back():
    spent = RateLimitedError("Daily message limit (5) reached.", retry_after=60)
    chat = service(Runtime([], set()), allowance=10, plan=Plan(spent))
    with pytest.raises(RateLimitedError):
        await chat.admit(CALLER, request())
    assert chat.counters.days["usermsgs:acme:alice"] == 0


async def test_an_allowance_of_zero_means_no_limit():
    chat = service(Runtime([], set()), allowance=0)
    for _ in range(3):
        await chat.admit(CALLER, request())
    assert chat.counters.days == {}


def test_starting_a_chat_goes_through_the_per_minute_rate_limit():
    from loamz.server.agents.bootstrap.http import rate_limit
    from loamz.server.agents.features.conversations.chat.api import router

    route = next(r for r in router.routes if r.path == "/api/v1/chat" and "POST" in r.methods)
    assert any(d.call is rate_limit for d in route.dependant.dependencies)


# ── rejoining a run that waited while the console was away ───────────────────
from loamz.engine.errors import NotFoundError  # noqa: E402
from loamz.runtime.kernel.types.run_log import RunLogKind  # noqa: E402


class Recorded:
    """A run with five entries already on its log; `tail` yields what is at or after `from_seq`, as the real one does."""

    def __init__(self, run_id="r1", tenant="acme"):
        self.run = SimpleNamespace(run_id=run_id, tenant=tenant, agent="assistant/t1")
        self.entries = [
            SimpleNamespace(seq=i, kind=RunLogKind.RUN_STARTED, payload={}) for i in range(5)
        ]
        self.entries.append(SimpleNamespace(seq=5, kind=RunLogKind.RUN_COMPLETED, payload={}))
        self.tailed_from = None

    async def find_runs(self, *, thread_id, active_only):
        return [self.run]

    async def read(self, run_id):
        return self.entries[:5]  # the durable log so far: the run is still going

    async def tail(self, run_id, *, from_seq=0):
        self.tailed_from = from_seq
        for entry in self.entries:
            if entry.seq >= from_seq:
                yield entry


async def test_a_console_that_reloaded_rejoins_the_run_after_what_is_recorded():
    runtime = Recorded()
    svc = service(runtime)
    run_id, from_seq = await svc.attach(CALLER, "t1")
    assert (run_id, from_seq) == ("r1", 5)
    lines = [line async for line in svc.reconnect(run_id, from_seq)]
    assert runtime.tailed_from == 5  # nothing before the card is replayed
    assert lines[0] == 'data: {"type": "ping"}\n\n'  # sent at once, before anything the run does
    assert 'data: {"type": "run.completed"}\n\n' in lines and lines[-1] == "data: [DONE]\n\n"


async def test_there_is_nothing_to_rejoin_when_no_run_is_in_progress_or_it_is_someone_elses():
    class Idle(Recorded):
        async def find_runs(self, *, thread_id, active_only):
            return []

    with pytest.raises(NotFoundError):
        await service(Idle()).attach(CALLER, "t1")
    with pytest.raises(NotFoundError):
        await service(Recorded(tenant="other")).attach(CALLER, "t1")


# ── an approval whose run cannot continue is not left waiting ────────────────
class Waiting(Runtime):
    def __init__(self, live):
        super().__init__(
            [SimpleNamespace(run_id="r1", agent="assistant/t1", tenant="acme", thread_id="t1")],
            live,
        )

    async def pending_approvals(self, run_id):
        return [
            SimpleNamespace(
                request_id="q1", tool_name="code_interpreter", args={}, risk="critical", summary="s"
            )
        ]


async def test_an_approval_waiting_in_a_run_that_is_alive_is_reported():
    status = await service(Waiting({"assistant/t1"})).hitl_status(CALLER, "t1")
    assert status.status == "waiting" and [p["request_id"] for p in status.pending] == ["q1"]


async def test_an_approval_whose_run_died_with_a_restart_is_cleared_not_left_waiting():
    runtime = Waiting(set())
    status = await service(runtime).hitl_status(CALLER, "t1")
    assert status.status == "idle" and status.pending == [] and runtime.cancelled == ["r1"]


async def test_answering_an_approval_the_server_can_no_longer_continue_says_so():
    from loamz.server.agents.features.conversations.chat.model import HitlResponse

    runtime = Waiting(set())
    with pytest.raises(ConflictError, match="restarted"):
        await service(runtime).respond_hitl(CALLER, "q1", HitlResponse(action="deny"))
    assert runtime.cancelled == ["r1"]


class OwnedBy(Threads):
    """Only `owner` may open the thread, as in the real service."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    async def require(self, caller, thread_id):
        if caller.user_id != self.owner:
            raise NotFoundError("Thread not found")
        return await super().require(caller, thread_id)


async def test_a_person_in_the_same_organization_cannot_list_or_answer_anothers_approval():
    from loamz.server.agents.features.conversations.chat.model import HitlResponse

    runtime = Waiting({"assistant/t1"})
    svc = service(runtime, OwnedBy("alice"))
    mallory = Caller(tenant_id="acme", user_id="mallory")
    assert await svc.list_approvals(mallory) == []
    assert [a.request_id for a in await svc.list_approvals(CALLER)] == ["q1"]
    with pytest.raises(NotFoundError):
        await svc.respond_hitl(mallory, "q1", HitlResponse(action="approve"))


async def test_a_message_to_an_agent_is_refused_while_the_person_has_paused_their_agents():
    with pytest.raises(InvalidRequestError, match="paused"):
        await service(Runtime([], set()), paused=True).admit(CALLER, request())
