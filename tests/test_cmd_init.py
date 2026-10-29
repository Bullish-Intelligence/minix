"""`minix init`: the workspace, made offline (009-D2, 009-D29, G9)."""

import json
from pathlib import Path

import pytest
from pynix_engine import Fail
from test_cmd_support import SOURCE_HASH, SOURCE_REV, STUB_URL, Setup, git_env, make_setup

from minix import commands
from minix.workspace import host_system
from minix_config.entry import written_entry


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return make_setup(tmp_path)


def test_init_makes_the_workspace_and_the_first_save(setup: Setup) -> None:
    assert commands.init(setup.ctx) == 0
    ws = setup.ws
    assert sorted(p.name for p in ws.iterdir() if p.name != ".git") == [
        ".gitignore",
        "flake.lock",
        "flake.nix",
        "home.py",
    ]
    assert (ws / "home.py").read_text() == written_entry("ada")
    assert (ws / "flake.nix").read_text().startswith("# Managed by minix. Do not edit.\n")
    assert f'inputs.minix.url = "{STUB_URL}"' in (ws / "flake.nix").read_text()
    assert (ws / ".gitignore").read_text() == ".basix/\nresult\n"
    assert setup.commits() == ["minix init"]
    assert setup.git("status", "--porcelain") == ""
    assert setup.lines[0] == "created ~/minix"
    assert setup.lines[-1] == "next: edit ~/minix/home.py, then run: minix apply"


def test_init_records_the_system_of_the_host_in_the_stub(setup: Setup) -> None:
    setup.ctx.system = "aarch64-linux"
    assert commands.init(setup.ctx) == 0
    assert 'system = "aarch64-linux";' in (setup.ws / "flake.nix").read_text()


def test_init_passes_the_url_to_the_stub_writer(setup: Setup) -> None:
    assert commands.init(setup.ctx, url="git+https://example.org/minix?ref=other") == 0
    assert 'inputs.minix.url = "git+https://example.org/minix?ref=other";' in (setup.ws / "flake.nix").read_text()
    assert STUB_URL not in (setup.ws / "flake.nix").read_text()


def test_init_writes_the_lock_from_the_files_of_the_package(setup: Setup) -> None:
    assert commands.init(setup.ctx) == 0
    lock = json.loads((setup.ws / "flake.lock").read_text())
    assert lock["root"] == "root"
    assert lock["nodes"]["root"] == {"inputs": {"minix": "minix"}}
    minix = lock["nodes"]["minix"]
    assert minix["original"] == {
        "type": "git",
        "url": "https://github.com/Bullish-Intelligence/minix",
        "ref": "release",
    }
    assert minix["locked"] == {
        **minix["original"],
        "rev": SOURCE_REV,
        "narHash": SOURCE_HASH,
        "lastModified": 1700000000,
        "revCount": 42,
    }
    assert minix["inputs"] == {"nixpkgs": "nixpkgs", "home-manager": "home-manager"}
    assert lock["nodes"]["home-manager"]["inputs"] == {"nixpkgs": ["minix", "nixpkgs"]}  # a follows path from the root


def test_init_keeps_the_lock_off_for_a_url_that_the_package_does_not_describe(setup: Setup) -> None:
    assert commands.init(setup.ctx, url="git+https://example.org/minix?ref=other") == 0
    assert not (setup.ws / "flake.lock").exists()
    assert "carries no lock" in setup.lines[1]


def test_init_runs_no_nix_and_needs_no_network(setup: Setup) -> None:
    assert commands.init(setup.ctx) == 0
    assert setup.fake.calls == []  # every non-git command goes to the fake runner


def test_init_refuses_a_folder_that_is_not_empty(setup: Setup) -> None:
    setup.ws.mkdir()
    (setup.ws / "notes.txt").write_text("mine\n")
    assert commands.init(setup.ctx) == 1
    assert setup.lines == ["~/minix exists and is not empty. minix init makes a new folder; give another with --dir."]
    assert sorted(p.name for p in setup.ws.iterdir()) == ["notes.txt"]


def test_init_refuses_a_file(setup: Setup) -> None:
    setup.ws.write_text("a file\n")
    assert commands.init(setup.ctx) == 1
    assert "exists and is not empty" in setup.lines[0]


def test_init_accepts_an_empty_folder(setup: Setup) -> None:
    setup.ws.mkdir()
    assert commands.init(setup.ctx) == 0
    assert setup.commits() == ["minix init"]


def test_init_takes_another_folder(setup: Setup) -> None:
    other = setup.home / "elsewhere" / "home"
    assert commands.init(setup.ctx, other) == 0
    assert (other / "home.py").is_file()
    assert setup.lines[0] == "created ~/elsewhere/home"
    assert not setup.ws.exists()


