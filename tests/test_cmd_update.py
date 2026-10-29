"""`minix update [NAME]`: move the lock, print the release names, the age of each nixpkgs, and the part of the
`pynix` changelog that names an option of `home.py`, and make the input packages (009-D10, 009-D48, 009-D54)."""

import json
from pathlib import Path

import pytest
from pynix_engine import Result
from test_cmd_support import NIX, NIXPKGS_REV, Setup, initialized, lock_json

from minix import commands
from minix.release import changelog_sections, nixpkgs_ages, relevant_lines, tag_name

NEW_LOCK = lock_json(nar_hash="sha256-BBBB", rev="c" * 40)
NOW = 1_800_000_000.0
DAY = 86400
CHANGELOG_DIR = Path(__file__).parent / "fixtures" / "changelog"
GIT = {"path": ["programs", "git"], "source": "home.py:5 (Git)", "value": {"enable": True}, "version": 1}
BASH = {"path": ["programs", "bash"], "source": "home.py:6 (Bash)", "value": {"enable": True}, "version": 1}


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    setup = initialized(tmp_path, update_to=NEW_LOCK)
    setup.ctx.wall_time = lambda: NOW
    return setup


def test_update_prints_the_old_and_the_new_release(setup: Setup) -> None:
    assert commands.update(setup.ctx) == 0
    assert setup.lines[0] == "minix v0.1.0 -> v0.1.1"
    assert setup.lines[-1] == "next: run minix apply to use it"
    assert (setup.ws / "flake.lock").read_text() == NEW_LOCK


def test_update_runs_one_nix_update_for_minix(setup: Setup) -> None:
    assert commands.update(setup.ctx) == 0
    assert setup.nix_calls("flake", "update") == [(*NIX, "flake", "update", "minix", "--no-warn-dirty")]


def test_update_with_no_new_release_says_so(tmp_path: Path) -> None:
    setup = initialized(tmp_path, update_to=lock_json())
    setup.world.new_version = "0.1.0"
    assert commands.update(setup.ctx) == 0
    assert setup.lines[0] == "minix is up to date (v0.1.0)"
    assert setup.lines[1:] == ["nixpkgs e7439b6b, age unknown", "next: run minix apply to use it"]


def test_update_adds_the_revision_when_two_releases_share_a_name(tmp_path: Path) -> None:
    setup = initialized(tmp_path, update_to=NEW_LOCK)
    setup.world.new_version = "0.1.0"
    assert commands.update(setup.ctx) == 0
    assert setup.lines[0] == f"minix v0.1.0 ({'a' * 7}) -> v0.1.0 ({'c' * 7})"


def test_update_does_not_save_or_switch(setup: Setup) -> None:
    assert commands.update(setup.ctx) == 0
    assert setup.commits() == ["minix init"]
    assert not [t for t in setup.trace() if t.startswith("activate") or t == "build"]


def test_update_reads_the_revision_and_the_tag_when_the_source_is_not_there(tmp_path: Path) -> None:
    setup = initialized(tmp_path, update_to=lock_json(nar_hash="sha256-BBBB", rev="c" * 40, ref="refs/tags/v0.2.0"))
    setup.world.no_source = True
    assert commands.update(setup.ctx) == 0
    assert setup.lines[0] == "minix aaaaaaa -> v0.2.0"


def test_update_of_an_unknown_input_stops_and_keeps_the_lock(tmp_path: Path) -> None:
    setup = initialized(tmp_path)  # update_to is None: nix fails
    before = (setup.ws / "flake.lock").read_text()
    assert commands.update(setup.ctx, "nope") == 2
    assert setup.lines == ["The update failed. The lock did not change.\nerror: input 'nope' does not exist"]
    assert (setup.ws / "flake.lock").read_text() == before


