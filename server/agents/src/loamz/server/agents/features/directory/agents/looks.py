"""The icons and colours a picture can have, read from `looks.json`: one file to edit to change the set."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from pydantic import BaseModel, field_validator


@cache
def catalog() -> dict[str, Any]:
    return json.loads((resources.files(__package__) / "looks.json").read_text(encoding="utf-8"))


def _ids(kind: str) -> set[str]:
    return {item["id"] for item in catalog()[kind]}


class Look(BaseModel):
    """An icon on a colour, both from the catalog."""

    icon: str
    color: str

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str) -> str:
        if value not in _ids("icons"):
            raise ValueError("Unknown icon")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if value not in _ids("colors"):
            raise ValueError("Unknown colour")
        return value
