"""Messenger contracts: membership, durable mutations and catch-up, without a live identity provider."""

import asyncio
import json

import httpx
import pytest

from loamz.engine.errors import NotFoundError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.kernel.runtime.channel import EntryKind, ParticipantKind
from loamz.runtime.kernel.types.identity import Actor
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.features.messaging.chats.service import chats
from loamz.server.agents.features.messaging.feed.model import FeedIn
from loamz.server.agents.features.messaging.feed.service import feed
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice", name="Alice")
BOB = Caller(tenant_id="acme", user_id="bob", name="Bob")
MALLORY = Caller(tenant_id="other", user_id="bob", name="Mallory")


class Directory:
    def user(self, tenant, user):
        if tenant != "acme" or user not in ("alice", "bob"):
            raise NotFoundError("User not found")
        return {"id": user, "firstName": user.title()}

    def search_people(self, tenant, query):
        return [self.user(tenant, name) for name in ("alice", "bob") if query.casefold() in name]


@pytest.fixture
async def messenger(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "agents.db")}, files={"path": str(tmp_path / "files")}
        )
    )
    container.__dict__["directory"] = Directory()
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            yield container, application, client


def as_user(app, caller):
    app.dependency_overrides[caller_dependency] = lambda: caller


async def create(client, **values):
    response = await client.post(
        "/api/v1/groups",
        json={"name": "Engineering", "members": [{"member": "user:bob"}], **values},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_two_people_share_real_edits_reactions_and_deletions(messenger):
    container, app, client = messenger
    group = await create(client)
    path = f"/api/v1/groups/{group['id']}"
    assert {m["kind"] for m in group["members"]} == {"human"}
    assert group["e2ee"] is False
    assert container.get(chats)._channels is container.runtime.store
    members = await container.runtime.store.channel_members(f"group/{group['id']}")
    assert all(m.kind == ParticipantKind.HUMAN and m.inbox is None for m in members)
    sent = (await client.post(path + "/messages", json={"text": "Hello"})).json()
    seq = sent["seq"]
    assert sent["from_user"] is True
    edit = await client.patch(f"{path}/messages/{seq}", json={"text": "Updated"})
    assert edit.json()["kind"] == "edit" and edit.json()["reply_to"] == seq
    as_user(app, BOB)
    rows = (await client.get(path + "/messages")).json()["entries"]
    assert rows[0]["text"] == "Updated" and not rows[0]["from_user"]
    assert rows[0]["sender_kind"] == "human" and rows[0]["sender"] == "Alice"
    assert (
        await client.patch(f"{path}/messages/{seq}", json={"text": "Forged"})
    ).status_code == 404
    reaction = await client.put(f"{path}/messages/{seq}/reaction", json={"emoji": "👍"})
    assert reaction.json()["kind"] == "reaction"
    assert (await client.get(path + "/messages")).json()["entries"][0]["reactions"][0][
        "emoji"
    ] == "👍"
    as_user(app, ALICE)
    deleted = await client.delete(f"{path}/messages/{seq}")
    assert deleted.json()["kind"] == "tombstone"
    rows = (await client.get(path + "/messages")).json()["entries"]
    assert rows[0]["deleted_at"] and rows[0]["text"] == ""
    assert all("Updated" not in json.dumps(row) for row in rows)


async def test_tenant_membership_and_admin_boundaries(messenger):
    _, app, client = messenger
    group = await create(client)
    path = f"/api/v1/groups/{group['id']}"
    as_user(app, MALLORY)
    for method, suffix, body in [
        ("GET", "", None),
        ("GET", "/messages", None),
        ("POST", "/messages", {"text": "Intrusion"}),
        ("DELETE", "", None),
        ("GET", "/files", None),
    ]:
        response = await client.request(method, path + suffix, json=body)
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
    assert (await client.get("/api/v1/groups")).json()["items"] == []
    as_user(app, BOB)
    assert (await client.get(path)).status_code == 200
    assert (await client.patch(path, json={"name": "Taken over"})).status_code == 404
    assert (
        await client.post(path + "/members", json={"member": "user:unknown"})
    ).status_code == 404
    as_user(app, ALICE)
    assert (
        await client.post(path + "/members", json={"member": "user:unknown"})
    ).status_code == 404
    assert (
        await client.patch(path + "/members/user:alice", json={"role": "member"})
    ).status_code == 400
    assert (
        await client.post(path + "/members", json={"member": "agent:unknown"})
    ).status_code == 404


async def test_direct_ids_are_stable_and_roles_cannot_be_changed_by_recreating(messenger):
    _, app, client = messenger
    original = await create(client, kind="direct")
    as_user(app, BOB)
    repeated = await create(client, kind="direct", members=[{"member": "user:alice"}])
    assert original["id"] == repeated["id"]
    assert next(m for m in repeated["members"] if m["user_id"] == "bob")["role"] == "member"
    assert (
        await client.patch(f"/api/v1/groups/{original['id']}", json={"name": "No"})
    ).status_code == 404


async def test_cursors_unread_and_incremental_markers(messenger):
    _, app, client = messenger
    group = await create(client)
    path = f"/api/v1/groups/{group['id']}"
    sent = (await client.post(path + "/messages", json={"text": "Hi Bob"})).json()
    as_user(app, BOB)
    assert (await client.get(path)).json()["unread"] == 1
    assert (await client.post(path + "/delivered", json={"upto": sent["seq"]})).status_code == 204
    assert (await client.post(path + "/read", json={"upto": sent["seq"]})).status_code == 204
    assert (await client.post(path + "/read", json={"upto": -1})).status_code == 204
    assert (await client.post(path + "/read", json={"upto": 999})).status_code == 400
    assert (await client.get(path)).json()["unread"] == 0
    as_user(app, ALICE)
    messages = (await client.get(path + "/messages")).json()
    assert messages["read_by_all"] == sent["seq"]
    assert messages["read_by"] == {"bob": sent["seq"]}
    assert messages["delivered_by"] == {"bob": sent["seq"]}
    await client.patch(path + f"/messages/{sent['seq']}", json={"text": "Changed"})
    newer = (await client.get(path + f"/messages?after={sent['seq']}")).json()["entries"]
    assert [e["kind"] for e in newer] == ["edit"]


async def test_files_match_console_and_reject_foreign_attachments(messenger):
    _, _, client = messenger
    group = await create(client)
    path = f"/api/v1/groups/{group['id']}"
    uploaded = await client.post(
        path + "/files", files={"file": ("../../notes.txt", b"notes", "text/plain")}
    )
    assert uploaded.status_code == 200
    file = uploaded.json()
    assert file["name"] == "notes.txt" and file["size"] == 5 and ".." not in file["key"]
    assert (await client.get(path + "/files")).json()["items"][0]["key"] == file["key"]
    assert (
        await client.post(path + "/messages", json={"text": "", "attachments": [file]})
    ).status_code == 200
    file["key"] = "tenants/other/private.txt"
    assert (
        await client.post(path + "/messages", json={"text": "", "attachments": [file]})
    ).status_code == 400


async def next_event(stream, kind):
    async with asyncio.timeout(3):
        async for chunk in stream:
            if chunk.startswith(b"data:"):
                value = json.loads(chunk[5:])
                if value["type"] == kind:
                    return value
    raise AssertionError(f"No {kind} event")


async def test_feed_catches_up_after_disconnect_and_revokes_removed_members(messenger):
    container, app, client = messenger
    group = await create(client)
    chat = group["id"]
    path = f"/api/v1/groups/{chat}"
    first = (await client.post(path + "/messages", json={"text": "First"})).json()
    stream = container.get(feed).stream(BOB, FeedIn())
    try:
        assert (await next_event(stream, "ready"))["chats"] == [chat]
        assert (await next_event(stream, "entry"))["entry"]["seq"] == first["seq"]
    finally:
        await stream.aclose()
    second = (await client.post(path + "/messages", json={"text": "Second"})).json()
    stream = container.get(feed).stream(BOB, FeedIn(since={chat: first["seq"]}))
    try:
        assert (await next_event(stream, "entry"))["entry"]["seq"] == second["seq"]
        await client.delete(path + "/members/user:bob")
        assert (await next_event(stream, "chats"))["chats"] == []
    finally:
        await stream.aclose()
    stream = container.get(feed).stream(MALLORY, FeedIn(since={chat: -1}))
    try:
        assert (await next_event(stream, "ready"))["chats"] == []
    finally:
        await stream.aclose()


async def test_message_preserves_a2ui_and_shared_runtime_observers(messenger):
    container, _, client = messenger
    group = await create(client)
    service = container.get(chats)
    shown = [{"version": "v0.9", "createSurface": {"surfaceId": "plan", "catalogId": "c"}}]
    heard = []

    async def observe(change):
        heard.append(change)

    stop = container.runtime.store.channel_observe(observe)
    try:
        await container.runtime.store.channel_append(
            service._channel_name(group["id"]),
            sender=Actor("member", "trader@g"),
            text="Plan",
            kind=EntryKind.MESSAGE,
            data={"ui": shown},
        )
        entries = (await client.get(f"/api/v1/groups/{group['id']}/messages")).json()["entries"]
        assert entries[0]["ui"] == shown and entries[0]["sender_kind"] == "agent"
        assert len(heard) == 1
    finally:
        stop()


async def test_hosted_worker_wakes_a_visible_agent_and_pass_stays_silent(messenger, monkeypatch):
    from loamz.runtime.testing.scripted import ScriptedModel
    from loamz.server.agents.features.directory.agents.model import AgentIn
    from loamz.server.agents.features.directory.agents.service import agents

    container, _, client = messenger
    model = ScriptedModel(
        lambda messages: "[PASS]" if "chatter" in messages[-1].text else "Hello team"
    )
    monkeypatch.setattr(container, "chat_model", lambda *args: model)
    directory = container.get(agents)
    profile = await directory.create(ALICE, AgentIn(name="Scout", allowed_tools=[]))
    group = await create(
        client, members=[{"member": "user:bob"}, {"member": f"agent:{profile.id}", "mode": "all"}]
    )
    path = f"/api/v1/groups/{group['id']}"
    await client.post(path + "/messages", json={"text": "@Scout hello"})
    async with asyncio.timeout(10):
        while True:
            entries = (await client.get(path + "/messages")).json()["entries"]
            if len(entries) > 1:
                break
            await asyncio.sleep(0.05)
    assert entries[1]["text"] == "Hello team"
    assert entries[1]["sender_kind"] == "agent" and entries[1]["sender"] == "Scout"
    calls = model._calls
    await client.post(path + "/messages", json={"text": "@Scout chatter"})
    async with asyncio.timeout(10):
        while model._calls == calls:
            await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    entries = (await client.get(path + "/messages")).json()["entries"]
    assert len(entries) == 3 and all("[PASS]" not in e["text"] for e in entries)


async def test_new_member_does_not_receive_history_before_joining(messenger):
    _, app, client = messenger
    group = await create(client, members=[])
    path = f"/api/v1/groups/{group['id']}"
    for i in range(205):
        await client.post(path + "/messages", json={"text": f"Private history {i}"})
    await client.post(path + "/members", json={"member": "user:bob"})
    sent = (await client.post(path + "/messages", json={"text": "Welcome Bob"})).json()
    as_user(app, BOB)
    messages = (await client.get(path + "/messages")).json()["entries"]
    assert [(m["seq"], m["text"]) for m in messages] == [(sent["seq"], "Welcome Bob")]
    assert (
        await client.post(path + "/messages", json={"text": "Read old", "reply_to": 0})
    ).status_code == 404


async def test_push_is_a_content_free_wake_and_rejects_private_endpoints(messenger, monkeypatch):
    from loamz.server.agents.features.messaging.push.service import push

    container, app, client = messenger
    container.settings.push.vapid_public_key = "configured-for-test"
    container.settings.push.vapid_private_key = "configured-for-test"
    as_user(app, BOB)
    body = {
        "device": "device-bob-123456789",
        "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/test",
        "p256dh": "A" * 87,
        "auth": "B" * 22,
    }
    assert (await client.put("/api/v1/push/subscriptions", json=body)).status_code == 204
    assert (
        await client.put(
            "/api/v1/push/subscriptions", json={**body, "endpoint": "http://127.0.0.1/private"}
        )
    ).status_code == 422
    received = []

    def deliver(subscription):
        received.append(subscription)

    monkeypatch.setattr(container.get(push), "deliver", deliver)
    as_user(app, ALICE)
    group = await create(client)
    await client.post(f"/api/v1/groups/{group['id']}/messages", json={"text": "Private message"})
    async with asyncio.timeout(3):
        while not received:
            await asyncio.sleep(0.01)
    assert len(received) == 1 and "Private message" not in json.dumps(received)
    # The endpoint is a capability; knowing it must not let another account take it over.
    assert (await client.put("/api/v1/push/subscriptions", json=body)).status_code == 400


async def test_typing_is_a_hint_that_others_see_and_expires(messenger):
    container, app, client = messenger
    group = await create(client)
    chat = group["id"]
    service = container.get(feed)
    stream = service.stream(BOB, FeedIn())
    try:
        assert (await next_event(stream, "ready"))["chats"] == [chat]
        assert (await client.post(f"/api/v1/groups/{chat}/typing")).status_code == 204
        assert (await next_event(stream, "typing"))["names"] == ["Alice"]
        service._typing[(chat, "alice")] = ("Alice", 0.0)  # its few seconds are up
        assert (await next_event(stream, "typing"))["names"] == []
    finally:
        await stream.aclose()
    as_user(app, MALLORY)
    assert (await client.post(f"/api/v1/groups/{chat}/typing")).status_code == 404
    as_user(app, ALICE)
    own = service.stream(ALICE, FeedIn())
    try:
        await next_event(own, "ready")
        await client.post(f"/api/v1/groups/{chat}/typing")
        with pytest.raises(TimeoutError):
            await next_event(
                own, "typing"
            )  # nothing arrives: your own typing is never shown back to you
    finally:
        await own.aclose()


async def test_the_chat_list_names_who_spoke_last(messenger):
    _, app, client = messenger
    group = await create(client)
    path = f"/api/v1/groups/{group['id']}"
    await client.post(path + "/messages", json={"text": "hello"})
    assert (await client.get(path)).json()["last_sender"] == "You"
    as_user(app, BOB)
    assert (await client.get(path)).json()["last_sender"] == "Alice"


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_REDIS_URL"),
    reason="set TEST_REDIS_URL to run against a real Redis",
)
async def test_a_message_posted_in_one_process_reaches_a_feed_in_another_through_redis(
    tmp_path, monkeypatch
):
    import os
    import time

    from loamz.server.agents.features.messaging.feed import service as feed_service

    # Polling is made so slow that only the Redis wake-up can deliver in time.
    monkeypatch.setattr(feed_service, "POLL_SECONDS", 30)
    url = os.environ["TEST_REDIS_URL"]

    def process() -> Container:
        c = Container(Settings(database={"path": str(tmp_path / "shared.db")}, redis={"url": url}))
        c.__dict__["directory"] = Directory()
        return c

    one, two = process(), process()
    app_one, app_two = create_app(one, roles={"all"}), create_app(two, roles={"all"})
    app_one.dependency_overrides[caller_dependency] = lambda: ALICE
    async with app_one.router.lifespan_context(app_one):
        async with app_two.router.lifespan_context(app_two):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_one), base_url="http://one"
            ) as client:
                group = await create(client)
                stream = two.get(feed).stream(BOB, FeedIn())
                try:
                    assert (await next_event(stream, "ready"))["chats"] == [group["id"]]
                    started = time.monotonic()
                    await client.post(
                        f"/api/v1/groups/{group['id']}/messages", json={"text": "across processes"}
                    )
                    event = await next_event(stream, "entry")
                    assert event["entry"]["text"] == "across processes"
                    assert time.monotonic() - started < 2.5  # Redis, not the 30 s poll
                finally:
                    await stream.aclose()


