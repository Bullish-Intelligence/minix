"""The learner word is "capability", never "component" (BSX-386).

"Component" stays the builder word. This check reads the text that a learner can see: the strings of the Python
source (docstrings, error lines, and messages, including the written `home.py`) and the Markdown files that the
learner reads. It fails on the word "component" there. The files in `BUILDER` are the builder files: they may use it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The builder files, as paths from the root of the repository, with the reason. Add a path only for builder text.
BUILDER = {
    "src/minix_config/_base.py": "the base class Component and its helpers",
}
#: Names of code that carry the word: a module and an error class. They are not text for the learner.
CODE_NAMES = re.compile(r"_components\b|ComponentError")
WORD = re.compile(r"component", re.IGNORECASE)


def strings(path: Path) -> list[tuple[int, str]]:
    """Every string of a Python file, with its line. Comments are not strings, so they do not count."""
    tree = ast.parse(path.read_text())
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def learner_text(root: Path) -> dict[Path, list[tuple[int, str]]]:
    """The learner-visible text of a repository: a list of (line, text) for each file."""
    found: dict[Path, list[tuple[int, str]]] = {}
    for path in root.glob("src/**/*.py"):
        if not path.is_symlink():
            found[path] = strings(path)
    for path in [*root.glob("template/**/*"), *root.glob("*/*/lesson.md"), root / "README.md"]:
        if path.is_file():
            found[path] = list(enumerate(path.read_text().splitlines(), 1))
    return found


def offenders(root: Path, builder: dict[str, str] = BUILDER) -> list[str]:
    """The lines with the word "component" in learner text, as `path:line: text`."""
    found: list[str] = []
    for path, items in sorted(learner_text(root).items()):
        relative = path.relative_to(root).as_posix()
        if relative in builder:
            continue
        for line, text in items:
            for part in text.splitlines():
                if WORD.search(CODE_NAMES.sub("", part)):
                    found.append(f"{relative}:{line}: {part.strip()}")
    return found


def test_learner_text_has_no_component() -> None:
    assert offenders(ROOT) == []


def test_the_check_reads_the_files_it_should() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in learner_text(ROOT)}
    assert {"src/minix_config/__init__.py", "src/minix_config/_desktop.py", "src/minix_config/entry.py"} <= names
    assert "README.md" in names
    assert set(BUILDER) <= names  # a builder path that names no file is a stale entry


def test_the_check_finds_the_word(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text('"""A Component of the home."""\nx = "ComponentError"\n')
    (tmp_path / "src" / "b.py").write_text('def f():\n    """Each component."""\n')
    (tmp_path / "README.md").write_text("A component.\n")
    assert offenders(tmp_path) == [
        "README.md:1: A component.",
        "src/a.py:1: A Component of the home.",
        "src/b.py:2: Each component.",
    ]
    assert offenders(tmp_path, {"src/a.py": "builder", "src/b.py": "builder", "README.md": "x"}) == []
