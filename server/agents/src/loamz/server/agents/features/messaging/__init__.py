"""Messaging: shared chats, delivery and organization-scoped people."""

from __future__ import annotations

from loamz.server.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("messaging.drives", "gateway"),
    feature("messaging.feed", "gateway"),
    feature("messaging.chats", "gateway"),
    feature("messaging.people", "gateway"),
    feature("messaging.push", "gateway"),
)
