"""Marketplace listings for the registry products (see bootstrap/products.py): one tool listing per product that serves tools.

What is said about the product comes from its tools manifest (`listing`: tagline, description, category, icon); what it may do comes from
the registry and the person's choices, never from the manifest alone. Nothing here names a product."""

from __future__ import annotations

from loamz.server.agents.bootstrap.products import ProductEntry, ProductTools
from loamz.server.agents.features.people.marketplace.model import Group, Listing, Permission

LABELS: dict[str, str] = {"read": "Look things up", "write": "Make changes"}


def listing_of(entry: ProductEntry) -> Listing:
    text = entry.manifest.listing
    tools = {t.name: t.group for t in entry.tools}
    reads = [t for t in entry.tools if t.group == "read"]
    groups: dict[str, Group] = {t.group: t.group for t in entry.tools}  # type: ignore[misc]
    permissions = [
        Permission(
            group=group,
            label=LABELS[group],
            # Reading starts allowed only when the operator marked every reading tool safe; changing never does.
            default="allow"
            if group == "read" and all(t.remote_name in entry.endpoint.safe_tools for t in reads)
            else "ask",
        )
        for group in ("read", "write")
        if group in groups
    ]
    return Listing(
        id=entry.endpoint.id,
        kind="tool",
        name=entry.name,
        tagline=str(text.get("tagline") or f"Use {entry.name} from a chat")[:80],
        description=str(
            text.get("description") or f"Lets the assistant use {entry.name}, acting as you."
        ),
        icon=str(text.get("icon") or "Plug"),
        category=str(text.get("category") or "Loamz products"),
        permissions=permissions,
        data_seen_by=["Loamz", entry.name],
        tools=tools,
        added_at=str(text.get("added_at") or "2026-11-02"),
    )


def product_listings(products: ProductTools) -> list[Listing]:
    return [listing_of(e) for e in products.entries.values()]