async def test_an_agent_in_a_group_has_what_it_has_alone_except_what_would_need_asking(messenger):
    from loamz.server.agents.features.directory.agents.model import AgentIn
    from loamz.server.agents.features.directory.agents.service import agents
    from loamz.server.agents.features.messaging.chats.member import build_member

    container, _, client = messenger
    profile = await container.get(agents).create(
        ALICE,
        AgentIn(
            name="Scout",
            allowed_tools=[],
            abilities=["web-search", "image-generation", "weather", "data-analysis"],
        ),
    )
    group = await create(
        client, members=[{"member": "user:bob"}, {"member": f"agent:{profile.id}", "mode": "all"}]
    )
    member = await build_member(container, Actor("member", f"{profile.id}@{group['id']}"))
    names = set(member.tools.names())
    # The built-ins it was given and the date and time are there although its allow-list of tool-server tools is empty ...
    assert {"web_search", "weather", "get_current_time"} <= names
    # ... but a tool that would stop and ask (a picture costs money) is not: nobody can answer from inside a group.
    assert "image_generator" not in names
    # Skills reach it too: it is told what it knows, and may load the instructions.
    assert "use_skill" in names
    assert "Data analysis" in member._system_instructions  # noqa: SLF001


def _jpeg() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 60, 60)).save(out, "JPEG")
    return out.getvalue()


