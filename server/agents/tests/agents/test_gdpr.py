import re
from pathlib import Path

import httpx
import pytest

from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.ports.threads import MessageNode
from loamz.runtime.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.runtime.stores.workspace.layout import user_prefix
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.automation.schedules.model import ScheduleIn
from loamz.server.agents.features.automation.schedules.service import schedules
from loamz.server.agents.features.directory.agents.model import AgentIn
from loamz.server.agents.features.directory.agents.service import agents
from loamz.server.agents.features.governance.gdpr.service import _erasers, gdpr
from loamz.server.agents.features.people.notifications.service import notifications
from loamz.server.agents.features.people.preferences.model import Preferences
from loamz.server.agents.features.people.preferences.service import preferences
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")


@pytest.fixture
def app(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    return application


async def test_gdpr_erasure(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            user_resp = await client.post(
                "/api/v1/internal/gdpr/erase-user",
                json={"tenant_id": "acme", "user_id": "alice"},
            )
            assert user_resp.status_code == 200
            assert user_resp.json()["status"] == "completed"

            tenant_resp = await client.post(
                "/api/v1/internal/gdpr/erase-tenant",
                json={"tenant_id": "acme"},
            )
            assert tenant_resp.status_code == 200
            assert tenant_resp.json()["status"] == "completed"


# ── what is really removed ──────────────────────────────────────────────────

BOB = Caller(tenant_id="acme", user_id="bob")


def test_every_service_that_keeps_something_about_a_person_is_in_the_erase_list():
    """A feature added later that stores personal data must be erased too: this fails until it is added to `_erasers`."""
    root = Path(__file__).resolve().parents[2] / "src" / "loamz" / "server" / "agents" / "features"
    modules = {
        "loamz.server.agents.features." + ".".join(p.relative_to(root).with_suffix("").parts)
        for p in root.rglob("service.py")
        if re.search(r"async def erase\(self, tenant_id", p.read_text())
    }
    modules.discard("loamz.server.agents.features.governance.gdpr.service")
    listed = {provider.__module__ for _, provider in _erasers()}
    assert modules <= listed, f"keeps personal data but is never erased: {sorted(modules - listed)}"


@pytest.fixture
async def world(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "f")},
            llm={"openai_api_key": "test"},
        )
    )
    await container.start({"all"})
    yield container
    await container.aclose()


async def keep_things(container, caller: Caller, name: str) -> str:
    """One of everything a person leaves behind: an agent, its chat with a message, a schedule, settings, a notice and a file."""
    agent = await container.get(agents).create(caller, AgentIn(name=name, role="Helps"))
    chat = await container.get(agents).open_thread(caller, agent.id)
    await container.agent_store.threads.append_and_advance(
        MessageNode(
            session_id=chat.id,
            parent_id=None,
            payload=ChatMessage(role=Role.USER, content=(TextBlock(text=f"{name}'s secret"),)),
        ),
        "main",
    )
    await container.get(schedules).create(
        caller,
        ScheduleIn(
            agent_id=agent.id,
            name="Brief",
            prompt="Tell me.",
            kind="interval",
            interval_seconds=3600,
        ),
    )
    await container.get(preferences).replace(
        caller, Preferences(timezone="Europe/London", custom_instructions=f"{name} likes tea")
    )
    await container.get(notifications).notify(caller.tenant_id, caller.user_id, "Hello", "A notice")
    await container.file_store.upload(
        f"{user_prefix(caller.tenant_id, caller.user_id)}/conversations/{chat.id}/workspace/shared/note.txt",
        b"hello",
    )
    return chat.id


async def test_erasing_one_person_removes_all_of_their_data_and_none_of_anyone_elses(world):
    container = world
    alice_chat = await keep_things(container, ALICE, "Alice")
    bob_chat = await keep_things(container, BOB, "Bob")

    summary = await container.get(gdpr).erase_user("acme", "alice")

    assert summary.status == "completed" and summary.failed == []
    assert (
        summary.removed["conversations"] >= 1
        and summary.removed["schedules"] >= 1
        and summary.objects_deleted >= 1
    )
    # Alice: gone, from the history to the settings.
    assert (await container.get(agents).list(ALICE)).items == []
    assert (await container.get(schedules).list(ALICE)).items == []
    assert await container.agent_store.threads.session_nodes(alice_chat) == []
    assert await container.get(notifications).recent(ALICE) == []
    assert (await container.get(preferences).get(ALICE)).timezone == ""
    assert await container.file_store.list_prefix(user_prefix("acme", "alice")) == []
    # Bob: every bit still there.
    assert len((await container.get(agents).list(BOB)).items) == 1
    assert len((await container.get(schedules).list(BOB)).items) == 1
    assert [
        n.payload.text for n in await container.agent_store.threads.session_nodes(bob_chat)
    ] == ["Bob's secret"]
    assert len(await container.get(notifications).recent(BOB)) == 1
    assert (await container.get(preferences).get(BOB)).timezone == "Europe/London"
    assert len(await container.file_store.list_prefix(user_prefix("acme", "bob"))) == 1


async def test_a_person_can_erase_their_own_data_through_the_api_and_only_their_own(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            files={"path": str(tmp_path / "f")},
            llm={"openai_api_key": "test"},
        )
    )
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    async with application.router.lifespan_context(application):
        await keep_things(container, ALICE, "Alice")
        await keep_things(container, BOB, "Bob")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            reply = await client.delete("/api/v1/me/data")
        assert reply.status_code == 200 and reply.json()["status"] == "completed"
        assert (await container.get(agents).list(ALICE)).items == []
        assert len((await container.get(agents).list(BOB)).items) == 1
