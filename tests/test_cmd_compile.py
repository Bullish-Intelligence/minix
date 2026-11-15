"""`minix compile`: it regenerates the generated files and stops before the build.

Git runs for real. Nix, the compiler, the activation, and `systemctl` are fakes. Each test of "no build" reads the
record of the fake runner: a call to a command that builds or switches the home is a failure.
"""

import hashlib
import json
from pathlib import Path

import pytest
from pynix_engine import Session
from test_cmd_support import COMPILE_ERROR, Setup, initialized

from minix import commands
from minix.layer import minix_layer

INPUT_HOME = 'inputs = [Input("nirinit", "github:amaanq/nirinit/REV", modules=["nirinit"])]\n'
MOVED_HOME = 'inputs = [Input("nirinit", "github:amaanq/nirinit/v2", modules=["nirinit"])]\n'
NO_SAVE = "Nothing was built, switched, or saved."


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return initialized(tmp_path)


def compile_home(setup: Setup, **env: str) -> int:
    setup.ctx.env = {**setup.ctx.env, **env}
    return commands.compile_home(setup.ctx)


def edit_home(setup: Setup, text: str = "# an edit\n") -> None:
    with (setup.ws / "home.py").open("a") as handle:
        handle.write(text)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def home_json(setup: Setup) -> dict[str, object]:
    return json.loads((setup.ws / "home.json").read_text())


def built_or_switched(setup: Setup) -> list[tuple[str, ...]]:
    """The calls that build the home, switch to it, check its units, or touch the live system."""
    found = []
    for call in setup.fake.calls:
        if call[0] == "nix" and call[-1].endswith("activationPackage"):
            found.append(call)
        elif call[0].endswith("/activate") or call[0] in ("systemctl", "loginctl", "nix-env", "nix-store", "sudo"):
            found.append(call)
        elif call[0] == "nix" and "archive" in call:
            found.append(call)
    return found


# --- it regenerates the files -----------------------------------------------------------------------
def test_compile_writes_home_json_for_the_current_home_py(setup: Setup) -> None:
    assert compile_home(setup) == 0, setup.lines
    assert home_json(setup)["source_sha256"] == digest(setup.ws / "home.py")
    assert setup.lines == [f"compiled: home.json. {NO_SAVE}"]
    assert setup.trace() == ["compiler", "compile"]


def test_compile_after_an_edit_of_home_py_regenerates_home_json(setup: Setup) -> None:
    assert commands.apply(setup.ctx) == 0
    old = home_json(setup)["source_sha256"]
    setup.lines.clear()
    edit_home(setup)
    assert compile_home(setup) == 0, setup.lines
    assert old != home_json(setup)["source_sha256"] == digest(setup.ws / "home.py")
    assert setup.lines == [f"compiled: home.json. {NO_SAVE}"]


def test_compile_of_a_current_workspace_says_so(setup: Setup) -> None:
    assert compile_home(setup) == 0
    setup.lines.clear()
    assert compile_home(setup) == 0
    assert setup.lines == [f"compiled: the generated files are current. {NO_SAVE}"]


