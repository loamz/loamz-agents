"""`DataGateway`: the sandbox's only way out, one Unix socket per run.

The sandbox has no network interface and no credential. Each run is given a socket whose far end is a `Channel` in this
process. The channel holds the run's token, adds it to every request it forwards, and drops any `Authorization` header the
sandbox sent, so code that prints "the secret" finds nothing. It also forwards only an allow-list of read requests and keeps
a budget of bytes returned per run, so one run cannot pull the warehouse through the socket.

    gateway = DataGateway("https://data.example.com")
    channel = await gateway.open(Path("/run/x/run-1"), lambda: minter.mint(tenant, user, run_id="run-1"))
    ...                                  # show channel.directory to the sandbox, read-only
    await gateway.close(channel)         # the socket is gone; nothing can use the run's identity any more
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import ssl
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn

logger = logging.getLogger(__name__)

UNIX_PATH_LIMIT = 100
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MAX_REQUEST_BYTES = 1 << 20  # 1 MiB max
DEFAULT_BUDGET_BYTES = 512 << 20  # what one run may receive in total
DEFAULT_BUDGET_REQUESTS = 500

_SEGMENT = r"[A-Za-z0-9_.$-]{1,128}"
ALLOWED = (
    (
        "GET",
        re.compile(
            rf"^/api/v1/(search|catalogs(/{_SEGMENT}(/schemas(/{_SEGMENT}"
            rf"(/(tables|views)(/{_SEGMENT})?)?)?)?)?)$"
        ),
    ),
    ("POST", re.compile(r"^/api/v1/statements$")),
    ("POST", re.compile(rf"^/api/v1/statements/{_SEGMENT}:cancel$")),
)

FORWARDED_REQUEST_HEADERS = {
    "content-type",
    "accept",
    "traceparent",
    "tracestate",
    "x-request-id",
}
FORWARDED_RESPONSE_HEADERS = {
    "content-type",
    "x-request-id",
    "retry-after",
    "etag",
    "location",
    "x-result-cache",
}


def _problem(status: int, kind: str, detail: str) -> tuple[int, bytes]:
    body = {
        "type": f"https://loamz.com/problems/{kind}",
        "title": kind.replace("-", " "),
        "status": status,
        "detail": detail,
    }
    return status, json.dumps(body).encode()


@dataclass(eq=False)
class Channel:
    """One run's socket, identity and budget."""

    directory: Path
    token: Callable[[], str] = field(repr=False)
    budget_bytes: int = DEFAULT_BUDGET_BYTES
    budget_requests: int = DEFAULT_BUDGET_REQUESTS
    bytes_out: int = 0
    requests: int = 0
    refused: int = 0
    server: uvicorn.Server | None = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def socket_path(self) -> Path:
        return self.directory / "api.sock"

    def stats(self) -> dict[str, int]:
        return {
            "bytes_out": self.bytes_out,
            "requests": self.requests,
            "refused": self.refused,
            "budget_bytes": self.budget_bytes,
        }


