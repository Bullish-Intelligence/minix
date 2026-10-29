"""`minix apply`: the order of the steps, the plain lines, the rollback, and the refusals (CONCEPT 4.1, S16)."""

import json
import subprocess
from pathlib import Path

import pytest
from test_cmd_support import (
    BAD_UNITS,
    COMPILE_ERROR,
    GOOD_UNITS,
    LOOPING_UNITS,
    NIX,
    Setup,
    initialized,
)

from minix import commands

FIRST_APPLY = ["compiler", "compile", "archive", "root", "build", "reset", "activate:home-1", "health"]


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return initialized(tmp_path)


def apply(setup: Setup, **env: str) -> int:
    setup.ctx.env = {**setup.ctx.env, **env}
    return commands.apply(setup.ctx)


def edit_home(setup: Setup, text: str = "# an edit\n") -> None:
    with (setup.ws / "home.py").open("a") as handle:
        handle.write(text)


def test_first_apply_runs_the_steps_in_order_and_saves_the_files(setup: Setup) -> None:
    assert apply(setup) == 0, setup.lines
    assert setup.trace() == FIRST_APPLY
    assert setup.lines == [
        "checked: home.py",
        "building the home",
        "built the home",
        "checking the units for 8 s",
        "applied: the home runs, and its units are healthy",
        "saved: home.json",
    ]
    assert setup.commits() == ["apply: home.json", "minix init"]
    assert setup.git("status", "--porcelain") == ""
    assert json.loads((setup.ws / "home.json").read_text())["user"] == "ada"


def test_apply_prints_no_store_path_and_no_git_text(setup: Setup) -> None:
    assert apply(setup) == 0
    text = "\n".join(setup.lines)
    assert "/nix/store" not in text
    assert str(setup.world.tmp) not in text
    assert "git " not in text


def test_apply_runs_no_sudo(setup: Setup) -> None:
    assert apply(setup) == 0
    assert not [call for call in setup.fake.calls if "sudo" in call or Path(call[0]).name == "sudo"]


def test_every_nix_call_carries_the_features(setup: Setup) -> None:
    assert apply(setup) == 0
    nix_calls = [call for call in setup.fake.calls if call[0] == "nix"]
    assert nix_calls
    assert all(call[:3] == NIX for call in nix_calls)


def test_apply_builds_the_compiler_from_the_workspace_lock_with_a_root(setup: Setup) -> None:
    assert apply(setup) == 0
    call = setup.nix_calls("build", "--out-link")[0]
    assert call[len(NIX) + 2] == str(setup.ws / ".git" / "minix" / "compiler")
    assert call[-1] == ".#compiler"


def test_apply_builds_one_activation_package_and_roots_the_result(setup: Setup) -> None:
    assert apply(setup) == 0
    builds = [c for c in setup.nix_calls("build") if c[-1].endswith("activationPackage")]
    assert len(builds) == 1
    assert builds[0][-1] == ".#homeConfigurations.ada.activationPackage"
    assert str(setup.ws / ".git" / "minix" / "result") in builds[0]


def test_apply_roots_the_flake_inputs(setup: Setup) -> None:
    assert apply(setup) == 0
    roots = [c for c in setup.fake.calls if c[0] == "nix-store"]
    assert roots == [
        (
            "nix-store",
            "--realise",
            "--indirect",
            "--add-root",
            str(setup.ws / ".git" / "minix" / "inputs"),
            str(setup.world.tmp / "source"),
        )
    ]


def test_apply_keeps_the_log_of_the_run(setup: Setup) -> None:
    assert apply(setup) == 0
    log = setup.log()
    assert "$ " in log
    assert "activate" in log


# --- nothing to change ------------------------------------------------------------------------------
def test_apply_with_the_running_home_says_nothing_to_change(setup: Setup) -> None:
    assert apply(setup) == 0
    setup.lines.clear()
    setup.fake.calls.clear()
    assert apply(setup) == 0
    assert setup.lines[-1] == "nothing to change"
    assert not [t for t in setup.trace() if t.startswith("activate")]
    assert setup.commits() == ["apply: home.json", "minix init"]


