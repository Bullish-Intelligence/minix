"""`minix undo` (L25): a dirty tree goes back to the last save; a clean tree goes back one save, and it commits."""

from pathlib import Path

import pytest
from test_cmd_support import Setup, initialized

from minix import commands


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return initialized(tmp_path)


def apply(setup: Setup) -> int:
    return commands.apply(setup.ctx)


def edit(setup: Setup, text: str) -> None:
    (setup.ws / "home.py").write_text(text)


def test_undo_of_a_dirty_tree_drops_the_edit_and_commits_nothing(setup: Setup) -> None:
    assert apply(setup) == 0
    saved = (setup.ws / "home.py").read_text()
    edit(setup, "home = 'edited'\n")
    setup.lines.clear()
    assert commands.undo(setup.ctx) == 0
    assert (setup.ws / "home.py").read_text() == saved
    assert setup.commits() == ["apply: home.json", "minix init"]
    assert setup.lines[0] == "undo: dropped the edit that minix did not apply (a copy stays in the workspace history)"
    assert setup.git("status", "--porcelain") == ""


def test_undo_of_a_dirty_tree_keeps_a_copy_of_the_edit(setup: Setup) -> None:
    assert apply(setup) == 0
    edit(setup, "home = 'edited'\n")
    assert commands.undo(setup.ctx) == 0
    dropped = setup.git("rev-parse", "refs/minix/dropped").strip()
    assert "home = 'edited'" in setup.git("show", f"{dropped}:home.py")


def test_undo_of_a_clean_tree_restores_the_files_of_the_save_before(setup: Setup) -> None:
    assert apply(setup) == 0
    edit(setup, "home = 'second'\n")
    setup.world.next_home = "home-2"
    assert apply(setup) == 0
    first = setup.git("rev-parse", "--short", "HEAD~1").strip()
    setup.lines.clear()
    setup.world.next_home = "home-1"
    assert commands.undo(setup.ctx) == 0, setup.lines
    assert "home = 'second'" not in (setup.ws / "home.py").read_text()
    assert setup.commits()[0] == f"undo to {first}"
    assert setup.lines[0] == "undo: restoring the files of the save before the last one"
    assert setup.lines[-1] == "saved: the files of the earlier save are back"
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-1")


def test_a_second_undo_goes_back_again(setup: Setup) -> None:
    assert apply(setup) == 0
    edit(setup, "home = 'second'\n")
    setup.world.next_home = "home-2"
    assert apply(setup) == 0
    setup.world.next_home = "home-1"
    assert commands.undo(setup.ctx) == 0
    setup.world.next_home = "home-2"
    assert commands.undo(setup.ctx) == 0
    assert "home = 'second'" in (setup.ws / "home.py").read_text()


def test_undo_with_one_save_only_refuses(setup: Setup) -> None:
    assert commands.undo(setup.ctx) == 1
    assert setup.lines == ["There is only one save. There is nothing to undo."]
    assert setup.trace() == []
    assert setup.commits() == ["minix init"]


def test_undo_applies_the_restored_files(setup: Setup) -> None:
    assert apply(setup) == 0
    setup.world.install("home-9")  # a later generation is running
    edit(setup, "home = 'edited'\n")
    assert commands.undo(setup.ctx) == 0
    assert "activate:home-1" in setup.trace()  # the home of the last save is built and activated again
