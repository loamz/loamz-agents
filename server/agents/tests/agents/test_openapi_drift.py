"""The committed public API description (specs/agents/v1/openapi.json) is what the code serves: change an endpoint, run `make spec`."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def test_the_committed_openapi_is_current() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr or done.stdout
