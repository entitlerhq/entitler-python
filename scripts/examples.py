"""Checks the examples: `python scripts/examples.py check` types them, `run` runs them live.

Each example folder is its own program, so mypy checks one folder at a time. `run` needs a secret
test key in `ENTITLER_KEY` and runs every example against that environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def folders() -> list[Path]:
    return sorted(path for path in EXAMPLES.iterdir() if path.is_dir())


def call(*args: str, cwd: Path = ROOT) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call(list(args), cwd=cwd)


def check() -> int:
    failed = 0
    for folder in folders():
        failed |= call(sys.executable, "-m", "mypy", "--strict", str(folder.relative_to(ROOT)))
    failed |= call(sys.executable, "-m", "pyright", "examples")
    return failed


FASTAPI_SMOKE = """
from fastapi.testclient import TestClient
from app import app

with TestClient(app) as client:
    assert client.get("/exports/d1.pdf").status_code == 401
    assert client.post("/entitler/token").status_code == 401
print("The FastAPI app started, and refused requests with nobody signed in.")
"""


def run() -> int:
    if not os.environ.get("ENTITLER_KEY"):
        print("Set ENTITLER_KEY to a secret test key first.", file=sys.stderr)
        return 2
    failed = 0
    for folder in folders():
        if (folder / "main.py").exists():
            failed |= call(sys.executable, "main.py", cwd=folder)
    failed |= call(sys.executable, "-m", "pytest", "-q", "examples/testing")
    failed |= call(sys.executable, "-c", FASTAPI_SMOKE, cwd=EXAMPLES / "fastapi")
    return failed


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "check":
        return check()
    if command == "run":
        return run()
    print("Usage: python scripts/examples.py check|run", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
