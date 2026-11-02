#!/usr/bin/env python3
"""Writes specs/agents/v1/openapi.json from the app (every feature mounted, default settings). `make spec` runs it; a test fails when the
committed file differs, so the public contract and the code cannot drift apart.

    uv run python scripts/gen_openapi.py            rewrite
    uv run python scripts/gen_openapi.py --check    exit 1 if the committed file is out of date
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.bootstrap.settings import Settings
from loamz.server.agents.http.app import create_app

PATH = Path(__file__).resolve().parents[1] / "specs" / "agents" / "v1" / "openapi.json"


def document() -> str:
    app = create_app(Container(Settings()), roles={"all"})
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    wanted = document()
    if "--check" in sys.argv:
        if not PATH.exists() or PATH.read_text() != wanted:
            sys.exit(f"{PATH.relative_to(PATH.parents[3])} is out of date: run `make spec`")
        print("openapi up to date")
    else:
        PATH.write_text(wanted)
        print(f"wrote {PATH.relative_to(PATH.parents[3])}")
