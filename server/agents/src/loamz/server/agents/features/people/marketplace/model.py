from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["skill", "tool", "connector", "agent", "plugin"]
Group = Literal["read", "write", "delete"]
Choice = Literal["allow", "ask", "block"]
Trust = Literal["verified", "publisher", "community"]
CUSTOM_PREFIX = "custom-"
"""The start of the id of a connector a person added by its address: theirs alone, not in the catalog."""


class Permission(BaseModel):
    """What one group of an item's abilities does, and what it starts as. Writing and deleting never start as allow."""

    group: Group
    label: str
    default: Choice = "ask"


class Listing(BaseModel):
    id: str
    kind: Kind
    name: str
    tagline: str = Field(max_length=80)
    description: str
    icon: str
    category: str
    publisher: str = "Loamz"
    trust: Trust = "verified"
    permissions: list[Permission] = Field(default_factory=list)
    needs: str | None = None
    """What must be connected first, like `oauth:google`."""
    data_seen_by: list[str] = Field(default_factory=list)
    mcp_url: str | None = None
    """For a connector: the vendor-hosted MCP server it connects to."""
    publisher_url: str | None = None
    icon_url: str | None = None
    checked_on: str | None = None
    """When we last read the vendor's own documentation for this entry."""
    tools: dict[str, Group] = Field(default_factory=dict)
    """Which permission group each of the server's tools belongs to. A tool not listed here counts as `write`."""
    includes: list[str] = Field(default_factory=list)
    """For a plugin: the ids of the skills, tools and connectors it bundles. Adding it adds them together."""
    provides: list[str] = Field(default_factory=list)
    """What it lets an agent do, in plain words (`email`, `calendar`, `files`, `docs`, `tasks`, `issues`): so a template can ask for a capability and
    the person is shown every way of getting it, whatever the vendor."""
    wants: list[str] = Field(default_factory=list)
    """For an agent template: the capabilities it works best with. They are offered, not required."""
    agent_name: str = ""
    role: str = ""
    instructions: str = ""
    """For an agent template: the name, one-line job and standing instructions the New agent form is filled with."""
    version: str = "1.0"
    added_at: str
    installs: int = 0


class Catalog(BaseModel):
    items: list[Listing]
    categories: list[str]


class Install(BaseModel):
    id: str
    permissions: dict[Group, Choice] = Field(default_factory=dict)
    enabled: bool = True
    installed_at: float = 0


class InstallBody(BaseModel):
    permissions: dict[Group, Choice] = Field(default_factory=dict)


class InstallPatch(BaseModel):
    enabled: bool | None = None
    permissions: dict[Group, Choice] | None = None


class InstallList(BaseModel):
    items: list[Install]