class DataGateway:
    """Opens a `Channel` per run; all channels share one pooled, verified connection to the data service."""

    def __init__(
        self,
        upstream_url: str,
        *,
        ca_file: str | None = None,
        max_connections: int = 64,
        connect_seconds: float = 10,
        read_seconds: float = 600,
        insecure_hosts: tuple[str, ...] = (),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(upstream_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("The data service URL must be http(s)://host[:port]")
        if parts.scheme == "http" and parts.hostname not in LOOPBACK | set(insecure_hosts):
            raise ValueError("Plain http is only allowed to localhost and insecure_hosts")

        self.upstream = upstream_url.rstrip("/")
        context: ssl.SSLContext | bool = False
        if parts.scheme == "https":
            context = ssl.create_default_context(cafile=ca_file or None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2

        self.client = httpx.AsyncClient(
            verify=context if parts.scheme == "https" else True,
            timeout=httpx.Timeout(connect=connect_seconds, read=read_seconds, write=30, pool=5),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=16),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self.slots = asyncio.Semaphore(max_connections)
        self.channels: set[Channel] = set()

    async def open(
        self,
        directory: Path,
        token: Callable[[], str],
        *,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        budget_requests: int = DEFAULT_BUDGET_REQUESTS,
    ) -> Channel:
        """A new socket in `directory` (created private to this process) that speaks for the identity `token()` returns."""
        channel = Channel(directory, token, budget_bytes, budget_requests)
        if len(str(channel.socket_path)) > UNIX_PATH_LIMIT:
            raise ValueError(f"Gateway path is too long for a Unix socket: {channel.socket_path}")
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        channel.socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(channel.socket_path))
        channel.socket_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        listener.listen(32)

        async def app(scope, receive, send):  # type: ignore[no-untyped-def]
            await self.forward(channel, scope, receive, send)

        config = uvicorn.Config(
            app, lifespan="off", log_level="warning", access_log=False, server_header=False
        )
        channel.server = uvicorn.Server(config)
        channel.task = asyncio.create_task(channel.server.serve(sockets=[listener]))
        while not channel.server.started and not channel.task.done():
            await asyncio.sleep(0.005)
        self.channels.add(channel)
        return channel

    async def close(self, channel: Channel) -> None:
        self.channels.discard(channel)
        if channel.server is not None:
            channel.server.should_exit = True
            if channel.task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(channel.task, 5)
            channel.server, channel.task = None, None
        channel.socket_path.unlink(missing_ok=True)

    async def stop(self) -> None:
        for channel in list(self.channels):
            await self.close(channel)
        await self.client.aclose()

    async def forward(self, channel: Channel, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        dotted = {".", ".."} & set(path.split("/"))
        if dotted or not any(m == method and rx.match(path) for m, rx in ALLOWED):
            await self._reply(
                send,
                *_problem(
                    403, "not-allowed-from-sandbox", "This request is not available from a sandbox"
                ),
            )
            return

        if channel.bytes_out >= channel.budget_bytes or channel.requests >= channel.budget_requests:
            channel.refused += 1
            await self._reply(
                send,
                *_problem(
                    429,
                    "run-data-budget-exceeded",
                    f"This run has received {channel.bytes_out >> 20} MiB of its "
                    f"{channel.budget_bytes >> 20} MiB data budget. Do the work in SQL "
                    "(GROUP BY, WHERE, LIMIT) and bring back only the answer.",
                ),
            )
            return
        channel.requests += 1

        query = scope.get("query_string", b"")
        if len(query) > 2048:
            await self._reply(send, *_problem(414, "uri-too-long", "Query string too long"))
            return

        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body += message.get("body", b"")
            if len(body) > MAX_REQUEST_BYTES:
                await self._reply(
                    send, *_problem(413, "request-too-large", "Request body too large")
                )
                return
            if not message.get("more_body"):
                break

        headers = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in scope["headers"]
            if k.decode("latin-1").lower() in FORWARDED_REQUEST_HEADERS
        }
        headers["accept-encoding"] = "identity"
        try:
            headers["authorization"] = f"Bearer {channel.token()}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("data channel could not mint a token: %s", exc)
            await self._reply(
                send, *_problem(503, "identity-unavailable", "No data identity for this run")
            )
            return

        async with self.slots:
            url = self.upstream + path + ("?" + query.decode("latin-1") if query else "")
            try:
                request = self.client.build_request(
                    method, url, headers=headers, content=body or None
                )
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException:
                await self._reply(
                    send,
                    *_problem(504, "upstream-timeout", "The data service did not answer in time"),
                )
                return
            except Exception as exc:  # noqa: BLE001
                await self._reply(
                    send, *_problem(502, "upstream-unavailable", f"Data service error: {exc}")
                )
                return

            res_headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in response.headers.items()
                if k.lower() in FORWARDED_RESPONSE_HEADERS
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": res_headers,
                }
            )
            try:
                async for chunk in response.aiter_bytes():
                    channel.bytes_out += len(chunk)
                    if channel.bytes_out > channel.budget_bytes:
                        # Headers are gone, so the stream just ends; the run's next request gets the explanation.
                        break
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            finally:
                await response.aclose()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _reply(self, send, status: int, body: bytes) -> None:  # type: ignore[no-untyped-def]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
