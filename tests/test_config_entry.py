"""The written entry: the first `home.py` of `minix init` (009-D2, 009-D23, 009-D34)."""

from __future__ import annotations

import ast

import pytest

from minix_config.entry import STATE_VERSION, written_entry

EXPECTED = """from minix_config import *

home = Home(
    user="ada",
    state_version="26.11",
    programs=[],  # Program(pkgs.ripgrep), Git(name="Ada Lovelace", email="ada@example.org"), Neovim(default_editor=True)
    files=[],  # File(".config/demo/demo.conf", "greeting = hello\\n")
    services=[],  # Service("web", program=pkgs.python3, args="-m http.server 8080")
)
"""


def test_text() -> None:
    assert written_entry("ada") == EXPECTED
    assert STATE_VERSION == "26.11"


def test_one_import_line_with_every_learner_name() -> None:
    tree = ast.parse(written_entry("ada"))
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
    assert len(imports) == 1 and imports[0].module == "minix_config"
    assert [a.name for a in imports[0].names] == ["*"]


def test_leaves_out_the_advanced_fields() -> None:
    text = written_entry("ada")
    for word in ("options", "native_modules", "inputs", "values"):
        assert word not in text


def test_user_and_state_version_are_checked() -> None:
    with pytest.raises(ValueError, match="not a valid Home user"):
        written_entry('ada"; import os')
    with pytest.raises(ValueError, match="not a release name"):
        written_entry("ada", "26")
    assert 'state_version="26.05"' in written_entry("ada", "26.05")
