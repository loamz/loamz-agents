import httpx
import pytest

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.people.marketplace.catalog import CATALOG
from loamz.server.agents.features.people.marketplace.model import InstallBody, InstallPatch
from loamz.server.agents.features.people.marketplace.service import MarketplaceService
from loamz.server.agents.features.people.marketplace.store import InstallStore
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")


def test_the_catalog_never_starts_writing_or_deleting_as_allowed():
    for item in CATALOG:
        for permission in item.permissions:
            assert permission.group == "read" or permission.default != "allow", item.id
        assert len(item.tagline) <= 80, item.id


async def test_installs_belong_to_one_person_and_connectors_need_an_account(db):
    service = MarketplaceService(InstallStore(db))
    record = await service.install(ALICE, "documents", InstallBody(permissions={"write": "allow"}))
    assert record.permissions == {"read": "allow", "write": "allow", "delete": "ask"}
    assert [i.id for i in (await service.installed(ALICE)).items] == ["documents"]
    assert (await service.installed(BOB)).items == []
    await service.remove(ALICE, "documents")
    assert (await service.installed(ALICE)).items == []
    try:
        await service.install(ALICE, "gmail", InstallBody())
    except Exception as e:  # noqa: BLE001 - the typed error is what the API turns into a problem document
        assert "connected account" in str(e)
    else:
        raise AssertionError("a connector installed without an account")


def test_the_catalog_can_be_searched_and_filtered(db=None):
    service = MarketplaceService(InstallStore(None))  # type: ignore[arg-type]
    assert {i.kind for i in service.catalog(kind="connector").items} == {"connector"}
    assert [i.id for i in service.catalog(kind="connector", q="google calendar").items] == [
        "google-calendar"
    ]
    assert [i.name for i in service.catalog(sort="name").items] == sorted(
        (i.name for i in CATALOG), key=str.lower
    )


async def test_the_api_lists_installs_and_removes(tmp_path):
    app = create_app(Container(Settings(database={"path": str(tmp_path / "agents.db")})))
    app.dependency_overrides[caller_dependency] = lambda: ALICE
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            catalog = (await client.get("/api/v1/marketplace?kind=skill")).json()
            assert catalog["items"] and all(i["kind"] == "skill" for i in catalog["items"])
            assert (
                await client.put("/api/v1/me/installs/data-analysis", json={})
            ).status_code == 200
            assert [i["id"] for i in (await client.get("/api/v1/me/installs")).json()["items"]] == [
                "data-analysis"
            ]
            assert (await client.put("/api/v1/me/installs/nope", json={})).status_code == 404
            assert (await client.delete("/api/v1/me/installs/data-analysis")).status_code == 204
            assert (await client.get("/api/v1/me/installs")).json()["items"] == []


async def test_switched_on_skills_reach_the_assistant_and_nothing_else_does(db):
    service = MarketplaceService(InstallStore(db))
    assert await service.skills_text(ALICE) == ""
    await service.install(ALICE, "data-analysis", InstallBody())
    await service.install(
        ALICE,
        "documents",
        InstallBody(permissions={"read": "block", "write": "block", "delete": "block"}),
    )
    await service.install(ALICE, "code-interpreter", InstallBody())
    text = await service.skills_text(ALICE)
    assert "Data analysis" in text and "Code interpreter" not in text
    assert await service.skills_text(BOB) == ""
    # A tool whose every group is blocked is not offered.
    assert set(await service.tools_on(ALICE)) == {"code-interpreter"}


async def test_an_added_item_can_be_switched_off_and_its_permissions_changed(db):
    service = MarketplaceService(InstallStore(db))
    await service.install(ALICE, "data-analysis", InstallBody())
    await service.install(ALICE, "code-interpreter", InstallBody())
    off = await service.change(ALICE, "data-analysis", InstallPatch(enabled=False))
    assert off.enabled is False
    assert await service.skills_text(ALICE) == ""
    await service.change(ALICE, "code-interpreter", InstallPatch(enabled=False))
    assert await service.tools_on(ALICE) == {}
    await service.change(
        ALICE, "code-interpreter", InstallPatch(enabled=True, permissions={"write": "allow"})
    )
    assert (await service.tools_on(ALICE))["code-interpreter"]["write"] == "allow"
    with pytest.raises(NotFoundError):
        await service.change(BOB, "data-analysis", InstallPatch(enabled=True))