def test_a_new_input_rewrites_the_stub_and_the_lock(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    assert setup.lines == [f"compiled: home.json, flake.nix, flake.lock. {NO_SAVE}"]
    assert 'inputs.nirinit.url = "github:amaanq/nirinit/REV";' in (setup.ws / "flake.nix").read_text()
    assert '"nirinit"' in (setup.ws / "flake.lock").read_text()
    assert setup.trace()[:4] == ["lock", "packages", "compiler", "compile"]


def test_a_moved_input_url_locks_again(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    setup.fake.calls.clear()
    setup.lines.clear()
    (setup.ws / "home.py").write_text((setup.ws / "home.py").read_text().replace(INPUT_HOME, MOVED_HOME))
    assert compile_home(setup) == 0, setup.lines
    assert len(setup.nix_calls("flake", "lock")) == 1
    assert 'inputs.nirinit.url = "github:amaanq/nirinit/v2";' in (setup.ws / "flake.nix").read_text()
    assert setup.lines[0].startswith("compiled: home.json, flake.nix")


def test_an_unchanged_stub_does_not_lock(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    setup.fake.calls.clear()
    edit_home(setup)
    assert compile_home(setup) == 0, setup.lines
    assert setup.nix_calls("flake", "lock") == []


def test_compile_makes_the_input_packages_that_the_compiler_reads(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    packages = [c for c in setup.nix_calls("build") if c[-1] == ".#input-packages"]
    assert len(packages) == 1
    assert str(setup.ws / ".basix" / "pynix") in packages[0]


# --- it builds nothing, switches nothing, saves nothing ---------------------------------------------
def test_compile_never_builds_the_home_or_switches(setup: Setup) -> None:
    setup.world.install("home-0")
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    assert built_or_switched(setup) == []
    builds = setup.nix_calls("build")
    assert builds
    assert all(not call[-1].endswith("activationPackage") for call in builds)
    assert setup.world.activated == []
    assert setup.world.generations == 1  # the generation that the test installed
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-0")


def test_compile_never_asks_the_user_manager(setup: Setup) -> None:
    setup.world.units = "api.service loaded failed failed Api\n"
    setup.world.linger = "no"
    assert compile_home(setup) == 0, setup.lines
    assert not [call for call in setup.fake.calls if call[0] in ("systemctl", "loginctl")]
    assert not any("lingering" in line for line in setup.lines)


def test_compile_ignores_a_bad_on_fail_value(setup: Setup) -> None:
    assert compile_home(setup, MINIX_ON_FAIL="junk") == 0, setup.lines


def test_compile_makes_no_commit_and_leaves_the_files_in_the_working_copy(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert compile_home(setup) == 0, setup.lines
    assert setup.commits() == ["minix init"]
    status = setup.git("status", "--porcelain")
    for name in ("home.json", "flake.nix", "flake.lock", "home.py"):
        assert name in status
    assert not [call for call in setup.runner.git_calls if "commit" in call]


def test_compile_prints_no_store_path_and_no_git_text(setup: Setup) -> None:
    assert compile_home(setup) == 0
    text = "\n".join(setup.lines)
    assert "/nix/store" not in text
    assert str(setup.world.tmp) not in text
    assert "git " not in text


def test_apply_after_compile_saves_the_files_and_switches(setup: Setup) -> None:
    assert compile_home(setup) == 0, setup.lines
    setup.lines.clear()
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert "activate:home-1" in setup.trace()
    assert setup.commits()[0] == "apply: home.json"


# --- it refuses loudly ------------------------------------------------------------------------------
def test_a_compile_error_exits_2_and_writes_no_file(setup: Setup) -> None:
    setup.world.compile_error = COMPILE_ERROR
    assert compile_home(setup) == 2
    assert setup.lines == [COMPILE_ERROR]
    assert not (setup.ws / "home.json").exists()
    assert not (setup.ws / "home.json.new").exists()
    assert setup.commits() == ["minix init"]


def test_a_failed_compile_leaves_the_old_home_json(setup: Setup) -> None:
    assert compile_home(setup) == 0
    old = (setup.ws / "home.json").read_text()
    setup.world.compile_error = COMPILE_ERROR
    edit_home(setup)
    assert compile_home(setup) == 2
    assert (setup.ws / "home.json").read_text() == old


def test_a_stale_home_json_after_the_compile_exits_2(setup: Setup) -> None:
    setup.world.compile_stale = True
    assert compile_home(setup) == 2
    assert setup.lines == [
        f"The compile finished, but home.json does not match home.py. {NO_SAVE} Run minix compile again."
    ]


def test_a_failed_lock_exits_2_with_the_cause(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    setup.world.lock_code = 1
    assert compile_home(setup) == 2
    assert setup.lines[0].startswith(f"The inputs could not be locked. {NO_SAVE}")
    assert "cannot lock" in setup.lines[0]


def test_a_failed_compiler_build_exits_2(setup: Setup) -> None:
    setup.world.build_error = "compiler: no network"
    assert compile_home(setup) == 2
    assert setup.lines[0].startswith(f"The compiler could not be built. {NO_SAVE}")


def test_a_failed_package_build_exits_2_before_the_compiler(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    setup.world.input_packages_code = 1
    assert compile_home(setup) == 2
    assert "compiler" not in setup.trace()


def test_a_home_for_another_account_exits_2_and_puts_the_old_file_back(setup: Setup) -> None:
    assert compile_home(setup) == 0
    old = (setup.ws / "home.json").read_text()
    setup.world.compiled_user = "bob"
    edit_home(setup)
    assert compile_home(setup) == 2
    assert 'names the account "bob"' in setup.lines[-1]
    assert (setup.ws / "home.json").read_text() == old


def test_a_missing_home_py_exits_2(setup: Setup) -> None:
    (setup.ws / "home.py").unlink()
    assert compile_home(setup) == 2
    assert setup.lines == ["Found no home.py in ~/minix. Run minix init to make one."]


def test_a_refused_secret_exits_2_and_leaves_the_index(setup: Setup) -> None:
    (setup.ws / "key.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    before = setup.git("ls-files", "-s")
    assert compile_home(setup) == 2
    assert setup.lines == ["apply stopped: key.pem looks like a secret; move it out of ~/minix"]
    assert setup.git("ls-files", "-s") == before
    assert setup.nix_calls() == []


def test_compile_outside_a_workspace_exits_1(tmp_path: Path) -> None:
    from test_cmd_support import make_setup

    bare = make_setup(tmp_path)
    assert commands.compile_home(bare.ctx) == 1
    assert "is not a minix workspace" in bare.lines[0]


# --- the folder, the lock, the worktree -------------------------------------------------------------
def test_compile_takes_the_folder_of_the_dir_option(setup: Setup, tmp_path: Path) -> None:
    setup.ctx.cwd = tmp_path
    assert commands.compile_home(setup.ctx, directory=setup.ws) == 0, setup.lines
    assert (setup.ws / "home.json").is_file()


def test_compile_takes_the_same_lock_as_apply(setup: Setup) -> None:
    main = Session(
        minix_layer(setup.world.user), setup.ws, setup.runner, setup.clock, setup.lines.append, setup.ctx.env
    )
    held = main.try_lock()
    assert held is not None
    try:
        assert compile_home(setup) == 1
    finally:
        held.close()
    assert setup.lines == ["Another minix command runs in this workspace. Wait for it."]
    assert setup.nix_calls() == []


def test_compile_in_a_worktree_keeps_its_state_in_the_worktree_git_folder(setup: Setup) -> None:
    wt = setup.home / "wt"
    setup.git("worktree", "add", "-q", "--detach", str(wt))
    setup.world.ws = wt  # the fake compiler and the fake lock write here
    edit_home(setup)
    assert commands.compile_home(setup.ctx, directory=wt) == 0, setup.lines
    assert json.loads((wt / "home.json").read_text())["source_sha256"] == digest(wt / "home.py")
    git_dir = Path(setup.git("-C", str(wt), "rev-parse", "--absolute-git-dir").strip())
    assert (git_dir / "minix" / "last.log").is_file()
    assert not (setup.ws / "home.json").exists()
    assert setup.commits() == ["minix init"]


def test_compile_keeps_the_log_of_the_run(setup: Setup) -> None:
    assert compile_home(setup) == 0
    assert "$ " in setup.log()