def test_update_makes_the_input_packages_again(tmp_path: Path) -> None:
    setup = initialized(tmp_path, update_to=lock_json(nar_hash="sha256-BBBB", inputs=["nirinit"]))
    (setup.ws / "home.py").write_text('inputs = [Input("nirinit", "github:amaanq/nirinit/REV", modules=["nirinit"])]\n')
    assert commands.update(setup.ctx, "nirinit") == 0, setup.lines
    assert setup.nix_calls("flake", "update") == [(*NIX, "flake", "update", "nirinit", "--no-warn-dirty")]
    assert [c for c in setup.nix_calls("build") if c[-1] == ".#input-packages"]
    assert setup.trace().index("update") < setup.trace().index("packages")


def test_update_without_inputs_builds_no_packages(setup: Setup) -> None:
    assert commands.update(setup.ctx) == 0
    assert [c for c in setup.nix_calls("build") if c[-1] == ".#input-packages"] == []


def test_update_leaves_a_home_py_that_cannot_be_read_to_the_next_apply(setup: Setup) -> None:
    (setup.ws / "home.py").write_text("home = (\n")
    assert commands.update(setup.ctx) == 0
    assert setup.lines[-1] == "next: run minix apply to use it"


def test_update_outside_a_workspace_says_where_to_go(setup: Setup) -> None:
    setup.ctx.cwd = setup.home / "nowhere"
    (setup.ws / ".git").rename(setup.ws / ".gone")
    assert commands.update(setup.ctx) == 1
    assert setup.lines[0].endswith(
        "is not a minix workspace. Go to ~/minix first, or set MINIX_WORKSPACE to its folder."
    )


@pytest.mark.parametrize(
    ("ref", "name"),
    [("refs/tags/v0.2.0", "v0.2.0"), ("v1.2", "v1.2"), ("release", None), ("main", None), (None, None)],
)
def test_tag_name(ref: str | None, name: str | None) -> None:
    assert tag_name(ref) == name


