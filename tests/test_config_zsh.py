"""The `Zsh` object: the wire that it writes, and a home.py that names it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from nixdantic import supplied

from minix_config import Zsh
from minix_config import compile as compiler


def emitted() -> dict[str, Any]:
    """The one Home Manager value of `Zsh()`, as the wire that Home Manager reads."""
    values = list(Zsh().emit())
    assert len(values) == 1
    wire: Any = supplied(values[0])
    return wire


def test_zsh_writes_enable_completion_and_the_cached_init() -> None:
    wire = emitted()
    assert set(wire) == {"enable", "enableCompletion", "completionInit"}
    assert wire["enable"] is True
    assert wire["enableCompletion"] is True


def test_the_completion_init_is_one_cached_compinit() -> None:
    text = emitted()["completionInit"]
    assert text.count("compinit -d ") == 1
    assert 'compinit -d "$dump/zcompdump-$ZSH_VERSION"' in text
    assert 'dump="${XDG_CACHE_HOME:-$HOME/.cache}/zsh"' in text
    assert 'mkdir -p "$dump"' in text
    assert "compinit -C" not in text


def test_zsh_takes_no_argument() -> None:
    with pytest.raises(TypeError):
        Zsh(True)  # ty: ignore[too-many-positional-arguments]


def test_a_home_with_zsh_compiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "home.py").write_text(
        'from minix_config import *\nhome = Home(user="a", programs=[Zsh(), TerminalWorkspace()])\n'
    )
    code = compiler.main(["minix-compile", str(tmp_path / "home.py"), str(tmp_path / "home.json")])
    assert (code, capsys.readouterr().err) == (0, "")
    fragments = json.loads((tmp_path / "home.json").read_text())["fragments"]
    assert fragments[0]["path"] == ["programs", "zsh"]
    assert fragments[0]["value"]["enableCompletion"] is True
    assert fragments[1]["path"] == ["programs", "zellij"]
