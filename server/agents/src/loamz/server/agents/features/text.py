"""Small text helpers features share."""

from __future__ import annotations

import re

_CARD = re.compile(r"```a2ui\b.*?(?:```|\Z)", re.DOTALL)


def one_line(text: str, limit: int) -> str:
    """A message as a one-line preview: a UI the reply drew (an `a2ui` fence) is called "[card]", spaces are collapsed, and a long
    message is cut at `limit` characters."""
    line = " ".join(_CARD.sub(" [card] ", text).split())
    return line if len(line) <= limit else line[:limit] + "…"