async def test_a_group_has_an_icon_or_a_photo_that_its_members_alone_can_open(messenger):
    container, app, client = messenger
    chat = (await create(client))["id"]
    # The icon and colour come from the catalog, can be changed, and are cleared with a flag.
    shown = await client.patch(
        f"/api/v1/groups/{chat}", json={"look": {"icon": "users", "color": "teal"}}
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["look"] == {"icon": "users", "color": "teal"}
    assert (
        await client.patch(
            f"/api/v1/groups/{chat}", json={"look": {"icon": "skull", "color": "teal"}}
        )
    ).status_code == 422
    assert (await client.patch(f"/api/v1/groups/{chat}", json={"name": "Eng"})).json()["look"][
        "icon"
    ] == "users"
    assert (await client.patch(f"/api/v1/groups/{chat}", json={"clear_look": True})).json()[
        "look"
    ] is None
    # A JPEG is accepted (it used to have to be a PNG) and made a square PNG; every member can read it, an outsider cannot.
    done = await client.put(
        f"/api/v1/groups/{chat}/avatar", files={"file": ("p.jpg", _jpeg(), "image/jpeg")}
    )
    key = done.json()["avatar"]
    assert key and key.endswith(".png")
    from loamz.server.agents.features.workspace.files.service import files

    def read(who):
        return container.get(files).serve_object(who, key)

    assert (await read(ALICE))[:4] == b"\x89PNG"
    assert (await read(BOB))[:4] == b"\x89PNG"
    with pytest.raises(NotFoundError):
        await read(MALLORY)
    as_user(app, ALICE)


async def test_the_picture_choices_come_from_one_json_file(messenger):
    _, _, client = messenger
    body = (await client.get("/api/v1/agents/looks")).json()
    assert {"bot", "users"} <= {i["id"] for i in body["icons"]} and all(
        c["tone"].startswith("#") for c in body["colors"]
    )
    assert "_about" not in body