def test_apply_with_only_a_file_change_saves_it_and_skips_the_activation(setup: Setup) -> None:
    assert apply(setup) == 0
    setup.lines.clear()
    setup.fake.calls.clear()
    edit_home(setup)
    assert apply(setup) == 0
    assert "the home is the same; only files changed" in setup.lines
    assert not [t for t in setup.trace() if t.startswith("activate")]
    assert setup.commits()[0] == "apply: home.py"


def test_apply_with_a_new_generation_activates_it(setup: Setup) -> None:
    setup.world.install("home-0")
    assert apply(setup) == 0
    assert "activate:home-1" in setup.trace()
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-1")


# --- errors before any change -----------------------------------------------------------------------
def test_a_compile_error_prints_the_line_and_stops_before_the_build(setup: Setup) -> None:
    setup.world.compile_error = COMPILE_ERROR
    assert apply(setup) == 2
    assert setup.lines == [COMPILE_ERROR]
    assert setup.trace() == ["compiler", "compile"]
    assert not (setup.ws / "home.json").exists()
    assert not (setup.ws / "home.json.new").exists()
    assert setup.commits() == ["minix init"]


def test_a_missing_home_py_stops_with_one_line(setup: Setup) -> None:
    (setup.ws / "home.py").unlink()
    assert apply(setup) == 2
    assert setup.lines == ["Found no home.py in ~/minix. Run minix init to make one."]


def test_a_syntax_error_in_an_input_call_stops_with_one_line(setup: Setup) -> None:
    (setup.ws / "home.py").write_text('inputs = [Input("a", url_from_somewhere())]\n')
    assert apply(setup) == 2
    assert setup.lines[0].startswith("home.py:1:")
    assert "takes plain text and True or False only" in setup.lines[0]
    assert setup.fake.calls == [("loginctl", "show-user", "ada", "--property=Linger", "--value")]


def test_a_failed_compiler_build_stops_with_the_cause(setup: Setup) -> None:
    setup.world.build_error = "compiler: no network"
    assert apply(setup) == 2
    assert setup.lines[0].startswith("The compiler could not be built. Nothing was built, switched, or saved.")
    assert setup.commits() == ["minix init"]


def test_a_failed_home_build_stops_before_the_switch(setup: Setup) -> None:
    setup.world.build_error = "builder for 'home-manager-generation.drv' failed"
    assert apply(setup) == 2
    assert setup.lines[-1].endswith("(full log: .git/minix/last.log)")
    assert "The build failed. Nothing was switched or saved. The home did not change." in "\n".join(setup.lines)
    assert not [t for t in setup.trace() if t.startswith("activate")]


# --- the account ------------------------------------------------------------------------------------
def test_apply_refuses_a_home_for_another_account(setup: Setup) -> None:
    setup.world.compiled_user = "bob"
    assert apply(setup) == 2
    assert setup.lines == [
        'home.py names the account "bob", but you are "ada". '
        'minix changes only your own home. Set user="ada" in home.py.'
    ]
    assert [t for t in setup.trace() if t == "build"] == []
    assert not (setup.ws / "home.json").exists()


def test_the_refusal_puts_the_old_home_json_back(setup: Setup) -> None:
    assert apply(setup) == 0
    old = (setup.ws / "home.json").read_text()
    setup.world.compiled_user = "bob"
    edit_home(setup)
    assert apply(setup) == 2
    assert (setup.ws / "home.json").read_text() == old


