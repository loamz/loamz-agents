"""LoamzDataRuntime — execute code inside Loamz Data's governed compute sandbox."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
from typing import TypedDict

import httpx

from loamz.runtime.auth.types import CredentialBroker
from loamz.runtime.kernel.types.auth import Auth
from loamz.runtime.kernel.types.user import UserCtx

from .base import Capability, ExecResult, SandboxSpec, is_display_artifact

logger = logging.getLogger(__name__)

VOLUME_NAME = "agent_files"
DEFAULT_SCHEMA = "main.agents"
MAX_POLL_INTERVAL = 2.0
INITIAL_POLL_INTERVAL = 0.3
POLL_BACKOFF_FACTOR = 1.5
BUSY_RETRY_DEADLINE_S = 30.0

_volume_cache: dict[tuple[str, str, str], str] = {}


class CommittedFile(TypedDict):
    path: str
    bytes: int


class RunResult(TypedDict, total=False):
    committed: list[CommittedFile]
    failed: list[str]


class RunData(TypedDict, total=False):
    id: str
    state: str
    stdout: str | None
    stderr: str | None
    error: str | None
    result: RunResult | str | None


class SecurableItem(TypedDict):
    id: str
    name: str
    kind: str


class OutputFileEntry(TypedDict, total=False):
    name: str
    path: str
    size: int
    mime_type: str
    type: str
    content_base64: str
    data_uri: str


def safe_thread_id(thread_id: str) -> str:
    """Make thread_id safe for Loamz Data thread parameter:
    Letters, digits, dash, underscore, at most 64 chars."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_id)
    return cleaned[:64] or "default"


async def get_or_create_agent_volume(
    base_url: str,
    ws: str,
    user_key: str,
    token: str,
    *,
    schema_spec: str = DEFAULT_SCHEMA,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Resolve or create the person's agent_files volume in the configured schema."""
    cache_key = (base_url.rstrip("/"), ws, user_key)
    cached_id = _volume_cache.get(cache_key)
    if cached_id:
        return cached_id

    if "." in schema_spec:
        catalog_name, schema_name = schema_spec.split(".", 1)
    else:
        catalog_name, schema_name = None, schema_spec

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
        # 1. Resolve Catalog
        cat_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id=",
            headers=headers,
        )
        cat_resp.raise_for_status()
        catalogs: list[SecurableItem] = cat_resp.json().get("items", [])
        catalog_id: str | None = None
        for cat in catalogs:
            if cat.get("kind") == "catalog" and (
                catalog_name is None or cat.get("name") == catalog_name
            ):
                catalog_id = cat.get("id")
                break

        if not catalog_id:
            if catalogs:
                catalog_id = catalogs[0].get("id")
            else:
                raise RuntimeError(f"No catalog found in Loamz Data workspace {ws}")

        # 2. Resolve Schema
        sch_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={catalog_id}",
            headers=headers,
        )
        sch_resp.raise_for_status()
        schemas: list[SecurableItem] = sch_resp.json().get("items", [])
        schema_id: str | None = None
        for sch in schemas:
            if sch.get("kind") == "schema" and sch.get("name") == schema_name:
                schema_id = sch.get("id")
                break

        if not schema_id:
            create_sch = await client.post(
                f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables",
                json={"kind": "schema", "parent_id": catalog_id, "name": schema_name},
                headers=headers,
            )
            if create_sch.status_code == 201:
                schema_id = create_sch.json().get("id")
            else:
                sch_resp2 = await client.get(
                    f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={catalog_id}",
                    headers=headers,
                )
                for sch in sch_resp2.json().get("items", []):
                    if sch.get("kind") == "schema" and sch.get("name") == schema_name:
                        schema_id = sch.get("id")
                        break

        if not schema_id:
            raise RuntimeError(f"Could not find or create schema {schema_name}")

        # 3. Resolve Volume (agent_files)
        vol_resp = await client.get(
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={schema_id}",
            headers=headers,
        )
        vol_resp.raise_for_status()
        volumes: list[SecurableItem] = vol_resp.json().get("items", [])
        volume_id: str | None = None
        for vol in volumes:
            if vol.get("kind") == "volume" and vol.get("name") == VOLUME_NAME:
                volume_id = vol.get("id")
                break

        if not volume_id:
            create_vol = await client.post(
                f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables",
                json={"kind": "volume", "parent_id": schema_id, "name": VOLUME_NAME},
                headers=headers,
            )
            if create_vol.status_code == 201:
                volume_id = create_vol.json().get("id")
            else:
                vol_resp2 = await client.get(
                    f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/securables?parent_id={schema_id}",
                    headers=headers,
                )
                for vol in vol_resp2.json().get("items", []):
                    if vol.get("kind") == "volume" and vol.get("name") == VOLUME_NAME:
                        volume_id = vol.get("id")
                        break

        if not volume_id:
            raise RuntimeError(f"Could not find or create volume {VOLUME_NAME}")

        _volume_cache[cache_key] = volume_id
        return volume_id


