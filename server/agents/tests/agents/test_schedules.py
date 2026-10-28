"""Schedules belong to one chat agent, run on their own, and report in that agent's chat: once per time, recoverable, quiet when asked."""

from __future__ import annotations

import asyncio
import time

import pytest

from loamz.engine.errors import ConflictError, InvalidRequestError, NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.testing.scripted import ScriptedModel
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.features.automation.schedules.model import (
    MAX_SCHEDULES_PER_PERSON,
    ScheduleIn,
    SchedulePatch,
)
from loamz.server.agents.features.automation.schedules.runner import LEASE_SECONDS, NOTHING, tick
from loamz.server.agents.features.automation.schedules.service import schedules
from loamz.server.agents.features.automation.schedules.tool import schedule_tools
from loamz.server.agents.features.directory.agents.model import AgentIn
from loamz.server.agents.features.directory.agents.service import agents
from loamz.server.agents.features.people.notifications.service import notifications

ALEX = Caller(tenant_id="acme", user_id="alex", name="Alex")
SAM = Caller(tenant_id="acme", user_id="sam", name="Sam")


@pytest.fixture
async def world(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "f")},
            llm={"openai_api_key": "test"},
            chat={"max_concurrent_runs": 4},
        )
    )
    await container.start({"all"})
    agent = await container.get(agents).create(ALEX, AgentIn(name="Inbox", role="Reads mail"))
    container.get(schedules)  # registers the hook that clears schedules when an agent is deleted
    yield container, agent
    await container.aclose()


def reply(container, *answers):
    model = ScriptedModel(*answers)
    container.model_client = lambda name=None: model
    container.chat_model = lambda name=None: model
    return model


def morning(agent_id: str, **kw) -> ScheduleIn:
    return ScheduleIn(
        agent_id=agent_id,
        name="Morning brief",
        prompt="Tell me what needs a reply.",
        kind="recurring",
        cron_expression="0 8 * * 1-5",
        timezone="Europe/London",
        **kw,
    )


async def due_now(container, schedule_id: str, tenant="acme") -> None:
    await container.get(schedules).store.change(tenant, schedule_id, next_run_at=time.time() - 5)


async def chat_texts(container, agent) -> list[str]:
    header = await container.get(agents).open_thread(ALEX, agent.id)
    return [n.payload.text for n in await container.agent_store.threads.session_nodes(header.id)]


async def test_a_schedule_is_made_for_one_of_the_persons_agents_with_its_timing_in_words(world):
    container, agent = world
    made = await container.get(schedules).create(ALEX, morning(agent.id))
    assert (
        made.agent_name == "Inbox"
        and made.timing == "Every weekday at 08:00 (Europe/London)"
        and made.next_run_at > time.time()
    )
    assert [s.id for s in (await container.get(schedules).list(ALEX)).items] == [made.id]
    assert (await container.get(schedules).list(SAM)).items == []  # not anyone else's
    with pytest.raises(NotFoundError):
        await container.get(schedules).create(SAM, morning(agent.id))  # not Sam's agent
    with pytest.raises(NotFoundError):
        await container.get(schedules).get(SAM, made.id)


async def test_the_timing_must_make_sense_and_a_person_has_a_limit(world):
    container, agent = world
    service = container.get(schedules)
    with pytest.raises(InvalidRequestError, match="15 minutes"):
        await service.create(
            ALEX,
            ScheduleIn(
                agent_id=agent.id, name="x", prompt="x", kind="interval", interval_seconds=60
            ),
        )
    with pytest.raises(InvalidRequestError, match="future"):
        await service.create(
            ALEX, ScheduleIn(agent_id=agent.id, name="x", prompt="x", kind="once", run_at=1.0)
        )
    with pytest.raises(InvalidRequestError, match="5 parts"):
        await service.create(
            ALEX, ScheduleIn(agent_id=agent.id, name="x", prompt="x", cron_expression="every day")
        )
    with pytest.raises(InvalidRequestError, match="time zone"):
        await service.create(ALEX, morning(agent.id).model_copy(update={"timezone": "Mars/Base"}))
    for i in range(MAX_SCHEDULES_PER_PERSON):
        await service.create(ALEX, morning(agent.id).model_copy(update={"name": f"s{i}"}))
    with pytest.raises(ConflictError):
        await service.create(ALEX, morning(agent.id))