# --- the stage guards ---------------------------------------------------------------------------------
def test_a_refused_secret_prints_one_line_and_leaves_the_index_as_it_was(setup: Setup) -> None:
    (setup.ws / "key.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    (setup.ws / "notes.txt").write_text("a plain new file\n")
    before = setup.git("ls-files", "-s")
    assert apply(setup) == 2
    assert setup.lines == ["apply stopped: key.pem looks like a secret; move it out of ~/minix"]
    assert setup.git("ls-files", "-s") == before
    assert setup.git("diff", "--cached", "--name-only") == ""
    assert setup.commits() == ["minix init"]


# --- the other layer and the folder -----------------------------------------------------------------
def test_a_basix_workspace_names_basix_apply(setup: Setup, tmp_path: Path) -> None:
    basix = setup.home / "basix"
    (basix / ".git").mkdir(parents=True)
    (basix / "system.py").write_text("machine = 1\n")
    setup.ctx.cwd = basix
    assert apply(setup) == 1
    assert setup.lines == [
        "~/basix is a Basix workspace. minix does not change it.\n"
        "To change the machine, run: basix apply\n"
        "To change your own home, go to ~/minix and run: minix apply"
    ]
    assert setup.fake.calls == []


def test_apply_outside_a_workspace_goes_to_the_default_folder(setup: Setup) -> None:
    setup.ctx.cwd = setup.home
    assert apply(setup) == 0
    assert setup.commits()[0] == "apply: home.json"


def test_apply_outside_a_workspace_goes_to_the_folder_of_the_variable(setup: Setup, tmp_path: Path) -> None:
    kept = tmp_path / "kept"
    setup.ws.rename(kept)  # the workspace is not ~/minix any more
    setup.ctx.cwd = setup.home
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": str(kept)}
    assert apply(setup) == 0
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=kept, env=setup.runner.env, capture_output=True, text=True, check=True
    )
    assert log.stdout.splitlines()[0] == "apply: home.json"


def test_apply_without_a_workspace_says_where_to_go(tmp_path: Path) -> None:
    from test_cmd_support import make_setup

    other = make_setup(tmp_path)
    assert commands.apply(other.ctx) == 1
    assert other.lines == ["~ is not a minix workspace. Go to ~/minix first, or set MINIX_WORKSPACE to its folder."]


def test_apply_finds_the_workspace_from_a_subfolder(setup: Setup) -> None:
    sub = setup.ws / "notes"
    sub.mkdir()
    setup.ctx.cwd = sub
    assert apply(setup) == 0
    assert setup.commits()[0] == "apply: home.json"


# --- a failed service -------------------------------------------------------------------------------
def test_a_failed_service_rolls_back_to_the_last_good_home(setup: Setup) -> None:
    earlier = setup.world.install("home-0")
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    out = "\n".join(setup.lines)
    assert "problem: failed units: api.service" in out
    assert "failed: the earlier home is back. Nothing was saved." in out
    assert "Your edited files are still in the folder." in out
    assert setup.world.running() == str(earlier)  # the earlier generation is active again
    assert setup.world.deleted == ["2"]  # the failed generation leaves the profile
    assert setup.commits() == ["minix init"]
    assert (setup.ws / "home.json").exists()  # the edit stays in the folder


def test_the_rollback_runs_the_earlier_activation_then_resets_the_units(setup: Setup) -> None:
    setup.world.install("home-0")
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    trace = setup.trace()
    start = trace.index("activate:home-1")
    assert trace[start:] == ["activate:home-1", "health", "activate:home-0", "reset", "switch", "delete"]


def test_the_rollback_sets_the_profile_to_the_earlier_generation(setup: Setup) -> None:
    """011 P3: the activation of the earlier home did not move the profile, so minix must set it (finding 1)."""
    earlier = setup.world.install("home-0")
    setup.world.earlier_relinks = False
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    assert setup.world.running() == str(earlier)
    assert setup.world.switched == ["1"]
    assert setup.world.deleted == ["2"]


def test_a_failed_rollback_says_so_and_the_next_apply_switches_again(setup: Setup) -> None:
    """011 P3: a failed rollback left the profile at the new home, and the next apply skipped the switch."""
    setup.world.install("home-0")
    setup.world.earlier_relinks = False
    setup.world.switch_code = 1
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    assert any("tried to roll back, but that also failed" in line for line in setup.lines)
    assert not any(".." in line for line in setup.lines)  # the failed-rollback line has one period before "Nothing"
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-1")  # the profile holds the new home
    setup.lines.clear()
    setup.fake.calls.clear()
    setup.world.units = GOOD_UNITS
    setup.world.switch_code = 0
    assert apply(setup) == 0, setup.lines
    assert "activate:home-1" in setup.trace()  # it did not say "nothing to change"
    assert "nothing to change" not in setup.lines
    assert setup.commits()[0] == "apply: home.json"


