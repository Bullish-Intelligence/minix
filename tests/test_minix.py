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
    assert __version__ == "0.3.0"


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
    with pytest.raises(Fail, match=r"is not a minix workspace\. Go to ~/minix first\.") as stop:
        find_workspace(None, tmp_path, tmp_path)
    assert stop.value.code == 1


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
