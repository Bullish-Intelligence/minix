"""The minix layer and its parts: the values that the engine reads (009-D26, 009-D29, 009-D30)."""

import os
from pathlib import Path

import pytest
from pynix_engine import EXIT_STOPPED, Fail, Result
from pynix_engine.testing import FakeRunner
from test_cmd_support import NIX

from minix import __version__
from minix.layer import HEALTH_COMMAND, WORDS, minix_layer, profile_path, roll_back_commands
from minix.nix import NixRunner, with_features
from minix.workspace import find_workspace, stub_url, tilde


def test_version() -> None:
    assert __version__ == "0.4.2"


def test_the_layer_names_the_files_and_the_build_attribute() -> None:
    layer = minix_layer("ada")
    assert (layer.name, layer.entry, layer.json_file) == ("minix", "home.py", "home.json")
    assert layer.build_attr == ".#homeConfigurations.ada.activationPackage"
    assert layer.compiler_bin == "minix-compile"
    assert layer.compiler_attr == ".#compiler"
    assert layer.on_fail_env == "MINIX_ON_FAIL"
    assert layer.noun == "home"


def test_the_layer_asks_the_user_manager_about_the_units() -> None:
    layer = minix_layer("ada")
    assert layer.health_command == HEALTH_COMMAND
    assert layer.health_command[:3] == ("systemctl", "--user", "list-units")
    assert ".service" in layer.unit_suffixes


def test_the_layer_has_no_sudo_and_no_build_command() -> None:
    layer = minix_layer("ada")
    assert layer.build_command is None
    assert layer.preflight is None  # the command adds the lingering warning


def test_the_words_are_plain() -> None:
    layer = minix_layer("ada")
    assert layer.text("nothing") == "nothing to change"
    assert layer.text("applied") == "applied: the home runs, and its units are healthy"
    assert layer.text("rolled_back") == "the earlier home is back"
    assert layer.text("wrong_account", named="bob", running="ada").startswith('home.py names the account "bob"')
    assert WORDS.stopped == "apply stopped"


def test_with_features_adds_the_features_after_nix() -> None:
    assert with_features(["nix", "build", ".#x"]) == [*NIX, "build", ".#x"]


def test_with_features_leaves_other_commands_alone() -> None:
    for argv in (["nix-store", "--realise"], ["nix-env", "--profile", "p"], ["git", "status"], ["systemctl", "--user"]):
        assert with_features(argv) == argv


def test_the_nix_runner_passes_the_features_and_the_rest_to_the_inner_runner(tmp_path: Path) -> None:
    fake = FakeRunner()
    fake.on(NIX, Result(0, "out\n"))
    lines: list[str] = []
    result = NixRunner(fake).run(["nix", "flake", "lock"], cwd=tmp_path, on_line=lines.append)
    assert (result.code, lines) == (0, ["out"])
    assert fake.calls == [(*NIX, "flake", "lock")]
    assert fake.cwds == [tmp_path]


def test_profile_path_follows_the_state_folder() -> None:
    assert profile_path({"XDG_STATE_HOME": "/s"}) == Path("/s/nix/profiles/home-manager")
    assert profile_path({}, Path("/h")) == Path("/h/.local/state/nix/profiles/home-manager")


def test_roll_back_has_no_commands_without_an_earlier_home(tmp_path: Path) -> None:
    assert roll_back_commands(tmp_path / "home-manager", None, "/nix/store/new") == []
    assert roll_back_commands(tmp_path / "home-manager", str(tmp_path / "gone"), "/nix/store/new") == []


def test_roll_back_activates_the_earlier_home_and_deletes_the_failed_generation(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for home in (old, new):
        home.mkdir()
    (old / "activate").write_text("")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "home-manager-3-link").symlink_to(old)
    (profiles / "home-manager-4-link").symlink_to(new)
    profile = profiles / "home-manager"
    assert roll_back_commands(profile, str(old), str(new)) == [
        [f"{old}/activate"],
        ["systemctl", "--user", "reset-failed"],
        ["nix-env", "--profile", str(profile), "--switch-generation", "3"],
        ["nix-env", "--profile", str(profile), "--delete-generations", "4"],
        ["rm", "-f", str(profiles / "home-manager.unsettled")],
    ]