def test_a_failed_switch_with_a_good_rollback_does_not_force_a_switch_again(setup: Setup) -> None:
    earlier = setup.world.install("home-0")
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    assert setup.world.running() == str(earlier)
    assert not (setup.world.profiles / "home-manager.unsettled").exists()


def test_a_good_switch_clears_the_unsettled_mark(setup: Setup) -> None:
    assert apply(setup) == 0
    assert not (setup.world.profiles / "home-manager.unsettled").exists()
    setup.lines.clear()
    assert apply(setup) == 0
    assert setup.lines[-1] == "nothing to change"


def test_a_looping_service_counts_as_failed(setup: Setup) -> None:
    setup.world.install("home-0")
    setup.world.units = LOOPING_UNITS
    assert apply(setup) == 3
    assert "problem: failed units: api.service" in setup.lines


def test_a_failed_activation_rolls_back(setup: Setup) -> None:
    setup.world.install("home-0")
    setup.world.activate_code = 7
    assert apply(setup) == 3
    assert "problem: the home did not switch (exit 7)" in setup.lines
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-0")
    assert setup.commits() == ["minix init"]


def test_on_fail_keep_keeps_the_new_activation(setup: Setup) -> None:
    setup.world.install("home-0")
    setup.world.units = BAD_UNITS
    assert apply(setup, MINIX_ON_FAIL="keep") == 3
    out = "\n".join(setup.lines)
    assert "failed: the new home is running, but it did not start cleanly. Nothing was saved." in out
    assert setup.world.running() == str(setup.world.tmp / "store" / "home-1")
    assert "activate:home-0" not in setup.trace()
    assert setup.world.deleted == []
    assert setup.commits() == ["minix init"]


def test_a_bad_on_fail_value_stops(setup: Setup) -> None:
    assert apply(setup, MINIX_ON_FAIL="maybe") == 1
    assert setup.lines == ["MINIX_ON_FAIL must be rollback or keep, found maybe."]


def test_a_failed_first_apply_has_no_earlier_home(setup: Setup) -> None:
    setup.world.units = BAD_UNITS
    assert apply(setup) == 3
    assert "found no earlier home to return to, so the new home stays" in "\n".join(setup.lines)
    assert setup.commits() == ["minix init"]


def test_a_failed_health_query_fails_the_apply(setup: Setup) -> None:
    setup.world.install("home-0")
    setup.world.health_code = 1
    setup.world.units = "Failed to connect to bus: No medium found\n"
    assert apply(setup) == 3
    assert setup.commits() == ["minix init"]


# --- lingering --------------------------------------------------------------------------------------
def test_apply_warns_once_when_the_account_does_not_linger(setup: Setup) -> None:
    setup.world.linger = "no"
    assert apply(setup) == 0
    warnings = [line for line in setup.lines if "lingering is off" in line]
    assert warnings == [
        "warning: lingering is off for ada, so your user services stop when you log out. "
        "To keep them running, ask an administrator to run: loginctl enable-linger ada"
    ]
    assert setup.lines[0] == warnings[0]


def test_apply_says_nothing_about_lingering_when_it_is_on(setup: Setup) -> None:
    assert apply(setup) == 0
    assert not [line for line in setup.lines if "lingering" in line]


# --- the stub, the lock, and the input packages -----------------------------------------------------
INPUT_HOME = 'inputs = [Input("nirinit", "github:amaanq/nirinit/REV", modules=["nirinit"])]\n'