async def delete_agent_volume_thread(
    base_url: str,
    ws: str,
    user_key: str,
    token: str,
    thread_id: str,
    *,
    schema_spec: str = DEFAULT_SCHEMA,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Delete a thread's folder in the person's agent_files volume."""
    safe_thread = safe_thread_id(thread_id)
    try:
        vol_id = await get_or_create_agent_volume(
            base_url, ws, user_key, token, schema_spec=schema_spec, transport=transport
        )
    except Exception as exc:
        logger.warning("Could not resolve volume for thread deletion: %s", exc)
        return

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
        url = (
            f"{base_url.rstrip('/')}/api/v1/workspaces/{ws}/volumes/{vol_id}/threads/{safe_thread}"
        )
        resp = await client.delete(url, headers=headers)
        if resp.status_code not in (204, 404):
            logger.warning("Delete thread returned status %d: %s", resp.status_code, resp.text)


class LoamzDataRuntime:
    """A SandboxRuntime implementation backed by Loamz Data's compute sandbox."""

    name: str = "loamz-data"
    capabilities: frozenset[Capability] = frozenset()

    def __init__(
        self,
        url: str,
        broker: CredentialBroker | None,
        *,
        schema: str = DEFAULT_SCHEMA,
        workspace: str | None = None,
        audience: str = "loamz-data",
        timeout_seconds: int = 60,
        memory_mb: int = 2048,
        cpu_millis: int = 1000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._broker = broker
        self._schema = schema
        self._workspace = workspace
        self._audience = audience
        self._timeout_seconds = timeout_seconds
        self._memory_mb = memory_mb
        self._cpu_millis = cpu_millis
        self._transport = transport

    @property
    def workspace(self) -> str | None:
        return self._workspace

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        if spec.code:
            script = spec.code
        elif spec.argv:
            script = f"import subprocess, sys\nsys.exit(subprocess.run({spec.argv!r}).returncode)"
        else:
            return ExecResult(stdout="", stderr="No code or command provided.", exit_code=1)

        auth: Auth | None = spec.extra.get("auth")
        user: UserCtx | None = spec.extra.get("user")
        ws = spec.extra.get("workspace") or self._workspace
        if not ws:
            return ExecResult(
                stdout="",
                stderr="No Loamz Data workspace configured for this run.",
                exit_code=1,
            )

        token = ""
        if self._broker is not None and auth is not None:
            token = await self._broker.credential(
                user,
                auth,
                audience=self._audience,
                scopes=frozenset(),
                ttl_s=max(spec.timeout_s + 60, 120),
            )
        elif auth is not None and hasattr(auth, "token") and auth.token:
            token = str(auth.token)
        elif spec.extra.get("token"):
            token = str(spec.extra["token"])

        if not token:
            return ExecResult(
                stdout="",
                stderr="LoamzDataRuntime needs a signed-in person with auth.",
                exit_code=1,
            )

        user_key = user.subject if user else (spec.user_id or "user")
        try:
            volume_id = await get_or_create_agent_volume(
                self._url,
                ws,
                user_key,
                token,
                schema_spec=self._schema,
                transport=self._transport,
            )
        except Exception as exc:
            logger.error("Failed to resolve or create agent volume: %s", exc)
            return ExecResult(
                stdout="", stderr=f"Failed to access agent volume: {exc}", exit_code=1
            )

        safe_thread = safe_thread_id(spec.thread_id)
        job_body: dict[str, object] = {
            "script": script,
            "reads": [volume_id],
            "commit": {
                "volume": volume_id,
                "thread": safe_thread,
            },
            "timeout_s": spec.timeout_s or self._timeout_seconds,
            "memory_mb": self._memory_mb,
            "cpu_millis": self._cpu_millis,
        }

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(
            transport=self._transport, timeout=max(spec.timeout_s + 45.0, 120.0)
        ) as client:
            # POST /runs with 409 retry for up to 30s
            deadline = time.monotonic() + BUSY_RETRY_DEADLINE_S
            resp: httpx.Response | None = None
            while True:
                resp = await client.post(
                    f"{self._url}/api/v1/workspaces/{ws}/runs",
                    json=job_body,
                    headers=headers,
                )
                if resp.status_code == 409:
                    if time.monotonic() >= deadline:
                        return ExecResult(
                            stdout="",
                            stderr="Thread is busy with another active job (409 Conflict).",
                            exit_code=1,
                        )
                    await asyncio.sleep(1.0)
                    continue
                break

            if resp is None or resp.status_code != 202:
                status = resp.status_code if resp is not None else 500
                detail = resp.text if resp is not None else "No response"
                try:
                    if resp is not None:
                        detail = resp.json().get("detail", detail)
                except Exception:
                    pass
                return ExecResult(
                    stdout="",
                    stderr=f"Failed to submit run ({status}): {detail}",
                    exit_code=1,
                )

            run_info: RunData = resp.json()
            run_id = run_info.get("id", "")
            if not run_id:
                return ExecResult(
                    stdout="", stderr="Run submitted but no run id returned.", exit_code=1
                )

            # Poll GET /runs/{id}
            poll_interval = INITIAL_POLL_INTERVAL
            while True:
                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * POLL_BACKOFF_FACTOR, MAX_POLL_INTERVAL)
                poll_resp = await client.get(
                    f"{self._url}/api/v1/workspaces/{ws}/runs/{run_id}",
                    headers=headers,
                )
                if poll_resp.status_code == 200:
                    run_info = poll_resp.json()
                    state = str(run_info.get("state", ""))
                    if state not in ("queued", "running"):
                        break
                elif poll_resp.status_code == 404:
                    return ExecResult(
                        stdout="",
                        stderr=f"Run {run_id} not found during polling.",
                        exit_code=1,
                    )

            state = str(run_info.get("state", ""))
            stdout = str(run_info.get("stdout") or "")
            stderr = str(run_info.get("stderr") or "")
            error = str(run_info.get("error") or "")

            if state == "timed_out":
                timeout_err = f"Run timed out after {job_body.get('timeout_s')}s"
                error = error or timeout_err

            if error and error not in stderr:
                stderr = f"{stderr}\n{error}".strip() if stderr else error

            exit_code = 0 if state == "succeeded" else 1

            # Parse results
            raw_result = run_info.get("result")
            result_obj: RunResult = {}
            if isinstance(raw_result, str):
                try:
                    result_obj = json.loads(raw_result)
                except Exception:
                    result_obj = {}
            elif isinstance(raw_result, dict):
                result_obj = raw_result

            committed = result_obj.get("committed", [])
            failed = result_obj.get("failed", [])
            if failed:
                fail_msg = f"Failed to commit files: {', '.join(str(f) for f in failed)}"
                stderr = f"{stderr}\n{fail_msg}".strip() if stderr else fail_msg
                exit_code = 1

            output_files: list[dict[str, object]] = []
            for item in committed:
                file_path = str(item.get("path", ""))
                filename = file_path.rsplit("/", 1)[-1]
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                is_viewable = (
                    mime.startswith("image/")
                    or ext in ("png", "jpg", "jpeg", "gif", "svg", "webp", "csv", "xlsx", "xls")
                    or is_display_artifact(mime)
                )
                if is_viewable:
                    f_resp = await client.get(
                        f"{self._url}/api/v1/workspaces/{ws}/volumes/{volume_id}/files/{file_path}",
                        headers=headers,
                    )
                    if f_resp.status_code == 200:
                        file_bytes = f_resp.content
                        b64 = base64.b64encode(file_bytes).decode("ascii")
                        prefix = f"threads/{safe_thread}/runs/{run_id}/"
                        name = (
                            file_path[len(prefix) :] if file_path.startswith(prefix) else filename
                        )
                        entry: OutputFileEntry = {
                            "name": name,
                            "path": file_path,
                            "size": len(file_bytes),
                            "mime_type": mime,
                            "type": "file",
                            "content_base64": b64,
                        }
                        if is_display_artifact(mime):
                            entry["data_uri"] = f"data:{mime};base64,{b64}"
                        output_files.append(entry)

            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                output_files=output_files,
            )

    async def stop(self) -> None:
        pass


__all__ = [
    "DEFAULT_SCHEMA",
    "LoamzDataRuntime",
    "VOLUME_NAME",
    "delete_agent_volume_thread",
    "get_or_create_agent_volume",
    "safe_thread_id",
]
