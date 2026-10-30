"""Errors and sites in a file that home.py imports: a helper of the workspace, or code of an input package.

An error in home.py keeps its plain `home.py:LINE:COL: text` line (see `test_config_compile.py`). An error in another file
names that file, relative to the workspace (or to `.basix/pynix` for an input package), and ends with
`(from home.py:LINE)`: the line of home.py that led there.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from minix_config import _base
from minix_config import compile as compiler

IMPORT = "from minix_config import *\n"
GIT_ERROR = 'Git.email must be an email address, for example you@example.org, found "ada".'


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """`compile.main` changes `sys.path` and `_base.ROOT`, and the helpers stay in `sys.modules`. Undo all three."""
    path, root = list(sys.path), _base.ROOT
    yield
    sys.path[:] = path
    _base.ROOT = root
    for name in [n for n in sys.modules if n.split(".")[0] in {"helpers", "comps", "pynix_gen"}]:
        del sys.modules[name]


def write(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(text)


def run(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, dict | None]:
    write(tmp_path, "home.py", text)
    code = compiler.main(["minix-compile", str(tmp_path / "home.py"), str(tmp_path / "home.json")])
    out = tmp_path / "home.json"
    return code, capsys.readouterr().err, json.loads(out.read_text()) if out.exists() else None


def fail(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> str:
    code, err, _ = run(tmp_path, text, capsys)
    assert code == 1
    return err.rstrip("\n")


def test_a_helper_that_raises_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", 'VALUE = 1\nraise ValueError("boom")\n')
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == "helpers/mymod.py:2: ValueError: boom (from home.py:2)"


def test_a_validation_error_in_a_helper_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", IMPORT + 'GIT = Git(name="Ada", email="ada")\n')
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a", programs=[mymod.GIT])\n'
    assert fail(tmp_path, text, capsys) == f"helpers/mymod.py:2:29: {GIT_ERROR} (from home.py:2)"


def test_a_helper_function_that_home_calls_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", IMPORT + 'def make():\n    return Git(name="Ada", email="ada")\n')
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a", programs=[mymod.make()])\n'
    assert fail(tmp_path, text, capsys) == f"helpers/mymod.py:3:34: {GIT_ERROR} (from home.py:3)"


def test_a_syntax_error_in_a_helper_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", "VALUE = (\n")
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == "helpers/mymod.py:1:9: SyntaxError: '(' was never closed (from home.py:2)"


def test_a_missing_name_in_a_helper_is_a_plain_import_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The helper asks minix for a name that it lacks. The text must not say that a name is "not a minix name"."""
    write(tmp_path, "helpers/mymod.py", "from minix_config import Nonesuch\n")
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == (
        "helpers/mymod.py:1: ImportError: cannot import name 'Nonesuch' from 'minix_config' (from home.py:2)"
    )


def test_a_missing_import_line_in_a_helper_names_the_helper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", "OWNER = pkgs.git\n")
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == (
        "helpers/mymod.py:1:9: `pkgs` is not imported. Add the line `from minix_config import *` at the top of "
        "helpers/mymod.py. (from home.py:2)"
    )


def test_a_name_that_minix_lacks_in_home_keeps_its_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The failing import is in home.py itself: the text of the learner is the same as before."""
    err = fail(tmp_path, "from minix_config import Nonesuch\nhome = 1\n", capsys)
    assert err.startswith("home.py:1:") and "`Nonesuch` is not a minix name." in err and "from home.py" not in err


def test_code_of_an_input_package_is_named_from_the_packages_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, ".basix/pynix/comps/desk.py", 'X = 1\nraise RuntimeError("desk")\n')
    text = IMPORT + 'from comps import desk\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == "comps/desk.py:2: RuntimeError: desk (from home.py:2)"


def test_a_generated_package_is_not_learner_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, ".basix/pynix/pynix_gen/__init__.py", 'raise RuntimeError("made")\n')
    text = IMPORT + 'import pynix_gen\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == "home.py:2: RuntimeError: made"


def test_the_site_of_a_fragment_from_a_helper_is_relative(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", IMPORT + 'GIT = Git(name="Ada", email="ada@example.org")\n')
    text = (
        IMPORT
        + 'from helpers import mymod\nhome = Home(user="a", programs=[mymod.GIT, Git(name="B", email="b@example.org")])\n'
    )
    code, err, data = run(tmp_path, text, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    assert [f["source"] for f in data["fragments"]] == ["helpers/mymod.py:2 (Git)", "home.py:3 (Git)"]


def test_a_warning_in_a_helper_names_the_helper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "helpers/mymod.py", IMPORT + 'SERVICE = Service("w", program=pkgs.python3, args="a | b")\n')
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a", services=[mymod.SERVICE])\n'
    code, err, _ = run(tmp_path, text, capsys)
    assert code == 0
    assert err.startswith("helpers/mymod.py:2:51: warning: Service.args holds `|`.")


def test_a_validation_error_in_home_is_unchanged_next_to_a_helper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "helpers/mymod.py", "VALUE = 1\n")
    text = IMPORT + 'from helpers import mymod\nhome = Home(user="a", programs=[Git(name="Ada", email="ada")])\n'
    assert fail(tmp_path, text, capsys) == f"home.py:3:55: {GIT_ERROR}"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("home.py", "home.py"),
        ("helpers/mine.py", "helpers/mine.py"),
        (".basix/pynix/comps/desk.py", "comps/desk.py"),
        (".basix/pynix/pynix_x/__init__.py", None),
        (".basix/pynix/pynix_nixpkgs_old/__init__.py", None),
        (".basix/other.py", None),
        (".devenv/state/venv/lib/python3.13/site-packages/pydantic/main.py", None),
        ("venv/lib/site-packages/pydantic/main.py", None),
        ("<frozen runpy>", None),
        ("../elsewhere/mine.py", None),
    ],
)
def test_learner_path(name: str, expected: str | None, tmp_path: Path) -> None:
    assert _base.learner_path(str(tmp_path / name) if not name.startswith("<") else name, tmp_path) == expected
