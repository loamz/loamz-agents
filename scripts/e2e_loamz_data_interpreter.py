#!/usr/bin/env python3
"""End-to-end check for loamz-agents running against real Loamz Data compute stack:
1. Executes a SQL query with save={volume, thread} via DataSql tool.
2. Runs Python code inside Loamz Data sandbox reading the Parquet file via Polars and committing a chart.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Final

from loamz.runtime.auth import AsUser
from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.data import DataConnection
from loamz.runtime.data.tools import DataSql
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx
from loamz.runtime.toolkit.code.runtimes.base import SandboxSpec
from loamz.runtime.toolkit.code.runtimes.loamz_data import (
    LoamzDataRuntime,
    delete_agent_volume_thread,
)

DATA_ROOT: Final[str] = "/home/ravikumar/personal/agent-framework/loamz-data"
API: Final[str] = "http://127.0.0.1:18040"
S3: Final[str] = "http://127.0.0.1:9000"
BAO: Final[str] = "http://127.0.0.1:8200"
DUCK: Final[str] = os.path.expanduser("~/.cache/loamz/modal-venv/bin/python")

work: str = tempfile.mkdtemp(prefix="loamz-agents-e2e-")
procs: list[subprocess.Popen[bytes]] = []


def stop() -> None:
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(5)
        except subprocess.TimeoutExpired, OSError:
            try:
                p.kill()
            except OSError:
                pass


def get_token(who: str) -> str:
    return subprocess.check_output(
        [sys.executable, os.path.join(DATA_ROOT, "deploy/dev/token.py"), who],
        text=True,
    ).strip()


def call(
    method: str,
    path: str,
    tok: str,
    body: dict[str, object] | None = None,
    ok: tuple[int, ...] = (200, 201, 202, 204),
) -> tuple[int, dict[str, object]]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": "Bearer " + tok,
        **({"Content-Type": "application/json"} if body is not None else {}),
    }
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw_out = r.read()
            res_obj = (
                json.loads(raw_out)
                if raw_out and r.headers.get("Content-Type", "").startswith("application/json")
                else {}
            )
            return int(r.status), res_obj
    except urllib.error.HTTPError as e:
        out = e.read()
        if e.code in ok:
            parsed = (
                json.loads(out)
                if out and e.headers.get("Content-Type", "").startswith("application/json")
                else {}
            )
            return int(e.code), parsed
        sys.exit(f"{method} {path} -> {e.code}: {out[:300]!r}")


class KeycloakBroker(CredentialBroker):
    def __init__(self, token_str: str) -> None:
        self._token = token_str

    async def credential(
        self,
        user: UserCtx | None,
        auth: Auth,
        *,
        audience: str,
        scopes: frozenset[str],
        ttl_s: int = 3600,
    ) -> str:
        return self._token


class E2ECtx:
    def __init__(self, scope: RunScope, auth: Auth) -> None:
        self.scope = scope
        self.auth = auth


async def main() -> None:
    print("=== Starting Loamz Data E2E Interpreter Check ===")

    # 1. Setup OpenBao secret
    os.makedirs(work + "/bao", exist_ok=True)
    with open(work + "/bao/token", "w") as f:
        f.write("loamz-dev-only")

    req_bao = urllib.request.Request(
        BAO + "/v1/secret/data/loamz-data/bucket",
        data=json.dumps({"data": {"key_id": "loamz-dev", "secret": "loamz-dev-only"}}).encode(),
        headers={"X-Vault-Token": "loamz-dev-only", "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req_bao).read()

    # 2. Launch Loamz Data Server
    server_env = {
        **os.environ,
        "LOAMZ_DATA_LISTEN": "127.0.0.1:18040",
        "LOAMZ_DATA_DATABASE_URL": f"sqlite:{work}/d.db?mode=rwc",
        "LOAMZ_DATA_BUCKET": "loamz-data",
        "LOAMZ_DATA_BUCKET_ENDPOINT": S3,
        "LOAMZ_DATA_BUCKET_KEY_ID_REF": "loamz-data/bucket#key_id",
        "LOAMZ_DATA_BUCKET_SECRET_REF": "loamz-data/bucket#secret",
        "LOAMZ_DATA_BUCKET_STS_ENDPOINT": S3,
        "LOAMZ_DATA_FLIGHT_LISTEN": "127.0.0.1:18041",
        "LOAMZ_DATA_STORAGE_ALLOW_PRIVATE": "true",
        "LOAMZ_DATA_STORAGE_ALLOW_HTTP_DEV": "true",
        "LOAMZ_DATA_OPENBAO_ADDR": BAO,
        "LOAMZ_DATA_OPENBAO_TOKEN_FILE": work + "/bao/token",
        "LOAMZ_DATA_OIDC_ISSUER": "http://127.0.0.1:8082/realms/loamz",
    }
    server_proc = subprocess.Popen(
        [os.path.join(DATA_ROOT, "target/release/loamz-data"), "serve"],
        env=server_env,
        stdout=open(f"{work}/server.log", "w"),
        stderr=subprocess.STDOUT,
    )
    procs.append(server_proc)

    for _ in range(100):
        try:
            urllib.request.urlopen(API + "/readyz", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    print("Loamz Data server is ready on 127.0.0.1:18040")

    ana_token = get_token("ana")
    _, me = call("GET", "/api/v1/me", ana_token)
    orgs = me.get("orgs", [])
    assert isinstance(orgs, list)
    org_id = next(str(o["id"]) for o in orgs if isinstance(o, dict) and o.get("alias") == "acme")

    call("POST", f"/api/v1/orgs/{org_id}/account", ana_token, {}, ok=(201, 409))
    call(
        "POST", f"/api/v1/orgs/{org_id}/workspaces", ana_token, {"name": "AgentsE2E"}, ok=(201, 409)
    )
    _, ws_list = call("GET", f"/api/v1/orgs/{org_id}/workspaces", ana_token)
    items = ws_list.get("items", [])
    assert isinstance(items, list)
    ws = next(str(x["id"]) for x in items if isinstance(x, dict) and x.get("name") == "AgentsE2E")
    ws_prefix = f"/api/v1/workspaces/{ws}"
    print(f"Workspace resolved: {ws}")

    # Create catalog, schema, table
    def mk_sec(kind: str, parent: str, name: str) -> str:
        code, resp = call(
            "POST",
            f"{ws_prefix}/securables",
            ana_token,
            {"kind": kind, "parent_id": parent, "name": name},
            ok=(201, 409),
        )
        if code == 201:
            return str(resp["id"])
        # Fetch existing
        _, listing = call("GET", f"{ws_prefix}/securables?parent_id={parent}", ana_token)
        for it in listing.get("items", []):  # type: ignore[union-attr]
            if it.get("name") == name:
                return str(it["id"])
        raise RuntimeError(f"Failed to find or create {kind} {name}")

    cat = mk_sec("catalog", "", "shop")
    _ = mk_sec("schema", cat, "main")

    seed = subprocess.run(
        [
            DUCK,
            os.path.join(DATA_ROOT, "scripts/iceberg_seed.py"),
            API + ws_prefix + "/iceberg",
            "shop",
            "main.orders",
            "order_id BIGINT, region VARCHAR, amount BIGINT",
        ],
        input=json.dumps(
            [
                [1, "north", 120],
                [2, "south", 80],
                [3, "north", 200],
                [4, "east", 50],
                [5, "south", 300],
            ]
        ),
        text=True,
        capture_output=True,
        env={**os.environ, "LZ_TOKEN": ana_token},
    )
    if seed.returncode != 0:
        sys.exit("Seeding iceberg table failed: " + seed.stderr)
    print("Seeded Iceberg table shop.main.orders with 5 rows")

    # Launch Runner Agent
    _, target = call(
        "POST",
        f"{ws_prefix}/compute/targets",
        ana_token,
        {"name": "agent-runner", "max_memory_mb": 2048, "max_cpu_millis": 1000},
    )
    secret_path = f"{work}/agent.secret"
    with open(secret_path, "w") as f:
        f.write(str(target["secret"]))
    os.chmod(secret_path, 0o600)

    cache = os.path.expanduser("~/.cache/loamz")
    agent_env = {
        **os.environ,
        "LOAMZ_DATA_URL": API,
        "LOAMZ_AGENT_SECRET_FILE": secret_path,
        "LOAMZ_RUNSC": f"{cache}/runsc",
        "LOAMZ_ROOTFS": f"{cache}/rootfs",
        "LOAMZ_WORK": f"{work}/agent",
        "LOAMZ_PLATFORM": "ptrace",
    }
    agent_proc = subprocess.Popen(
        [os.path.join(DATA_ROOT, "target/release/loamz-runner"), "agent"],
        env=agent_env,
        stdout=open(f"{work}/agent.log", "w"),
        stderr=subprocess.STDOUT,
    )
    procs.append(agent_proc)

    # Wait for compute to be online
    for _ in range(150):
        time.sleep(0.2)
        _, targets = call("GET", f"{ws_prefix}/compute/targets", ana_token)
        t_items = targets.get("items", [])
        if t_items and t_items[0].get("online"):  # type: ignore[union-attr]
            break
    print("Compute target connected and online")

    # 3. Test DataSql with code_backend="loamz-data"
    print("\n--- Step 1: Executing DataSql with save to agent_files ---")
    broker = KeycloakBroker(ana_token)
    ana_user = UserCtx.of("ana", "acme", attributes={"loamz.data/workspace": ws})
    conn = DataConnection(
        API,
        broker,
        workspace=ws,
        code_backend="loamz-data",
        schema="main.agents",
    )
    sql_tool = DataSql(conn)

    thread_id = "e2e_thread_01"
    scope = RunScope.from_user(ana_user, thread_id=thread_id)
    ctx = E2ECtx(scope, AsUser(ana_token))

    sql_stmt = "SELECT region, count(*) AS orders, sum(amount) AS total FROM shop.main.orders GROUP BY region ORDER BY total DESC"
    sql_res = await sql_tool.execute(ctx=ctx, call_id="tool-call-1", sql=sql_stmt)

    if sql_res.is_error:
        print("DataSql error:", sql_res.content[0].text)
        sys.exit(1)

    sql_output = json.loads(sql_res.content[0].text)
    print("DataSql Result:")
    print("  Columns:", sql_output["columns"])
    print("  Preview rows:", sql_output["rows"])
    print("  Total row count:", sql_output["row_count"])
    print("  Instruction message:", sql_output["message"])

    assert sql_output["columns"] == ["region", "orders", "total"]
    assert sql_output["row_count"] == 3
    assert 'loamz.path("agent_files"' in sql_output["message"]
    # Extract path from message
    # "The file is at <path> in the volume agent_files..."
    file_path = sql_output["message"].split("The file is at ")[1].split(" in the volume")[0]
    print(f"Parquet file saved at: {file_path}")

    # 4. Test LoamzDataRuntime reading via Polars and committing chart
    print("\n--- Step 2: Running Python in sandbox via LoamzDataRuntime ---")
    runtime = LoamzDataRuntime(
        API,
        broker,
        workspace=ws,
        schema="main.agents",
    )

    python_code = f"""