async def test_a_plugin_adds_what_it_bundles_except_apps_that_need_signing_in(db):
    service = MarketplaceService(InstallStore(db))
    plugin = service.listing("plugin-planning")
    assert plugin.kind == "plugin" and {"project-planning", "todoist"} <= set(plugin.includes)
    for inner in service.bundled(plugin):
        assert inner.id != plugin.id and inner.kind in {
            "skill",
            "tool",
            "connector",
        }  # bundles are made of real parts, never of plugins
    await service.install(ALICE, "plugin-planning", InstallBody())
    ids = {i.id for i in (await service.installed(ALICE)).items}
    assert {"plugin-planning", "product-requirements", "project-planning"} <= ids
    assert "todoist" not in ids and "linear" not in ids  # connecting is the person's own sign-in
    assert {i.id for i in (await service.installed(BOB)).items} == set()
    assert all(i.kind == "plugin" for i in service.catalog(kind="plugin").items)


def test_every_shipped_skill_is_a_real_skill_file_and_every_plugin_bundles_real_parts():
    from loamz.server.agents.features.people.marketplace.shipped import (
        shipped_instructions,
        shipped_listings,
    )

    skills = shipped_listings()
    assert {s.id for s in skills} >= {
        "data-analysis",
        "web-development",
        "software-architecture",
        "lead-finding",
        "security-review",
    }
    assert not {s.id for s in skills} & {
        "summarize",
        "translate",
        "writing-assistant",
        "daily-briefing",
    }  # what any model already does is not a skill
    for skill in skills:
        body = shipped_instructions(skill.id) or ""
        assert len(body) > 1500, (
            f"{skill.id} is too thin to be a skill"
        )  # a skill carries real procedure, not a sentence
        assert len(skill.description) <= 1024 and skill.tagline and skill.icon and skill.category
        assert body.lstrip().startswith("#"), f"{skill.id} should start with a heading"
    ids = {i.id for i in CATALOG}
    assert len(ids) == len(CATALOG)  # no id is used twice
    for plugin in (i for i in CATALOG if i.kind == "plugin"):
        assert (
            plugin.includes
            and set(plugin.includes) <= ids
            and not any(i.startswith("plugin-") for i in plugin.includes)
        )


def test_every_agent_template_is_a_complete_agent_made_of_real_parts():
    from loamz.server.agents.features.directory.agents.model import MAX_NAME_CHARS
    from loamz.server.agents.features.people.marketplace.templates import ASK_FIRST

    parts = {i.id: i for i in CATALOG if i.kind in {"skill", "tool", "connector"}}
    gives = {c for i in parts.values() for c in i.provides}
    templates = [i for i in CATALOG if i.kind == "agent"]
    assert {
        "agent-email-triage",
        "agent-office-assistant",
        "agent-project-manager",
        "agent-restaurant-explorer",
    } <= {t.id for t in templates}
    for t in templates:
        assert (
            0 < len(t.agent_name) <= MAX_NAME_CHARS
            and 0 < len(t.role) <= 160
            and len(t.tagline) <= 80
        )
        assert len(t.instructions) <= 8000 and ASK_FIRST in t.instructions, (
            f"{t.id} must say that changes wait for the person"
        )
        assert t.includes and set(t.includes) <= set(parts), (
            f"{t.id} includes something that is not a skill, tool or connector"
        )
        # An app that cannot be connected yet is shown as a want, never pre-added.
        assert not any(parts[i].needs for i in t.includes), (
            f"{t.id} pre-adds an app nobody can connect yet"
        )
        assert len(t.includes) <= 8 and set(t.wants) <= gives, (
            f"{t.id} wants something nothing provides"
        )


def test_every_connectable_connector_is_a_vendors_https_server_that_says_who_sees_the_text():
    for c in (i for i in CATALOG if i.kind == "connector" and i.mcp_url):
        assert c.mcp_url.startswith("https://") and "@" not in c.mcp_url, c.id
        assert c.trust in {"verified", "publisher"} and c.publisher_url and c.checked_on, c.id
        assert {c.publisher, c.name, c.name.split()[0]} & set(c.data_seen_by), (
            f"{c.id} must name the vendor that receives the text"
        )
        assert not c.needs, c.id
        assert {p.group for p in c.permissions} >= {"read"} and c.permissions[
            0
        ].default == "allow", c.id
        assert all(p.default != "allow" for p in c.permissions if p.group != "read"), c.id
        assert set(c.tools.values()) <= {"read", "write", "delete"}
        # What a curated entry offers must be covered by a permission group the person can set.
        assert {g for g in c.tools.values()} <= {p.group for p in c.permissions}, c.id