def test_roll_back_sets_the_profile_to_the_earlier_generation_by_path_when_it_has_no_link(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for home in (old, new):
        home.mkdir()
    (old / "activate").write_text("")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = profiles / "home-manager"
    assert roll_back_commands(profile, str(old), str(new))[2] == [
        "nix-env",
        "--profile",
        str(profile),
        "--set",
        str(old),
    ]


def test_find_workspace_prefers_the_explicit_folder(tmp_path: Path) -> None:
    assert find_workspace(tmp_path / "w", tmp_path, tmp_path) == (tmp_path / "w").resolve()


def test_find_workspace_walks_up_to_the_repository(tmp_path: Path) -> None:
    ws = tmp_path / "minix"
    (ws / ".git").mkdir(parents=True)
    (ws / "home.py").write_text("")
    (ws / "deep" / "er").mkdir(parents=True)
    assert find_workspace(None, ws / "deep" / "er", tmp_path) == ws


def test_find_workspace_falls_back_to_the_default_folder(tmp_path: Path) -> None:
    (tmp_path / "minix" / ".git").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert find_workspace(None, other, tmp_path) == tmp_path / "minix"


def test_find_workspace_stops_when_there_is_none(tmp_path: Path) -> None:
    with pytest.raises(
        Fail, match=r"is not a minix workspace\. Go to ~/minix first, or set MINIX_WORKSPACE to its folder\."
    ) as stop:
        find_workspace(None, tmp_path, tmp_path)
    assert stop.value.code == 1


def make_workspace(folder: Path, entry: str = "home.py") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".git").mkdir(exist_ok=True)
    (folder / entry).write_text("")
    return folder


def test_the_environment_names_the_workspace_outside_any_workspace(tmp_path: Path) -> None:
    kept = make_workspace(tmp_path / "home" / "kept" / "minix")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    assert find_workspace(None, nowhere, tmp_path / "home", {"MINIX_WORKSPACE": str(kept)}) == kept


def test_the_environment_expands_the_tilde_against_the_home_of_the_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    kept = make_workspace(home / "kept" / "minix")
    assert find_workspace(None, tmp_path, home, {"MINIX_WORKSPACE": "~/kept/minix"}) == kept
    make_workspace(home)
    assert find_workspace(None, tmp_path, home, {"MINIX_WORKSPACE": "~"}) == home.resolve()


def test_the_order_is_dir_then_the_repository_above_then_the_environment_then_the_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    default = make_workspace(home / "minix")
    named = make_workspace(home / "named")
    above = make_workspace(tmp_path / "above")
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    env = {"MINIX_WORKSPACE": str(named)}
    inside = above / "sub"
    inside.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert find_workspace(explicit, inside, home, env) == explicit.resolve()  # --dir first
    assert find_workspace(None, inside, home, env) == above  # then the repository above the current folder
    assert find_workspace(None, elsewhere, home, env) == named  # then the environment
    assert find_workspace(None, elsewhere, home, {}) == default  # then ~/minix
    assert find_workspace(None, elsewhere, home, {"MINIX_WORKSPACE": ""}) == default  # an empty variable is unset


def test_a_variable_that_names_a_folder_with_no_home_py_stops_with_one_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    make_workspace(home / "minix")  # the default exists, and still the run must not fall back to it
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Fail) as stop:
        find_workspace(None, tmp_path, home, {"MINIX_WORKSPACE": str(empty)})
    assert str(stop.value) == (
        f"MINIX_WORKSPACE is {empty}, but that folder holds no home.py. "
        "Set MINIX_WORKSPACE to your workspace folder, or unset it."
    )
    assert stop.value.code == 1
    with pytest.raises(Fail, match="holds no home.py"):  # a folder that does not exist
        find_workspace(None, tmp_path, home, {"MINIX_WORKSPACE": "~/missing"})
    make_workspace(tmp_path / "bas", "system.py")  # a Basix workspace is not a minix workspace
    with pytest.raises(Fail, match="holds no home.py"):
        find_workspace(None, tmp_path, home, {"MINIX_WORKSPACE": str(tmp_path / "bas")})


def test_tilde() -> None:
    assert tilde(Path("/h/minix"), Path("/h")) == "~/minix"
    assert tilde(Path("/h"), Path("/h")) == "~"
    assert tilde(Path("/srv/minix"), Path("/h")) == "/srv/minix"


def test_stub_url_reads_the_url_of_the_stub(tmp_path: Path) -> None:
    assert stub_url(tmp_path) is None
    (tmp_path / "flake.nix").write_text('{\n  inputs.minix.url = "git+https://example.org/minix?ref=release";\n}\n')
    assert stub_url(tmp_path) == "git+https://example.org/minix?ref=release"
    (tmp_path / "flake.nix").write_text("{ }\n")
    assert stub_url(tmp_path) is None


def test_stopped_errors_exit_with_two() -> None:
    from minix.layer import stopped

    assert stopped("x").code == EXIT_STOPPED


def test_real_context_reads_the_account_and_the_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from minix import commands

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    ctx = commands.real_context()
    assert ctx.home == tmp_path
    assert ctx.cwd == Path(os.getcwd())
    assert ctx.user
