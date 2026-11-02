"""Manage the people and groups of one tenant in Keycloak (organizations, users, groups) through its admin API.

A tenant is a Keycloak organization. People are the organization's members; a tenant's groups are realm groups named
`<tenant alias>:<display name>`, so tenants cannot see or change each other's groups. Every method takes the tenant and
refuses to touch a user who is not a member of it. The client authenticates as a service account (client credentials)
and keeps its token until shortly before it expires.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from loamz.core import tenancy
from loamz.engine.errors import ConflictError, NotFoundError, ServiceUnavailableError


class Directory:
    def __init__(
        self,
        url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15,
    ) -> None:
        self.realm = realm
        self.client_id, self.client_secret = client_id, client_secret
        self.http = httpx.Client(base_url=url.rstrip("/"), transport=transport, timeout=timeout)
        self.lock = threading.Lock()
        self.token: tuple[str, float] | None = None

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------------ plumbing
    def bearer(self) -> str:
        with self.lock:
            if self.token and time.time() < self.token[1] - 30:
                return self.token[0]
            response = self.http.post(
                f"/realms/{self.realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            if response.status_code >= 400:
                raise ServiceUnavailableError(
                    "The identity provider refused the directory credentials"
                )
            body = response.json()
            self.token = (body["access_token"], time.time() + int(body.get("expires_in", 60)))
            return self.token[0]

    def call(self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kwargs: Any):
        try:
            response = self.http.request(
                method,
                f"/admin/realms/{self.realm}{path}",
                headers={"Authorization": "Bearer " + self.bearer()},
                **kwargs,
            )
        except httpx.TransportError:
            raise ServiceUnavailableError("The identity provider is unreachable") from None
        if response.status_code == 404:
            raise NotFoundError("Not found in the directory")
        if response.status_code == 409:
            raise ConflictError("Already exists in the directory")
        if response.status_code not in ok:
            raise ServiceUnavailableError(f"The identity provider answered {response.status_code}")
        return response

    # ------------------------------------------------------------------ users
    def search_people(self, tenant: str, query: str = "", *, limit: int = 100) -> list[dict]:
        """Search inside the organization endpoint, never the realm-wide user directory."""
        return self.call(
            "GET",
            f"/organizations/{tenant}/members",
            params={"search": query, "first": 0, "max": limit},
        ).json()

    def invite(self, tenant: str, email: str) -> None:
        self.call("POST", f"/organizations/{tenant}/members/invite-user", data={"email": email})

    def members(
        self, tenant: str, *, first: int, count: int, username: str | None = None
    ) -> list[dict]:
        params: dict[str, Any] = {"first": first, "max": count}
        if username:
            params.update(search=username, exact="true")
        return self.call("GET", f"/organizations/{tenant}/members", params=params).json()

    def count_members(self, tenant: str) -> int:
        return int(self.call("GET", f"/organizations/{tenant}/members/count").text or 0)

    def is_member(self, tenant: str, user_id: str) -> bool:
        try:
            self.call("GET", f"/organizations/{tenant}/members/{user_id}")
        except NotFoundError:
            return False
        return True

    def user(self, tenant: str, user_id: str) -> dict[str, Any]:
        if not self.is_member(tenant, user_id):
            raise NotFoundError("User not found")
        return self.call("GET", f"/users/{user_id}").json()

    def create_user(self, tenant: str, values: dict[str, Any]) -> dict[str, Any]:
        response = self.call("POST", "/users", json={"emailVerified": True, **values})
        user_id = response.headers["location"].rsplit("/", 1)[-1]
        self.call("POST", f"/organizations/{tenant}/members", json=user_id)
        return self.call("GET", f"/users/{user_id}").json()

    def update_user(self, tenant: str, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.user(tenant, user_id)
        merged = {**current, **changes}
        self.call("PUT", f"/users/{user_id}", json=merged)
        return merged

    def remove_user(self, tenant: str, user_id: str) -> None:
        if not self.is_member(tenant, user_id):
            raise NotFoundError("User not found")
        self.call("DELETE", f"/organizations/{tenant}/members/{user_id}")

    # ------------------------------------------------------------------ groups
    @staticmethod
    def prefix(tenant: str) -> str:
        return f"{tenancy.tenant_alias(tenant)}:"

    def groups(self, tenant: str) -> list[dict[str, Any]]:
        prefix = self.prefix(tenant)
        found = self.call(
            "GET", "/groups", params={"search": prefix, "briefRepresentation": "true", "max": 1000}
        ).json()
        return [g for g in found if g["name"].startswith(prefix)]

    def group(self, tenant: str, group_id: str) -> dict[str, Any]:
        found = self.call("GET", f"/groups/{group_id}").json()
        if not found["name"].startswith(self.prefix(tenant)):
            raise NotFoundError("Group not found")
        return found

    def create_group(self, tenant: str, display_name: str) -> dict[str, Any]:
        response = self.call("POST", "/groups", json={"name": self.prefix(tenant) + display_name})
        return self.group(tenant, response.headers["location"].rsplit("/", 1)[-1])

    def rename_group(self, tenant: str, group_id: str, display_name: str) -> dict[str, Any]:
        found = self.group(tenant, group_id)
        found["name"] = self.prefix(tenant) + display_name
        self.call("PUT", f"/groups/{group_id}", json=found)
        return found

    def delete_group(self, tenant: str, group_id: str) -> None:
        self.group(tenant, group_id)
        self.call("DELETE", f"/groups/{group_id}")

    def group_members(self, tenant: str, group_id: str) -> list[dict[str, Any]]:
        self.group(tenant, group_id)
        return self.call("GET", f"/groups/{group_id}/members", params={"max": 10000}).json()

    def add_to_group(self, tenant: str, group_id: str, user_id: str) -> None:
        self.group(tenant, group_id)
        if not self.is_member(tenant, user_id):
            raise NotFoundError("User not found")
        self.call("PUT", f"/users/{user_id}/groups/{group_id}")

    def remove_from_group(self, tenant: str, group_id: str, user_id: str) -> None:
        self.group(tenant, group_id)
        self.call("DELETE", f"/users/{user_id}/groups/{group_id}")