import polars as pl
import matplotlib.pyplot as plt
import loamz

# 1. Read Parquet file via Polars scan_parquet
path = loamz.path("agent_files", "{file_path}")
print("Reading parquet from:", path)
df = pl.scan_parquet(path).collect()
print("Polars DataFrame:")
print(df)

# 2. Generate and commit chart
plt.figure(figsize=(6, 4))
plt.bar(df['region'].to_list(), df['total'].to_list(), color='teal')
plt.title("Regional Order Totals")
plt.xlabel("Region")
plt.ylabel("Total Amount")
plt.tight_layout()

chart_out = loamz.out("chart.png")
plt.savefig(chart_out)
print("Committed chart to:", chart_out)
"""

    spec = SandboxSpec(
        user_id="ana",
        thread_id=thread_id,
        session_dir=f"{thread_id}/main",
        code=python_code,
        timeout_s=120,
        extra={"user": ana_user, "auth": AsUser(ana_token), "workspace": ws},
    )

    exec_res = await runtime.execute(spec)
    print("LoamzDataRuntime Execution Completed:")
    print("  Exit code:", exec_res.exit_code)
    print("  Stdout:\n" + exec_res.stdout)
    if exec_res.stderr:
        print("  Stderr:\n" + exec_res.stderr)

    assert exec_res.exit_code == 0, f"Runtime execution failed: {exec_res.stderr}"
    assert len(exec_res.output_files) == 1, (
        f"Expected 1 output file, got {len(exec_res.output_files)}"
    )
    chart_file = exec_res.output_files[0]
    print("Output file returned:")
    print("  Name:", chart_file["name"])
    print("  Path:", chart_file["path"])
    print("  Size:", chart_file["size"], "bytes")
    print("  Mime:", chart_file["mime_type"])
    assert chart_file["name"] == "chart.png"
    assert chart_file["mime_type"] == "image/png"
    assert chart_file.get("content_base64")
    assert chart_file.get("data_uri", "").startswith("data:image/png;base64,")

    # 5. Delete thread in volume
    print("\n--- Step 3: Cleaning up conversation thread in volume ---")
    await delete_agent_volume_thread(
        API,
        ws,
        ana_user.subject,
        ana_token,
        thread_id,
        schema_spec="main.agents",
    )
    print("Thread folder deleted successfully")

    print("\n=== Loamz Data E2E Interpreter Check PASSED ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        stop()
