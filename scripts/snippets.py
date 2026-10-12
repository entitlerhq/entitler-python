"""Type-checks every Python snippet in README.md and docs/ with `mypy --strict`.

Each document's ```python blocks become one module, in order, after a prelude that declares the
names the guides share (`server`, `customer`, `client`, `features` and `signed_in_user_id`), so a
snippet compiles exactly as written and may use what an earlier one in its guide defined.
Framework guides import Django or Flask, which this repository does not install: those imports
type as `Any`, and the snippet's own code is still checked. Run `python scripts/snippets.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)

PRELUDE = '''\
from __future__ import annotations

from typing import Any

import entitler_features as features
from entitler import (
    AsyncEntitlerClient,
    AsyncEntitlerServer,
    AsyncServerCustomer,
    EntitlerClient,
    EntitlerServer,
    ServerCustomer,
)

server: EntitlerServer
async_server: AsyncEntitlerServer
customer: ServerCustomer
async_customer: AsyncServerCustomer
client: EntitlerClient
async_client: AsyncEntitlerClient


def signed_in_user_id(request: Any) -> str | None:
    """Stands in for the app's authentication."""
    return None


'''

CONFIG = """\
[mypy]
strict = True
python_version = 3.11
warn_unused_ignores = True
disable_error_code = no-redef

[mypy-django.*,flask.*]
ignore_missing_imports = True

[mypy-guide_django.*,guide_flask.*]
disallow_untyped_decorators = False
disallow_subclassing_any = False
disallow_any_unimported = False
"""


def documents() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def write_snippets(target: Path) -> int:
    count = 0
    for document in documents():
        blocks = BLOCK.findall(document.read_text(encoding="utf-8"))
        if not blocks:
            continue
        name = f"guide_{document.stem.replace('-', '_').lower()}"
        package = target / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        body = "".join(
            f"# {document.name}, snippet {number}\n{block}\n"
            for number, block in enumerate(blocks, start=1)
        )
        (package / "snippets.py").write_text(PRELUDE + body)
        count += len(blocks)
    return count


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        count = write_snippets(target)
        config = target / "mypy.ini"
        config.write_text(CONFIG)
        print(f"Checking {count} snippets.", flush=True)
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(config),
                "--no-incremental",
                *sorted(str(path) for path in target.glob("guide_*")),
            ],
            cwd=target,
            env={
                **os.environ,
                "MYPYPATH": str(ROOT / "examples" / "generated-features"),
            },
        )


if __name__ == "__main__":
    sys.exit(main())