def test_update_waits_when_another_minix_command_runs(setup: Setup) -> None:
    import fcntl

    (setup.ws / ".git" / "minix").mkdir(exist_ok=True)
    with (setup.ws / ".git" / "minix" / "lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert commands.update(setup.ctx) == 1
    assert setup.lines == ["Another minix command runs in this workspace. Wait for it."]
    assert setup.nix_calls("flake", "update") == []


# --- the changelog, the tag, and the age (010-D31, 010-D32) -----------------------------------------
def pynix_node(tag: str, rev: str) -> dict[str, object]:
    ref = f"refs/tags/{tag}"
    return {
        "flake": False,
        "locked": {
            "type": "git",
            "ref": ref,
            "rev": rev,
            "narHash": f"sha256-{tag}",
            "url": "https://example.invalid/p",
        },
        "original": {"type": "git", "ref": ref, "url": "https://example.invalid/p"},
    }


def nested_lock(minix_rev: str, minix_hash: str, pynix_tag: str, pynix_rev: str, minix_ref: str = "release") -> str:
    """A `flake.lock` of a workspace: `minix` holds the nested input `pynix`, and the pin of nixpkgs has a date."""
    lock = json.loads(lock_json(nar_hash=minix_hash, rev=minix_rev, ref=minix_ref))
    lock["nodes"]["minix"]["inputs"] = {"pynix": "pynix"}
    lock["nodes"]["pynix"] = pynix_node(pynix_tag, pynix_rev)
    lock["nodes"]["nixpkgs"]["locked"]["lastModified"] = int(NOW) - 13 * DAY
    return json.dumps(lock)


def moving(tmp_path: Path, home_fragments: list[dict], *, pynix_moves: bool = True, **world: object) -> Setup:
    """A workspace whose lock moves `minix` and (when `pynix_moves`) the nested `pynix` from rc.2 to rc.3."""
    new_tag, new_rev = ("pynix-v0.1.0-rc.3", "q" * 40) if pynix_moves else ("pynix-v0.1.0-rc.2", "p" * 40)
    after = nested_lock("c" * 40, "sha256-BBBB", new_tag, new_rev)
    setup = initialized(tmp_path, update_to=after, **world)
    (setup.ws / "flake.lock").write_text(nested_lock("a" * 40, "sha256-AAAA", "pynix-v0.1.0-rc.2", "p" * 40))
    (setup.ws / "home.json").write_text(json.dumps({"user": "ada", "fragments": home_fragments}))
    setup.ctx.wall_time = lambda: NOW
    return setup


def serve(
    setup: Setup, tmp_path: Path, tags: tuple[str | None, str | None], changelog: Path | None = CHANGELOG_DIR
) -> None:
    """Make `nix flake archive` give a minix source for each side of the update and a `pynix` source with a changelog.

    `tags` is the `tag` key of `provenance/inputs.json` before and after; None leaves the file out.
    """

    def archive(argv):
        side = 1 if setup.world.updated else 0
        source = tmp_path / f"minix-source-{side}"
        (source / "provenance").mkdir(parents=True, exist_ok=True)
        (source / "pyproject.toml").write_text('[project]\nname = "minix"\nversion = "0.1.0"\n')
        if tags[side]:
            (source / "provenance" / "inputs.json").write_text(json.dumps({"tag": tags[side], "inputs": {}}))
        pynix = changelog or tmp_path / "no-changelog"
        pynix.mkdir(exist_ok=True)
        tree = {
            "path": str(source),
            "inputs": {"minix": {"path": str(source), "inputs": {"pynix": {"path": str(pynix)}}}},
        }
        return Result(0, "warning: noise\n" + json.dumps(tree) + "\n")

    setup.fake.on(NIX + ("flake", "archive"), archive)


def test_update_names_two_releases_by_the_tag_of_inputs_json(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.2.0-rc.1", "v0.2.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[0] == "minix v0.2.0-rc.1 -> v0.2.0-rc.2"


def test_update_tells_two_rcs_of_one_version_apart(tmp_path: Path) -> None:
    """The two sources hold the same `version` in `pyproject.toml`; only the recorded tag differs."""
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[0] == "minix v0.1.0-rc.1 -> v0.1.0-rc.2"
    assert "(" not in setup.lines[0]


def test_update_falls_back_to_the_version_when_inputs_json_has_no_tag(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.3.0", None))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[0] == "minix v0.3.0 -> v0.1.0"


def test_update_falls_back_to_the_short_revision(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT], no_source=True)
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[0] == f"minix {'a' * 7} -> {'c' * 7}"


def test_update_falls_back_to_the_git_ref(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT], no_source=True)
    setup.world.update_to = nested_lock(
        "c" * 40, "sha256-BBBB", "pynix-v0.1.0-rc.3", "q" * 40, minix_ref="refs/tags/v0.4.0"
    )
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[0] == f"minix {'a' * 7} -> v0.4.0"


def test_update_prints_the_age_of_each_pinned_nixpkgs(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines[1] == f"nixpkgs {NIXPKGS_REV[:8]}, 13 days old"


def test_update_shows_the_changelog_lines_that_touch_an_option_that_home_py_sets(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines == [
        "minix v0.1.0-rc.1 -> v0.1.0-rc.2",
        f"nixpkgs {NIXPKGS_REV[:8]}, 13 days old",
        "pynix-v0.1.0-rc.3 changes options that home.py sets:",
        "  programs.git.enable: bool -> enum  [Git]",
        "next: run minix apply to use it",
    ]
    assert (setup.ws / "flake.lock").read_text() == setup.world.update_to


def test_update_uses_the_provenance_key_to_name_the_field(tmp_path: Path) -> None:
    setup = moving(tmp_path, [BASH])
    (setup.ws / "home.json").write_text(
        json.dumps({"user": "ada", "fragments": [BASH], "provenance": {"Git": ["programs.git"]}})
    )
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert "  programs.git.enable: bool -> enum  [Git]" in setup.lines


def test_update_says_when_no_change_touches_an_option_that_home_py_sets(tmp_path: Path) -> None:
    setup = moving(tmp_path, [BASH])
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert "pynix-v0.1.0-rc.2 -> pynix-v0.1.0-rc.3: no change to an option that home.py sets" in setup.lines
    assert not [line for line in setup.lines if "changes options" in line]


def test_update_prints_nothing_about_pynix_when_it_did_not_move(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT], pynix_moves=False)
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert setup.lines == [
        "minix v0.1.0-rc.1 -> v0.1.0-rc.2",
        f"nixpkgs {NIXPKGS_REV[:8]}, 13 days old",
        "next: run minix apply to use it",
    ]


def test_update_says_when_the_changelog_of_pynix_cannot_be_read(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"), changelog=tmp_path / "empty")
    assert commands.update(setup.ctx) == 0, setup.lines
    assert "pynix changed, but its changelog could not be read" in setup.lines
    assert setup.lines[-1] == "next: run minix apply to use it"


def test_update_reads_home_json_when_home_py_was_not_compiled_yet(tmp_path: Path) -> None:
    setup = moving(tmp_path, [GIT])
    (setup.ws / "home.json").unlink()
    serve(setup, tmp_path, ("v0.1.0-rc.1", "v0.1.0-rc.2"))
    assert commands.update(setup.ctx) == 0, setup.lines
    assert "pynix-v0.1.0-rc.2 -> pynix-v0.1.0-rc.3: no change to an option that home.py sets" in setup.lines


def test_update_names_every_tag_between_the_old_and_the_new() -> None:
    text = (CHANGELOG_DIR / "CHANGELOG.md").read_text()
    sections = changelog_sections(text, "pynix-v0.1.0-rc.1")
    assert [tag for tag, _ in sections] == ["pynix-v0.1.0-rc.3", "pynix-v0.1.0-rc.2"]
    assert changelog_sections(text, "gone") == sections[:1]  # an old tag that the file lacks: the newest only
    assert changelog_sections(text, None) == sections[:1]


def test_the_filter_keeps_the_lines_that_name_an_option_of_the_workspace() -> None:
    body = changelog_sections((CHANGELOG_DIR / "CHANGELOG.md").read_text(), "pynix-v0.1.0-rc.2")[0][1]
    assert relevant_lines(body, {"Git": ["programs.git.enable"]}) == ["programs.git.enable: bool -> enum  [Git]"]
    assert relevant_lines(body, {"Git": ["programs.git"]}) == ["programs.git.enable: bool -> enum  [Git]"]
    assert relevant_lines(body, {"Zsh": ["programs.zsh.enable"]}) == []


def test_the_age_of_each_pinned_nixpkgs() -> None:
    def pin(rev: str, stamp: int | None) -> dict[str, object]:
        locked: dict[str, object] = {"rev": rev, "type": "github"}
        if stamp is not None:
            locked["lastModified"] = stamp
        return {"locked": locked}

    lock = {
        "nodes": {
            "root": {"inputs": {"nixpkgs": "nixpkgs", "nixpkgs-old": "nixpkgs-old", "nixpkgs-x": "nixpkgs-x"}},
            "nixpkgs": pin("e7439b6b14ad", int(NOW) - 13 * DAY),
            "nixpkgs-old": pin("1234567890ab", int(NOW) - DAY),
            "nixpkgs-x": pin("abcdefabcdef", None),
        },
        "root": "root",
    }
    assert nixpkgs_ages(lock, NOW) == [
        "nixpkgs e7439b6b, 13 days old",
        "nixpkgs-old 12345678, 1 day old",
        "nixpkgs-x abcdefab, age unknown",
    ]


def test_a_pin_inside_the_minix_node_has_an_age_too() -> None:
    lock = {
        "nodes": {
            "root": {"inputs": {"minix": "minix"}},
            "minix": {"inputs": {"nixpkgs": "nixpkgs_2"}},
            "nixpkgs_2": {"locked": {"rev": "e7439b6b14ad", "lastModified": int(NOW) - 13 * DAY}},
        },
        "root": "root",
    }
    assert nixpkgs_ages(lock, NOW) == ["nixpkgs e7439b6b, 13 days old"]
