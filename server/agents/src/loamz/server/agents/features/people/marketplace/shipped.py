"""The skills Loamz ships: each is a folder under `skills/` holding a SKILL.md (a name and description, then the full instructions).

The listing the Marketplace shows comes from the file's own header, so the catalog and the instructions can never disagree. The
assistant always sees a skill's name and description; the instructions are loaded only when it uses the skill (`use_skill`).
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources

import yaml

from loamz.server.agents.features.people.marketplace.model import Listing, Permission

_READ = Permission(group="read", label="Read what you give it", default="allow")
ADDED = "2026-10-28"


@cache
def _packages() -> dict[str, tuple[dict[str, object], str]]:
    """`{id: (header, instructions)}` for every shipped skill."""
    found: dict[str, tuple[dict[str, object], str]] = {}
    root = resources.files(__package__) / "skills"
    for folder in sorted(root.iterdir(), key=lambda p: p.name):
        file = folder / "SKILL.md"
        if not folder.is_dir() or not file.is_file():
            continue
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", file.read_text(encoding="utf-8"), re.S)
        if not match:
            raise ValueError(f"{folder.name}/SKILL.md has no header")
        header = yaml.safe_load(match.group(1)) or {}
        found[str(header["name"])] = (header, match.group(2).strip())
    return found


def shipped_listings() -> tuple[Listing, ...]:
    out = []
    for skill_id, (header, _body) in _packages().items():
        meta = header.get("metadata") or {}
        out.append(
            Listing(
                id=skill_id,
                kind="skill",
                name=str(meta["title"]),
                tagline=str(meta["tagline"]),
                description=str(header["description"]),
                icon=str(meta.get("icon", "Sparkles")),
                category=str(meta.get("category", "Productivity")),
                permissions=[_READ],
                data_seen_by=["Loamz", "OpenAI"],
                added_at=ADDED,
            )
        )
    return tuple(out)


def shipped_instructions(skill_id: str) -> str | None:
    """The full instructions of a shipped skill, or None when there is no such skill."""
    found = _packages().get(skill_id)
    return None if found is None else found[1]
