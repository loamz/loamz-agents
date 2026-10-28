"""An agent has its own skills, tools and connectors: only those reach its runs, and only reviewed catalog items can be given."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.features.directory.agents.model import AgentIn, AgentPatch
from loamz.server.agents.features.directory.agents.service import agents
from loamz.server.agents.features.people.marketplace.model import InstallBody
from loamz.server.agents.features.people.marketplace.service import MarketplaceService
from loamz.server.agents.features.people.marketplace.store import InstallStore

ALICE = Caller(tenant_id="acme", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")


@pytest.fixture
async def container(tmp_path):
    c = Container(
        Settings(database={"path": str(tmp_path / "a.db")}, files={"path": str(tmp_path / "files")})
    )
    yield c
    await c.aclose()


async def test_a_new_agent_starts_with_no_tools_and_keeps_the_abilities_it_was_given(container):
    service = container.get(agents)
    made = await service.create(
        ALICE, AgentIn(name="Scout", abilities=["data-analysis", "notion", "data-analysis"])
    )
    assert made.allowed_tools == [] and made.abilities == ["data-analysis", "notion"]
    again = await service.get(ALICE, made.id)
    assert again.abilities == ["data-analysis", "notion"]
    changed = await service.patch(ALICE, made.id, AgentPatch(abilities=["web-development"]))
    assert changed.abilities == ["web-development"]
    kept = await service.patch(ALICE, made.id, AgentPatch(name="Scout 2"))
    assert kept.abilities == ["web-development"]  # not sending abilities leaves them as they are


async def test_only_catalog_skills_tools_and_connectors_can_be_given(container):
    service = container.get(agents)
    with pytest.raises(Exception, match="Unknown skill, tool or connector"):
        await service.create(ALICE, AgentIn(name="Bad", abilities=["made-up"]))
    with pytest.raises(Exception, match="Unknown skill, tool or connector"):
        await service.create(
            ALICE, AgentIn(name="Bad", abilities=["researcher"])
        )  # an agent listing is not an ability


async def test_an_agent_hears_only_about_its_own_skills(db):
    market = MarketplaceService(InstallStore(db))
    for item in ("data-analysis", "web-development", "code-interpreter"):
        await market.install(ALICE, item, InstallBody())
    everything = await market.skills_text(ALICE)
    assert "Data analysis" in everything and "Web development" in everything
    assert (
        "use_skill" in everything and "Step 1" not in everything
    )  # the prompt carries names and descriptions, never the instructions
    mine = await market.skills_text(ALICE, {"data-analysis"})
    assert "Data analysis" in mine and "Web development" not in mine
    # An agent given a skill has it whether or not the person switched it on for themselves.
    given = await market.skills_text(BOB, {"security-review"})
    assert "Security review" in given
    assert await market.skills_text(ALICE, set()) == ""
    assert "Step 1" in (await market.skill_instructions(ALICE, "Data analysis") or "")
    assert (
        await market.skill_instructions(ALICE, "Security review") is None
    )  # not switched on, not given
    assert await market.skill_instructions(ALICE, "Data analysis", {"web-development"}) is None
    assert set(await market.tools_on(ALICE, set())) == set()
    assert set(await market.tools_on(ALICE, {"code-interpreter"})) == {"code-interpreter"}


async def test_the_tools_ticked_for_a_connector_are_kept_checked_and_dropped_with_the_connector(
    container,
):
    service = container.get(agents)
    ticked = {"notion": {"notion-search": "allow", "notion-create-pages": "ask"}}
    made = await service.create(
        ALICE, AgentIn(name="Scribe", abilities=["notion", "data-analysis"], tool_access=ticked)
    )
    assert made.tool_access == ticked and (await service.get(ALICE, made.id)).tool_access == ticked
    # Choices for a connector the agent does not have are refused, and so is a flood of names.
    with pytest.raises(Exception, match="does not have"):
        await service.create(
            ALICE, AgentIn(name="Bad", abilities=["notion"], tool_access={"linear": {"x": "ask"}})
        )
    with pytest.raises(Exception, match="too many"):
        await service.create(
            ALICE,
            AgentIn(
                name="Bad",
                abilities=["notion"],
                tool_access={"notion": {f"t{i}": "ask" for i in range(201)}},
            ),
        )
    with pytest.raises(ValidationError):  # only allow or ask exist: a tool is off by being left out
        AgentIn(name="Bad", abilities=["notion"], tool_access={"notion": {"x": "block"}})  # type: ignore[dict-item]
    # Changing something else keeps the choices; changing them replaces them; taking the connector away takes them with it.
    same = await service.patch(ALICE, made.id, AgentPatch(name="Scribe 2"))
    assert same.tool_access == ticked
    narrower = await service.patch(
        ALICE, made.id, AgentPatch(tool_access={"notion": {"notion-search": "allow"}})
    )
    assert narrower.tool_access == {"notion": {"notion-search": "allow"}}
    gone = await service.patch(ALICE, made.id, AgentPatch(abilities=["data-analysis"]))
    assert gone.tool_access == {}
    # Someone else's agent is still someone else's.
    with pytest.raises(NotFoundError):
        await service.patch(BOB, made.id, AgentPatch(tool_access={}))


async def test_an_agents_icon_and_colour_are_kept_changed_and_cleared(container):
    service = container.get(agents)
    made = await service.create(
        ALICE, AgentIn(name="Scout", look={"icon": "search", "color": "teal"})
    )
    assert made.look and (made.look.icon, made.look.color) == ("search", "teal")
    assert (await service.get(ALICE, made.id)).look == made.look  # kept in the store
    changed = await service.patch(
        ALICE, made.id, AgentPatch(look={"icon": "rocket", "color": "amber"})
    )
    assert changed.look and (changed.look.icon, changed.look.color) == ("rocket", "amber")
    kept = await service.patch(ALICE, made.id, AgentPatch(name="Scout 2"))
    assert kept.look == changed.look  # not sending a look leaves it as it is
    cleared = await service.patch(ALICE, made.id, AgentPatch(clear_look=True))
    assert cleared.look is None and (await service.get(ALICE, made.id)).look is None


def test_only_the_listed_icons_and_colours_are_accepted():
    with pytest.raises(ValidationError):
        AgentIn(name="Bad", look={"icon": "skull", "color": "teal"})
    with pytest.raises(ValidationError):
        AgentIn(name="Bad", look={"icon": "bot", "color": "#ff0000"})