def test_init_uses_the_folder_of_the_environment_variable(setup: Setup) -> None:
    named = setup.home / "named" / "minix"
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": str(named)}
    assert commands.init(setup.ctx) == 0
    assert (named / "home.py").is_file()
    assert not setup.ws.exists()
    assert setup.lines[0] == "created ~/named/minix"
    assert setup.lines[-1] == "next: edit ~/named/minix/home.py, then run: minix apply"


def test_init_expands_the_tilde_of_the_variable_and_makes_the_parent_folders(setup: Setup) -> None:
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": "~/.dotfiles/minix"}
    assert not (setup.home / ".dotfiles").exists()
    assert commands.init(setup.ctx) == 0
    assert (setup.home / ".dotfiles" / "minix" / "home.py").is_file()
    assert (setup.home / ".dotfiles" / "minix" / ".git").is_dir()
    assert setup.lines[0] == "created ~/.dotfiles/minix"
    assert not setup.ws.exists()


def test_init_reads_a_variable_that_is_the_tilde_alone_as_the_home_folder(setup: Setup) -> None:
    setup.home.mkdir(exist_ok=True)
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": "~"}
    assert commands.init(setup.ctx) == 0
    assert (setup.home / "home.py").is_file()


def test_init_without_the_variable_makes_the_default_folder(setup: Setup) -> None:
    assert "MINIX_WORKSPACE" not in setup.ctx.env
    assert commands.init(setup.ctx) == 0
    assert (setup.ws / "home.py").is_file()


def test_init_treats_an_empty_variable_as_unset(setup: Setup) -> None:
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": ""}
    assert commands.init(setup.ctx) == 0
    assert (setup.ws / "home.py").is_file()


def test_init_dir_wins_over_the_variable(setup: Setup) -> None:
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": "~/named"}
    other = setup.home / "other"
    assert commands.init(setup.ctx, other) == 0
    assert (other / "home.py").is_file()
    assert not (setup.home / "named").exists()


def test_init_leaves_an_existing_workspace_of_the_variable_alone(setup: Setup) -> None:
    named = setup.home / "named"
    named.mkdir(parents=True)
    (named / "home.py").write_text("mine\n")
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": "~/named"}
    assert commands.init(setup.ctx) == 1
    assert setup.lines == ["~/named exists and is not empty. minix init makes a new folder; give another with --dir."]
    assert (named / "home.py").read_text() == "mine\n"
    assert not setup.ws.exists()


def test_init_removes_the_parent_folders_that_it_made_when_it_fails(setup: Setup) -> None:
    setup.ctx.env = {**setup.ctx.env, "MINIX_WORKSPACE": "~/.dotfiles/minix"}
    setup.ctx.user = "Ada Lovelace"  # Home Manager refuses this name
    assert commands.init(setup.ctx) == 1
    assert not (setup.home / ".dotfiles").exists()


def test_init_without_a_bundled_lock_says_so(setup: Setup) -> None:
    setup.ctx.share = None
    assert commands.init(setup.ctx) == 0
    assert not (setup.ws / "flake.lock").exists()
    assert "carries no lock" in setup.lines[1]


def test_init_commits_with_the_fallback_identity_when_git_knows_no_author(setup: Setup) -> None:
    assert commands.init(setup.ctx) == 0
    assert setup.git("log", "-1", "--format=%an <%ae>").strip() == "minix <minix@localhost>"


def test_init_keeps_the_author_that_git_knows(tmp_path: Path) -> None:
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Ada Lovelace\n\temail = ada@example.org\n")
    setup = make_setup(tmp_path)
    setup.runner.env = git_env(config)
    assert commands.init(setup.ctx) == 0
    assert setup.git("log", "-1", "--format=%an <%ae>").strip() == "Ada Lovelace <ada@example.org>"


def test_host_system_names() -> None:
    assert host_system("Linux", "x86_64") == "x86_64-linux"
    assert host_system("Linux", "aarch64") == "aarch64-linux"
    assert host_system("Linux", "arm64") == "aarch64-linux"


@pytest.mark.parametrize(("kernel", "machine"), [("Darwin", "arm64"), ("Linux", "riscv64")])
def test_host_system_refuses_other_hosts(kernel: str, machine: str) -> None:
    with pytest.raises(Fail, match="x86_64 and aarch64 Linux only"):
        host_system(kernel, machine)


def test_init_without_the_source_revision_says_so(setup: Setup) -> None:
    (setup.share / "source.json").write_text(json.dumps({"rev": None, "narHash": "sha256-X"}))  # a dirty tree
    assert commands.init(setup.ctx) == 0
    assert not (setup.ws / "flake.lock").exists()
    assert "carries no lock" in setup.lines[1]


def test_init_refuses_an_account_name_that_home_manager_cannot_use(setup: Setup) -> None:
    setup.ctx.user = "Ada Lovelace"
    assert commands.init(setup.ctx) == 1
    assert "is not a valid Home user" in setup.lines[0]
    assert not setup.ws.exists()
