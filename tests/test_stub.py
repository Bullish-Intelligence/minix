"""The stub writer: the managed `flake.nix` of a workspace (009-D3, 009-D51, 009-D53, CONCEPT Appendix C)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pynix_native import Found, discover_source

from minix_config import stub

URL = "git+https://github.com/Bullish-Intelligence/minix?ref=release"

NO_INPUT = """# Managed by minix. Do not edit.
{
  inputs.minix.url = "git+https://github.com/Bullish-Intelligence/minix?ref=release";
  outputs = { self, minix }: {
    packages.x86_64-linux.compiler = minix.packages.x86_64-linux.compiler;
    homeConfigurations = (minix.lib.home {
      compiled = ./home.json;
      source = ./home.py;
      system = "x86_64-linux";
    }).homeConfigurations;
  };
}
"""

WITH_INPUT = """# Managed by minix. Do not edit.
{
  inputs.minix.url = "git+https://github.com/Bullish-Intelligence/minix?ref=release";
  inputs.nixpkgs.follows = "minix/nixpkgs";
  inputs.example-tool.url = "github:o/example-tool";
  inputs.example-tool.inputs.nixpkgs.follows = "nixpkgs";
  inputs.example-tool.inputs.home-manager.follows = "minix/home-manager";
  outputs = inputs@{ self, minix, ... }:
    let
      extraInputs = removeAttrs inputs [ "self" "minix" "nixpkgs" ];
    in
    {
      packages.x86_64-linux.compiler = minix.packages.x86_64-linux.compiler;
      packages.x86_64-linux.input-packages = minix.lib.inputPackages {
        inherit extraInputs;
        system = "x86_64-linux";
        modules = { example-tool = [ "example-tool" ]; };
      };
      homeConfigurations = (minix.lib.home {
        compiled = ./home.json;
        source = ./home.py;
        system = "x86_64-linux";
        inherit extraInputs;
      }).homeConfigurations;
    };
}
"""


def found(text: str) -> Found:
    return discover_source(text, "home.py")


def text_of(source: str, names: dict | None = None, **kw) -> tuple[str, list[str]]:
    return stub.stub_text(found(source), URL, input_names=names, **kw)


def test_no_input_stub_is_the_text_of_the_concept() -> None:
    """The stub of a home with no `Input` does not change when the home changes in another way."""
    for source in ["", 'home = Home(user="a")', 'home = Home(user="b", programs=[Program(pkgs.hello)])']:
        assert text_of(source) == (NO_INPUT, [])


def test_first_line_is_the_managed_marker() -> None:
    assert NO_INPUT.splitlines()[0] == stub.FIRST_LINE == "# Managed by minix. Do not edit."


def test_input_follows_nixpkgs_and_home_manager() -> None:
    source = 'Input("example-tool", "github:o/example-tool", modules=["example-tool"])'
    assert text_of(source, {"example-tool": {"nixpkgs", "home-manager", "flake-utils"}}) == (WITH_INPUT, [])


def test_follows_only_for_inputs_that_exist() -> None:
    """009-D51: the tool reads the input list first, so no `follows` points at a missing input."""
    source = 'Input("a", "github:o/a")'
    text, notes = text_of(source, {"a": set()})
    assert "inputs.a.url" in text
    assert "follows" not in text.replace('inputs.nixpkgs.follows = "minix/nixpkgs";', "")
    assert notes == []
    only_pkgs, _ = text_of(source, {"a": {"nixpkgs"}})
    assert 'inputs.a.inputs.nixpkgs.follows = "nixpkgs";' in only_pkgs
    assert "home-manager" not in only_pkgs.split("outputs")[0]


def test_unreadable_input_list_gives_a_note_and_no_follows() -> None:
    text, notes = text_of('Input("a", "github:o/a")', {"a": None})
    assert 'follows = "nixpkgs"' not in text
    assert notes == ["home.py:1: could not read the inputs of `a`, so the stub has no follows line for it"]
    _, missing = text_of('Input("a", "github:o/a")')
    assert missing == notes


def test_input_that_is_not_a_flake() -> None:
    text, _ = text_of('Input("src", "github:o/src", flake=False)')
    assert 'inputs.src.url = "github:o/src";\n  inputs.src.flake = false;' in text
    assert "inputs.src.inputs" not in text
    assert "modules = { };" in text  # no input package for a source folder


def test_nixpkgs_pins_and_choices() -> None:
    """009-D53: `NixpkgsPin(...)` becomes the input `nixpkgs-NAME`; `nixpkgs=` picks it; `OWN` adds no follows."""
    commit = "d" * 40
    source = (
        f'old = NixpkgsPin("old", "{commit}")\n'
        'Input("a", "github:o/a", nixpkgs=old)\n'
        'Input("b", "github:o/b", nixpkgs=OWN)\n'
        'Input("c", "github:o/c")\n'
    )
    names = {k: {"nixpkgs"} for k in "abc"}
    text, notes = text_of(source, names)
    assert f'inputs.nixpkgs-old.url = "github:NixOS/nixpkgs/{commit}";' in text
    assert 'inputs.a.inputs.nixpkgs.follows = "nixpkgs-old";' in text
    assert "inputs.b.inputs.nixpkgs.follows" not in text
    assert 'inputs.c.inputs.nixpkgs.follows = "nixpkgs";' in text
    assert notes == []


def test_a_nixpkgs_pin_alone_still_writes_the_extra_inputs() -> None:
    commit = "d" * 40
    text, _ = text_of(f'old = NixpkgsPin("old", "{commit}")')
    assert 'inputs.nixpkgs.follows = "minix/nixpkgs";' in text
    assert "extraInputs" in text


def test_pin_with_an_input_that_has_no_nixpkgs_input_gives_a_note() -> None:
    commit = "d" * 40
    source = f'old = NixpkgsPin("old", "{commit}")\nInput("a", "github:o/a", nixpkgs=old)'
    _, notes = text_of(source, {"a": set()})
    assert notes == ["home.py:2: the input `a` has no nixpkgs input, so nixpkgs=old changes nothing for it"]


def test_home_manager_with_another_nixpkgs_warns() -> None:
    """009-D53: on minix an input with `home-manager` that follows another nixpkgs warns."""
    commit = "d" * 40
    source = f'old = NixpkgsPin("old", "{commit}")\nInput("a", "github:o/a", nixpkgs=old)\nInput("b", "github:o/b", nixpkgs=OWN)'
    text, notes = text_of(source, {"a": {"nixpkgs", "home-manager"}, "b": {"nixpkgs", "home-manager"}})
    assert text.count('home-manager.follows = "minix/home-manager";') == 2
    assert notes == [
        "home.py:2: the input `a` has home-manager and uses the nixpkgs `old`. "
        "Home Manager here goes with the nixpkgs of minix. The two may not match.",
        "home.py:3: the input `b` has home-manager and uses its own nixpkgs. "
        "Home Manager here goes with the nixpkgs of minix. The two may not match.",
    ]


def test_system_and_url_are_parameters() -> None:
    text, _ = text_of("", system="aarch64-linux")
    assert "packages.aarch64-linux.compiler = minix.packages.aarch64-linux.compiler;" in text
    assert 'system = "aarch64-linux";' in text
    other = stub.stub_text(found(""), "git+file:///srv/minix?ref=main")[0]
    assert 'inputs.minix.url = "git+file:///srv/minix?ref=main";' in other


@pytest.mark.parametrize("bad", ['x" ; evil', "no colon", "a b:c", ""])
def test_a_bad_url_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="is not a flake reference"):
        stub.stub_text(found(""), bad)


def test_a_bad_system_is_refused() -> None:
    with pytest.raises(ValueError, match="is not a Nix system"):
        stub.stub_text(found(""), URL, system="x86 64")


def test_default_url_is_the_public_form() -> None:
    assert stub.DEFAULT_MINIX_URL == "git+https://github.com/Bullish-Intelligence/minix?ref=release"
    assert stub.stub_text(found(""))[0] == NO_INPUT


def test_write_stub_writes_once(tmp_path: Path) -> None:
    first = stub.write_stub(tmp_path, found(""), URL)
    assert first.changed and first.path == tmp_path / "flake.nix" and first.warnings == ()
    assert (tmp_path / "flake.nix").read_text() == NO_INPUT
    again = stub.write_stub(tmp_path, found(""), URL)
    assert not again.changed


def test_write_stub_rewrites_when_an_input_appears(tmp_path: Path) -> None:
    stub.write_stub(tmp_path, found(""), URL)
    names = {"example-tool": {"nixpkgs", "home-manager"}}
    source = 'Input("example-tool", "github:o/example-tool", modules=["example-tool"])'
    result = stub.write_stub(tmp_path, found(source), URL, input_names=names)
    assert result.changed
    assert (tmp_path / "flake.nix").read_text() == WITH_INPUT


def test_write_stub_never_overwrites_a_file_of_the_learner(tmp_path: Path) -> None:
    (tmp_path / "flake.nix").write_text("{ outputs = _: { }; }\n")
    with pytest.raises(ValueError, match="is not a managed stub"):
        stub.write_stub(tmp_path, found(""), URL)
    assert (tmp_path / "flake.nix").read_text() == "{ outputs = _: { }; }\n"


class FakeRun:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.result = subprocess.CompletedProcess([], returncode, stdout, "")
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return self.result


def test_input_names_reads_the_inputs_of_a_flake() -> None:
    run = FakeRun(0, '["home-manager", "nixpkgs"]')
    assert stub.input_names("github:o/a", run=run) == frozenset({"home-manager", "nixpkgs"})
    command = run.commands[0]
    assert command[:3] == ["nix", "--extra-experimental-features", "nix-command flakes"]
    assert "--offline" not in command
    assert 'builtins.parseFlakeRef "github:o/a"' in command[-1]


def test_input_names_offline_and_failures() -> None:
    run = FakeRun(0, "[]")
    stub.input_names("github:o/a", offline=True, run=run)
    assert "--offline" in run.commands[0]
    assert stub.input_names("github:o/a", run=FakeRun(1, "")) is None
    assert stub.input_names("github:o/a", run=FakeRun(0, "not json")) is None


# PF-6: the list of inputs of an input honours `dir=`.

SAMPLE_URL = "git+https://github.com/Bullish-Intelligence/example-host?dir=nix/example-host&ref=main&rev=a1de8f0c0000000000000000000000000000000a"


@pytest.mark.parametrize(
    "url",
    [
        SAMPLE_URL,
        "github:o/r?dir=sub",
        "github:o/r/REV",
        "path:/x/y?dir=a/b",
        "git+file:///x/y?dir=sub&rev=0123456789abcdef0123456789abcdef01234567",
    ],
)
def test_the_input_list_expression_reads_the_flake_in_dir(url: str) -> None:
    expr = stub.input_list_expr(url)
    # The reference goes whole to the parser, so `ref`, `rev`, and the other parts stay.
    assert f'builtins.parseFlakeRef "{url}"' in expr
    # `fetchTree` gets the reference without `dir`, and the read goes to `DIR/flake.nix`.
    assert 'builtins.fetchTree (builtins.removeAttrs ref [ "dir" ])' in expr
    assert '"/${ref.dir or "."}/flake.nix"' in expr
    # The old expression read `ROOT/flake.nix` and ignored `dir`.
    assert '+ "/flake.nix"' not in expr


def test_the_input_list_reads_a_flake_in_a_subdirectory(tmp_path: Path) -> None:
    if shutil.which("nix") is None or shutil.which("git") is None:
        pytest.skip("needs nix and git")
    repo = tmp_path / "repo"
    (repo / "nix" / "sub").mkdir(parents=True)
    (repo / "nix" / "sub" / "flake.nix").write_text(
        '{ inputs.nixpkgs.url = "github:NixOS/nixpkgs"; inputs.other.url = "github:o/o"; outputs = _: { }; }\n'
    )
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
    }

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "x")
    rev = git("rev-parse", "HEAD")
    got = stub.input_names(f"git+file://{repo}?dir=nix/sub&rev={rev}")
    assert got == frozenset({"nixpkgs", "other"})


def test_module_main_writes_the_stub(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "home.py").write_text('from minix_config import *\nhome = Home(user="a")\n')
    assert stub.main([str(tmp_path), "--url", URL]) == 0
    assert capsys.readouterr().out == "flake.nix written\n"
    assert stub.main([str(tmp_path), "--url", URL]) == 0
    assert capsys.readouterr().out == "flake.nix unchanged\n"
    assert (tmp_path / "flake.nix").read_text() == NO_INPUT


def test_module_main_stops_on_a_computed_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "home.py").write_text('u = "github:o/a"\nhome = Home(user="a", inputs=[Input("a", u)])\n')
    assert stub.main([str(tmp_path)]) == 1
    assert capsys.readouterr().err == "home.py:2:31: Input(...) takes plain text and True or False only\n"
    assert not (tmp_path / "flake.nix").exists()


# 011 P3: read the inputs of an input from its locked tree in the store, before the URL.

GIT_URL = "git+file:///srv/example/gone"
LOCK_HASH = "sha256-PuV2gKPD4hiy82F5goai7t79RGSk4vEd7tzX9IBpe7w="


def lock_node(url_type: str = "git", **locked: object) -> dict[str, object]:
    return {
        "locked": {
            "type": url_type,
            "url": "file:///srv/example/gone",
            "narHash": LOCK_HASH,
            **locked,
        },
        "original": {"type": url_type, "url": "file:///srv/example/gone"},
    }


def test_the_store_path_follows_from_the_nar_hash() -> None:
    # A real pair: the path that Nix 2.34 gave to the tree with this hash.
    assert stub.fixed_source_path(LOCK_HASH) == "/nix/store/d3myhmiahganqjvh9biv37lf4dn97h4m-source"
    assert stub.fixed_source_path("sha256-short") is None
    assert stub.fixed_source_path("sha1-AAAA") is None
    assert stub.fixed_source_path("nonsense") is None


def fake_store(tmp_path: Path) -> str:
    """A store folder that holds the tree of `LOCK_HASH`, and the folder of the input is absent."""
    store = tmp_path / "store"
    source = stub.fixed_source_path(LOCK_HASH, str(store))
    assert source is not None
    Path(source).mkdir(parents=True)
    (Path(source) / "flake.nix").write_text('{ inputs.nixpkgs.url = "github:NixOS/nixpkgs"; outputs = _: { }; }\n')
    return str(store)


def test_the_locked_tree_in_the_store_is_read_offline_without_the_url(tmp_path: Path) -> None:
    store = fake_store(tmp_path)
    run = FakeRun(0, '["nixpkgs"]')
    got = stub.locked_input_names(GIT_URL, lock_node(), store_dir=store, run=run)
    assert got == frozenset({"nixpkgs"})
    command = run.commands[0]
    assert "--offline" in command
    assert str(stub.fixed_source_path(LOCK_HASH, store)) in command[-1]
    assert "fetchTree" not in command[-1]  # the folder of the URL is not touched


def test_the_locked_tree_is_not_used_without_a_hash_or_a_tree_or_for_a_path(tmp_path: Path) -> None:
    store = fake_store(tmp_path)
    run = FakeRun(0, "[]")
    no_hash = {"locked": {"type": "git"}, "original": {"type": "git"}}
    assert stub.locked_input_names(GIT_URL, no_hash, store_dir=store, run=run) is None
    assert stub.locked_input_names(GIT_URL, lock_node(), store_dir=str(tmp_path / "empty"), run=run) is None
    assert stub.locked_input_names(GIT_URL, lock_node("path"), store_dir=store, run=run) is None
    assert run.commands == []  # none of these started Nix


def test_input_names_falls_back_to_the_url_when_the_lock_does_not_help(tmp_path: Path) -> None:
    run = FakeRun(0, '["home-manager"]')
    absent = lock_node(
        narHash="sha256-" + "B" * 43 + "="
    )  # no store holds this tree (the all-A fake hash can: a failed fetch leaves it)
    got = stub.input_names(GIT_URL, locked=absent, run=run)
    assert got == frozenset({"home-manager"})
    assert len(run.commands) == 1
    assert "builtins.fetchTree" in run.commands[0][-1]


def test_input_names_prefers_the_locked_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stub, "locked_input_names", lambda url, node, **kw: frozenset({"from-lock"}))
    run = FakeRun(0, '["from-url"]')
    assert stub.input_names(GIT_URL, locked=lock_node(), run=run) == frozenset({"from-lock"})
    assert run.commands == []


def test_a_lock_for_another_reference_does_not_answer(tmp_path: Path) -> None:
    if shutil.which("nix") is None:
        pytest.skip("needs nix")
    store = fake_store(tmp_path)
    assert stub.locked_input_names(GIT_URL, lock_node(), store_dir=store) == frozenset({"nixpkgs"})  # real Nix
    other = "git+file:///srv/example/other"
    assert stub.locked_input_names(other, lock_node(), store_dir=store) is None  # the lock is for another URL
