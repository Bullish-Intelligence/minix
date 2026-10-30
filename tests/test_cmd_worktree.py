"""`update`, `apply`, and `undo` in a git linked worktree, for example a gitman workspace.

The tests use real git and a fake Nix. A worktree keeps its own state in its git folder. All worktrees of one
repository share one lock, because they change one home.
"""

from pathlib import Path

import pytest
from pynix_engine import Session
from test_cmd_support import Setup, initialized, lock_json

from minix import commands
from minix.layer import minix_layer

NEW_LOCK = lock_json(nar_hash="sha256-BBBB", rev="c" * 40)
NOT_A_WORKSPACE = "is not a minix workspace"


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    setup = initialized(tmp_path, update_to=NEW_LOCK)
    setup.ctx.wall_time = lambda: 1_800_000_000.0
    return setup


def add_worktree(setup: Setup, name: str = "wt") -> Path:
    """Make a linked worktree of the workspace, with a detached HEAD as `gitman work` does."""
    folder = setup.home / name
    setup.git("worktree", "add", "-q", "--detach", str(folder))
    assert (folder / ".git").is_file()
    return folder


def test_update_in_a_worktree_writes_its_log_in_the_worktree_git_folder(setup: Setup) -> None:
    wt = add_worktree(setup)
    setup.world.ws = wt  # the fake `nix flake update` writes the new lock here
    assert commands.update(setup.ctx, directory=wt) == 0, setup.lines
    assert setup.lines[0] == "minix v0.1.0 -> v0.1.1"
    git_dir = Path(setup.git("-C", str(wt), "rev-parse", "--absolute-git-dir").strip())
    assert git_dir != setup.ws / ".git"
    assert (git_dir / "minix" / "last.log").is_file()
    assert (wt / "flake.lock").read_text() == NEW_LOCK
    assert not (setup.ws / ".git" / "minix" / "last.log").exists()


def test_update_in_a_clone_keeps_the_state_in_the_git_folder(setup: Setup) -> None:
    assert commands.update(setup.ctx) == 0, setup.lines
    assert (setup.ws / ".git" / "minix" / "last.log").is_file()
    assert (setup.ws / ".git" / "minix" / "lock").is_file()


def test_update_in_a_worktree_waits_for_a_command_in_the_main_checkout(setup: Setup) -> None:
    wt = add_worktree(setup)
    main = Session(
        minix_layer(setup.world.user), setup.ws, setup.runner, setup.clock, setup.lines.append, setup.ctx.env
    )
    held = main.try_lock()
    assert held is not None
    try:
        assert commands.update(setup.ctx, directory=wt) == 1
    finally:
        held.close()
    assert setup.lines == ["Another minix command runs in this workspace. Wait for it."]
    assert setup.nix_calls("flake", "update") == []


def test_update_in_a_worktree_says_so_when_the_git_file_is_bad(setup: Setup) -> None:
    wt = add_worktree(setup)
    (wt / ".git").write_text("gitdir: nowhere\n")
    assert commands.update(setup.ctx, directory=wt) == 1
    assert len(setup.lines) == 1
    assert NOT_A_WORKSPACE in setup.lines[0]


def test_apply_in_a_worktree_runs_and_keeps_its_state_in_the_worktree_git_folder(setup: Setup) -> None:
    wt = add_worktree(setup)
    commands.apply(setup.ctx, directory=wt)
    assert not [line for line in setup.lines if NOT_A_WORKSPACE in line], setup.lines
    assert setup.lines[-2:] == ["applied: the home runs, and its units are healthy", "saved: home.json"]
    git_dir = Path(setup.git("-C", str(wt), "rev-parse", "--absolute-git-dir").strip())
    assert (git_dir / "minix" / "last.log").is_file()