async def test_a_due_schedule_runs_once_in_its_agents_chat_and_the_person_is_told(world):
    container, agent = world
    reply(container, "Two emails need a reply today: the bank and Priya.")
    made = await container.get(schedules).create(ALEX, morning(agent.id))
    await due_now(container, made.id)
    # Two servers looking at the same moment still run it once.
    tasks = [*await tick(container), *await tick(container)]
    await asyncio.gather(*tasks)
    texts = await chat_texts(container, agent)
    assert sum("Two emails need a reply" in t for t in texts) == 1 and any(
        "Scheduled task “Morning brief”" in t for t in texts
    )
    after = await container.get(schedules).get(ALEX, made.id)
    assert (
        [f.status for f in after.recent] == ["succeeded"]
        and after.last_run_at
        and after.next_run_at > time.time()
    )
    assert [f.zone for f in after.recent] == [
        "Europe/London"
    ]  # the zone it was set for is recorded with the run
    note = (await container.get(notifications).recent(ALEX))[0]
    assert "Morning brief" in note.title and "bank" in note.body


async def test_after_a_long_stop_it_runs_once_not_once_per_missed_time(world):
    container, agent = world
    reply(container, "Done.")
    made = await container.get(schedules).create(ALEX, morning(agent.id))
    await container.get(schedules).store.change(
        "acme", made.id, next_run_at=time.time() - 10 * 24 * 3600
    )
    await asyncio.gather(*await tick(container))
    assert len((await container.get(schedules).get(ALEX, made.id)).recent) == 1
    assert (await container.get(schedules).get(ALEX, made.id)).next_run_at > time.time()


async def test_a_quiet_schedule_stays_out_of_the_chat_until_there_is_something_to_report(world):
    container, agent = world
    made = await container.get(schedules).create(ALEX, morning(agent.id, quiet=True))
    reply(container, NOTHING)
    await due_now(container, made.id)
    await asyncio.gather(*await tick(container))
    assert await chat_texts(container, agent) == []  # nothing in the agent's own chat
    assert (await container.get(schedules).get(ALEX, made.id)).recent[
        0
    ].summary == "Nothing to report."
    assert await container.get(notifications).recent(ALEX) == []
    reply(container, "The site is down.")
    await due_now(container, made.id)
    await asyncio.gather(*await tick(container))
    assert "The site is down." in (await container.get(notifications).recent(ALEX))[0].body


async def test_a_one_off_completes_after_it_runs_and_a_stale_one_is_marked_missed(world):
    container, agent = world
    reply(container, "Reminder sent.")
    soon = time.time() + 3600
    made = await container.get(schedules).create(
        ALEX,
        ScheduleIn(
            agent_id=agent.id,
            name="Call the bank",
            prompt="Remind me.",
            kind="once",
            run_at=soon,
            timezone="UTC",
        ),
    )
    await asyncio.gather(*await tick(container, now=soon + 10))
    done = await container.get(schedules).get(ALEX, made.id)
    assert (
        done.status == "completed"
        and done.next_run_at is None
        and done.recent[0].status == "succeeded"
    )
    late = await container.get(schedules).create(
        ALEX,
        ScheduleIn(
            agent_id=agent.id,
            name="Old",
            prompt="x",
            kind="once",
            run_at=time.time() + 60,
            timezone="UTC",
        ),
    )
    await asyncio.gather(*await tick(container, now=time.time() + 8 * 3600))
    assert (await container.get(schedules).get(ALEX, late.id)).recent[0].status == "missed"


async def test_a_firing_that_was_recorded_but_never_started_is_picked_up_and_a_dead_worker_is_recovered(
    world,
):
    container, agent = world
    reply(container, "Ran.")
    service = container.get(schedules)
    made = await service.create(ALEX, morning(agent.id))
    s = await service.store.by_id("acme", made.id)
    # A crash right after the firing was recorded, before anything ran: the firing waits.
    assert await service.store.begin_firing(s, time.time() - 1, s.next_run_at) is not None
    assert (
        await service.store.begin_firing(
            s, (await service.store.firings("acme", "alex", made.id))[0].due_at, None, advance=False
        )
        is None
    )  # recorded once
    await asyncio.gather(*await tick(container))
    assert [f.status for f in (await service.get(ALEX, made.id)).recent] == ["succeeded"]
    # A worker that took a firing and died: after its lease runs out another takes it, as attempt 2.
    other = await service.store.begin_firing(s, time.time() - 2, s.next_run_at)
    taken = await service.store.claim(time.time(), LEASE_SECONDS)
    assert [f.id for f in taken] == [other] and taken[0].attempt == 0
    assert await service.store.claim(time.time(), LEASE_SECONDS) == []  # held
    again = await service.store.claim(time.time() + LEASE_SECONDS + 1, LEASE_SECONDS)
    assert [f.id for f in again] == [other]


