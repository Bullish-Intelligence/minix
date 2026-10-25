"""The lock of a new workspace, made from the files that the package carries (009-D2, G9)."""

import json
from pathlib import Path

import pytest
from test_cmd_support import SOURCE_HASH, SOURCE_REV, write_share

from minix import commands
from minix.seedlock import git_original, workspace_lock
from minix_config.stub import DEFAULT_MINIX_URL


@pytest.fixture
def share(tmp_path: Path) -> Path:
    write_share(tmp_path / "share" / "minix")
    return tmp_path / "share" / "minix"


def test_the_default_url_gives_a_lock(share: Path) -> None:
    lock = json.loads(workspace_lock(share, DEFAULT_MINIX_URL) or "")
    assert lock["version"] == 7
    assert set(lock["nodes"]) == {"root", "minix", "nixpkgs", "home-manager"}
    assert lock["nodes"]["minix"]["locked"]["rev"] == SOURCE_REV
    assert lock["nodes"]["minix"]["locked"]["narHash"] == SOURCE_HASH


def test_the_nodes_of_minix_keep_their_locked_versions(share: Path) -> None:
    own = json.loads((share / "flake.lock").read_text())
    lock = json.loads(workspace_lock(share, DEFAULT_MINIX_URL) or "")
    assert lock["nodes"]["nixpkgs"] == own["nodes"]["nixpkgs"]
    assert lock["nodes"]["home-manager"]["locked"] == own["nodes"]["home-manager"]["locked"]


def test_the_share_folder_is_not_changed(share: Path) -> None:
    before = (share / "flake.lock").read_text()
    workspace_lock(share, DEFAULT_MINIX_URL)
    assert (share / "flake.lock").read_text() == before


@pytest.mark.parametrize(
    "url",
    [
        "github:Bullish-Intelligence/minix",
        "git+https://example.org/minix?ref=release&rev=" + "a" * 40,
        "git+https://example.org/minix?dir=sub",
        "path:/home/ada/minix",
    ],
)
def test_a_url_that_the_source_does_not_describe_gives_no_lock(share: Path, url: str) -> None:
    assert workspace_lock(share, url) is None


@pytest.mark.parametrize("missing", ["source.json", "flake.lock"])
def test_a_missing_file_gives_no_lock(share: Path, missing: str) -> None:
    (share / missing).unlink()
    assert workspace_lock(share, DEFAULT_MINIX_URL) is None


def test_a_broken_file_gives_no_lock(share: Path) -> None:
    (share / "flake.lock").write_text("{")
    assert workspace_lock(share, DEFAULT_MINIX_URL) is None


def test_a_source_without_a_revision_gives_no_lock(share: Path) -> None:
    (share / "source.json").write_text(json.dumps({"rev": None, "narHash": "sha256-X"}))
    assert workspace_lock(share, DEFAULT_MINIX_URL) is None


def test_git_original_reads_the_ref() -> None:
    assert git_original("git+https://example.org/minix") == {"type": "git", "url": "https://example.org/minix"}
    assert git_original("git+file:///srv/minix?ref=main") == {"type": "git", "url": "file:///srv/minix", "ref": "main"}


def test_real_context_reads_the_share_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINIX_SHARE", str(tmp_path / "share"))
    assert commands.real_context().share == tmp_path / "share"
    monkeypatch.delenv("MINIX_SHARE")
    assert commands.real_context().share is None
