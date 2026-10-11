"""The README's links work on PyPI and on GitHub (section 13)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOB = "https://github.com/entitlerhq/entitler-python/blob/main/"


def test_readme_links_are_absolute_and_name_files_in_this_repository() -> None:
    links = re.findall(r"\]\(([^)]+)\)", (ROOT / "README.md").read_text(encoding="utf-8"))
    assert links
    for link in links:
        assert link.startswith("https://"), link
        if link.startswith(BLOB):
            assert (ROOT / link.removeprefix(BLOB).split("#")[0]).exists(), link


def test_guides_link_to_files_that_exist() -> None:
    for guide in [*(ROOT / "docs").glob("*.md"), ROOT / "examples" / "README.md"]:
        for link in re.findall(r"\]\(([^)]+)\)", guide.read_text(encoding="utf-8")):
            if not link.startswith("https://"):
                assert (guide.parent / link.split("#")[0]).exists(), (guide.name, link)


def test_every_public_enum_member_has_a_docstring() -> None:
    import ast

    for path in (ROOT / "src" / "entitler").glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            if not any(ast.unparse(base) == "StrEnum" for base in node.bases):
                continue
            body = node.body
            for index, member in enumerate(body):
                if isinstance(member, ast.Assign):
                    following = body[index + 1] if index + 1 < len(body) else None
                    documented = isinstance(following, ast.Expr) and isinstance(
                        following.value, ast.Constant
                    )
                    assert documented, (path.name, node.name, ast.unparse(member.targets[0]))