async def test_three_failures_in_a_row_pause_it_and_deleting_the_agent_deletes_its_schedules(world):
    container, agent = world

    class Broken:
        capabilities = ScriptedModel().capabilities
        model = "broken"

        async def generate(self, *a, **k):
            raise RuntimeError("model unavailable")

        async def generate_stream(self, *a, **k):
            raise RuntimeError("model unavailable")
            yield

    container.model_client = lambda name=None: Broken()
    service = container.get(schedules)
    made = await service.create(ALEX, morning(agent.id))
    for _ in range(3):
        await due_now(container, made.id)
        await asyncio.gather(*await tick(container))
        for _ in range(4):  # a temporary failure is tried again after its lease
            await asyncio.gather(*await tick(container, now=time.time() + LEASE_SECONDS * 2))
    assert (await service.get(ALEX, made.id)).status == "paused"
    assert "paused" in (await container.get(notifications).recent(ALEX))[0].body
    await container.get(agents).delete(ALEX, agent.id)
    assert (await service.list(ALEX)).items == []


async def test_the_agents_own_tools_look_freely_and_change_only_its_own_schedules(world):
    container, agent = world
    service = container.get(schedules)
    listing, changing = schedule_tools(service, ALEX, agent.id)
    assert (
        listing.risk.name == "SAFE" and changing.risk.name == "HIGH"
    )  # a change is asked about first
    assert "no schedules" in (await listing.execute()).content[0].text
    made = await changing.execute(
        action="create",
        summary="Every weekday at 8:00",
        name="Brief",
        task="Summarize my mail.",
        kind="recurring",
        cron_expression="0 8 * * 1-5",
        timezone="Europe/London",
    )
    assert not made.is_error and "Every weekday at 08:00" in made.content[0].text
    shown = (await listing.execute()).content[0].text
    schedule_id = shown.split("[")[1].split("]")[0]
    assert "Brief" in shown and "Every weekday" in shown
    assert not (await changing.execute(action="pause", summary="pause", id=schedule_id)).is_error
    assert (await service.get(ALEX, schedule_id)).status == "paused"
    other = await container.get(agents).create(ALEX, AgentIn(name="Other"))
    _, elsewhere = schedule_tools(service, ALEX, other.id)
    assert (
        await elsewhere.execute(action="delete", summary="x", id=schedule_id)
    ).is_error  # not this agent's schedule
    assert (
        await changing.execute(
            action="create", summary="x", name="y", task="z", kind="interval", interval_minutes=5
        )
    ).is_error
    assert not (await changing.execute(action="delete", summary="x", id=schedule_id)).is_error
    patched = await service.create(ALEX, morning(agent.id))
    assert (
        await service.patch(ALEX, patched.id, SchedulePatch(cron_expression="30 9 * * *"))
    ).timing == "Every day at 09:30 (Europe/London)"


async def test_the_result_is_mailed_to_the_address_the_person_had_when_they_asked(
    world, monkeypatch
):
    container, agent = world
    sent: list[tuple[str, str, str]] = []

    async def email(to, subject, text, client=None):
        sent.append((to, subject, text))
        return True

    monkeypatch.setattr(container.get(notifications), "email", email)
    reply(container, "Two emails need a reply.")
    with_address = Caller(tenant_id="acme", user_id="alex", name="Alex", email="alex@example.test")
    made = await container.get(schedules).create(
        with_address, morning(agent.id, email_results=True)
    )
    await due_now(container, made.id)
    await asyncio.gather(*await tick(container))
    assert [(to, text) for to, _, text in sent] == [
        ("alex@example.test", "Two emails need a reply.")
    ]
    plain = await container.get(schedules).create(ALEX, morning(agent.id, email_results=False))
    await due_now(container, plain.id)
    await asyncio.gather(*await tick(container))
    assert len(sent) == 1  # one that did not ask is not mailed
