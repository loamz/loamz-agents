import httpx
import pytest

from loamz.runtime.kernel.caller import Caller
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")


@pytest.fixture
def app(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    return application


async def test_schedules_crud_and_runs(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            agent = (await client.post("/api/v1/agents", json={"name": "Inbox"})).json()
            body = {
                "agent_id": agent["id"],
                "name": "Daily Summary",
                "prompt": "Check market data and summarize",
                "kind": "recurring",
                "cron_expression": "0 8 * * *",
                "timezone": "Europe/London",
            }
            created = await client.post("/api/v1/schedules", json=body)
            assert created.status_code == 201
            task = created.json()
            assert (
                task["agent_name"] == "Inbox"
                and task["timing"] == "Every day at 08:00 (Europe/London)"
            )
            task_id = task["id"]

            listed = await client.get("/api/v1/schedules", params={"agent_id": agent["id"]})
            assert [s["id"] for s in listed.json()["items"]] == [task_id]
            assert (await client.get("/api/v1/schedules", params={"agent_id": "other"})).json()[
                "items"
            ] == []

            paused = await client.patch(f"/api/v1/schedules/{task_id}", json={"status": "paused"})
            assert paused.json()["status"] == "paused"
            assert (await client.post(f"/api/v1/schedules/{task_id}/run")).status_code == 202
            runs = await client.get(f"/api/v1/schedules/{task_id}/runs")
            assert runs.status_code == 200 and runs.json()["items"][0]["status"] == "pending"

            bad = await client.post(
                "/api/v1/schedules", json={**body, "cron_expression": "every day"}
            )
            assert bad.status_code == 400
            assert (
                await client.post("/api/v1/schedules", json={**body, "agent_id": "nobody"})
            ).status_code == 404

            assert (await client.delete(f"/api/v1/schedules/{task_id}")).status_code == 204
            assert (await client.get(f"/api/v1/schedules/{task_id}")).status_code == 404


async def test_triggers_cron_webhooks_conditions(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Cron triggers
            c_resp = await client.post(
                "/api/v1/triggers/cron",
                json={"name": "hourly_sync", "schedule": "3600", "kind": "interval"},
            )
            assert c_resp.status_code == 201

            list_cron = await client.get("/api/v1/triggers/cron")
            assert list_cron.status_code == 200
            assert len(list_cron.json()) == 1

            del_c = await client.delete("/api/v1/triggers/cron/hourly_sync")
            assert del_c.status_code == 200

            # Webhooks
            w_resp = await client.post(
                "/api/v1/triggers/webhooks",
                json={"name": "github_push", "path": "gh/push"},
            )
            assert w_resp.status_code == 201
            assert w_resp.json()["path"] == "gh/push"

            inc_resp = await client.post(
                "/api/v1/triggers/webhooks/gh/push/incoming",
                json={"ref": "refs/heads/main"},
            )
            assert inc_resp.status_code == 200

            del_w = await client.delete("/api/v1/triggers/webhooks/gh/push")
            assert del_w.status_code == 200

            # Conditions
            cond_resp = await client.post(
                "/api/v1/triggers/conditions",
                json={"name": "on_error", "event_type": "pipeline.error"},
            )
            assert cond_resp.status_code == 201

            list_cond = await client.get("/api/v1/triggers/conditions")
            assert list_cond.status_code == 200
            assert len(list_cond.json()) == 1

            del_cond = await client.delete("/api/v1/triggers/conditions/on_error")
            assert del_cond.status_code == 200


async def test_pipelines_crud_run_validate(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Save pipeline
            p_resp = await client.post(
                "/api/v1/pipelines",
                json={
                    "name": "etl_flow",
                    "description": "Extract and load data",
                    "steps": [
                        {"id": "step1", "adapter": "postgres", "action": "extract"},
                        {"id": "step2", "adapter": "storage", "action": "load"},
                    ],
                },
            )
            assert p_resp.status_code == 200

            # Get pipeline
            get_p = await client.get("/api/v1/pipelines/etl_flow")
            assert get_p.status_code == 200
            assert len(get_p.json()["steps"]) == 2

            # Validate pipeline
            val_p = await client.post("/api/v1/pipelines/etl_flow/validate")
            assert val_p.status_code == 200
            assert val_p.json()["valid"] is True

            # Run pipeline
            run_p = await client.post("/api/v1/pipelines/etl_flow/run")
            assert run_p.status_code == 200
            assert run_p.json()["success"] is True

            # Delete pipeline
            del_p = await client.delete("/api/v1/pipelines/etl_flow")
            assert del_p.status_code == 200