def test_an_input_in_home_py_rewrites_the_stub_locks_and_makes_the_packages(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert apply(setup) == 0, setup.lines
    stub = (setup.ws / "flake.nix").read_text()
    assert 'inputs.nirinit.url = "github:amaanq/nirinit/REV";' in stub
    assert 'inputs.nirinit.inputs.nixpkgs.follows = "nixpkgs";' in stub
    trace = setup.trace()
    assert trace[:4] == ["lock", "packages", "compiler", "compile"]
    packages = setup.nix_calls("build", "--out-link")[0]
    assert packages[len(NIX) + 2] == str(setup.ws / ".basix" / "pynix")
    assert packages[-1] == ".#input-packages"
    assert setup.git("status", "--porcelain") == ""
    assert ".basix" not in setup.git("ls-files")


def test_the_stub_keeps_the_url_that_it_holds(setup: Setup) -> None:
    stub = setup.ws / "flake.nix"
    stub.write_text(stub.read_text().replace("Bullish-Intelligence/minix", "example.org/mine"))
    assert apply(setup) == 0
    assert 'inputs.minix.url = "git+https://github.com/example.org/mine?ref=release";' in stub.read_text()


def test_an_unchanged_stub_needs_no_lock_but_the_input_packages_are_built_on_every_run(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    assert apply(setup) == 0, setup.lines
    setup.fake.calls.clear()
    edit_home(setup)
    assert apply(setup) == 0, setup.lines
    assert setup.nix_calls("flake", "lock") == []
    assert len([c for c in setup.nix_calls("build") if c[-1] == ".#input-packages"]) == 1  # 010-D13


def test_an_entry_without_inputs_builds_no_input_packages(setup: Setup) -> None:
    assert apply(setup) == 0, setup.lines
    assert [c for c in setup.nix_calls("build") if c[-1] == ".#input-packages"] == []


def test_the_stub_writer_warns_in_plain_words(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    setup.world.input_names = None  # Nix could not read the inputs of the flake
    assert apply(setup) == 0, setup.lines
    assert any("could not read the inputs of `nirinit`" in line for line in setup.lines)


def test_the_reader_gets_the_lock_node_of_each_input(setup: Setup) -> None:
    """011 P3: the reader reads the locked tree of an input first; the Compiler must hand it the lock node."""
    edit_home(setup, INPUT_HOME)
    assert apply(setup) == 0, setup.lines  # the first apply makes the lock
    seen: list[tuple[str, object]] = []

    def reader(url: str, locked: object = None) -> frozenset[str]:
        seen.append((url, locked))
        return frozenset({"nixpkgs"})

    setup.ctx.read_inputs = reader
    edit_home(setup)
    assert apply(setup) == 0, setup.lines
    assert [url for url, _ in seen] == ["github:amaanq/nirinit/REV"]
    node = seen[0][1]
    assert isinstance(node, dict) and node["locked"]["narHash"] == "sha256-nirinit"


def test_the_reader_gets_no_node_for_an_input_the_lock_lacks(setup: Setup) -> None:
    seen: list[object] = []
    setup.ctx.read_inputs = lambda url, locked=None: seen.append(locked) or frozenset({"nixpkgs"})
    edit_home(setup, INPUT_HOME)
    assert apply(setup) == 0, setup.lines  # the workspace lock has no node for `nirinit` yet
    assert seen == [None]


def test_a_flake_nix_that_minix_did_not_write_stops_the_apply(setup: Setup) -> None:
    (setup.ws / "flake.nix").write_text("{ outputs = _: { }; }\n")
    assert apply(setup) == 2
    assert "is not a managed stub" in setup.lines[0]


def test_a_failed_package_build_stops_before_the_compiler(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    setup.world.input_packages_code = 1
    assert apply(setup) == 2
    assert setup.lines[0].startswith("The input packages could not be built. Nothing was built, switched, or saved.")
    assert "compiler" not in setup.trace()


def test_a_failed_lock_stops_with_the_cause(setup: Setup) -> None:
    edit_home(setup, INPUT_HOME)
    setup.world.lock_code = 1
    assert apply(setup) == 2
    assert setup.lines[0].startswith("The inputs could not be locked. Nothing was built, switched, or saved.")
    assert "cannot lock" in setup.lines[0]


def test_apply_ignores_the_scratch_folder_when_the_ignore_file_lost_it(setup: Setup) -> None:
    (setup.ws / ".gitignore").write_text("result\n")
    setup.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-am", "drop the ignore line")
    assert apply(setup) == 0
    assert (setup.ws / ".gitignore").read_text() == "result\n.basix/\n"
    assert "added .basix/ to .gitignore" in setup.lines
