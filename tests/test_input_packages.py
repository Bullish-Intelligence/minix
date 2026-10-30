"""`lib.inputPackages` hands the `nixpkgs-NAME` inputs to pynix as named nixpkgs pins (009-D53, 009-D54, PF-2).

The test replaces pynix with a stub that returns its arguments. It proves what minix passes, with no build. It needs
`nix` and the pinned nixpkgs library in the store, and no network: a missing prerequisite skips it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("nix") is None, reason="nix is not installed")

#: `lib` from the nixpkgs of `flake.lock`, the stub pynix `pynix`, and the function as the stub of a workspace calls it.
EXPRESSION = """
let
  lock = (builtins.fromJSON (builtins.readFile "@ROOT@/flake.lock")).nodes;
  lib = import ((builtins.fetchTree (builtins.removeAttrs lock.nixpkgs.locked [ "dir" ])) + "/lib");
  call = (import "@ROOT@/lib/home.nix" {
    self = null;
    inputs = {
      nixpkgs = { inherit lib; legacyPackages.x86_64-linux = { }; };
      home-manager = null;
      pynix = "@PYNIX@";
    };
  }).inputPackages;
  got = call { extraInputs = { demo.id = "demo"; } // (@PINS@); };
in
{ inputs = got.inputs; nixpkgsInputs = got.nixpkgsInputs or null; }
"""


def evaluate(tmp_path: Path, pins: str) -> dict:
    (tmp_path / "nix").mkdir()
    (tmp_path / "nix" / "input-packages.nix").write_text("args: args\n")
    expression = EXPRESSION.replace("@ROOT@", str(ROOT)).replace("@PYNIX@", str(tmp_path)).replace("@PINS@", pins)
    done = subprocess.run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "--offline",
            "eval",
            "--json",
            "--impure",
            "--expr",
            expression,
        ],
        capture_output=True,
        text=True,
    )
    if "fetching" in done.stderr or "unable to download" in done.stderr:
        pytest.skip(f"the pinned nixpkgs of flake.lock is not in the store: {done.stderr[-300:]}")
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout)


def test_named_nixpkgs_inputs_go_to_pynix_as_pins(tmp_path: Path) -> None:
    """Before PF-2, `nixpkgs-*` inputs were dropped, so `NixpkgsPin(name, commit)` had no package list."""
    got = evaluate(tmp_path, '{ nixpkgs-old.id = "old"; nixpkgs-older.id = "older"; }')
    assert got["inputs"] == {"demo": {"id": "demo"}}
    assert got["nixpkgsInputs"] == {"old": {"id": "old"}, "older": {"id": "older"}}


def test_an_entry_with_no_pin_passes_no_argument(tmp_path: Path) -> None:
    """A pynix that predates `nixpkgsInputs` rejects the argument, so minix leaves it out when there is no pin."""
    got = evaluate(tmp_path, "{ }")
    assert got == {"inputs": {"demo": {"id": "demo"}}, "nixpkgsInputs": None}


#: `lib.home` with a stub Home Manager that returns its arguments. The record of the stub `pynix_hm` pairs with the stub revisions.
HOME_EXPRESSION = """
let
  lock = (builtins.fromJSON (builtins.readFile "@ROOT@/flake.lock")).nodes;
  lib = import ((builtins.fetchTree (builtins.removeAttrs lock.nixpkgs.locked [ "dir" ])) + "/lib");
  minix = import "@ROOT@/lib/home.nix" {
    self = null;
    inputs = {
      nixpkgs = { inherit lib; rev = "@REV@"; legacyPackages.x86_64-linux = { }; inputs = { }; };
      home-manager = { rev = "@REV@"; inputs.nixpkgs.rev = "@REV@"; lib.homeManagerConfiguration = args: args; };
      pynix = "@PYNIX@";
    };
  };
  got = minix.home {
    compiled = "@WORK@/home.json";
    source = "@WORK@/home.py";
    extraInputs = { x = { narHash = "@NAR@"; }; };
  };
in
got.homeConfigurations.ada.extraSpecialArgs.home.user
"""


def evaluate_home(tmp_path: Path, nar: str) -> subprocess.CompletedProcess[str]:
    """Compile a home with the input `x` (package `narHash` `sha256-AAAA`), then evaluate it with an input of `nar`."""
    from minix_config import compile as compiler

    rev = "a" * 40
    pynix = tmp_path / "pynix"
    (pynix / "src" / "pynix_hm").mkdir(parents=True)
    (pynix / "src" / "pynix_hm" / "_generation.py").write_text(
        f'SOURCE = {{\n    "home-manager": "{rev}",\n    "nixpkgs": "{rev}",\n}}\n'
    )
    work = tmp_path / "work"
    package = work / ".basix" / "pynix" / "pynix_x"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    record = {"input": "x", "narHash": "sha256-AAAA", "rev": "r1", "nixpkgs": rev, "generator": "g"}
    (package / "_generation.py").write_text(f"SOURCE = {record!r}\n")
    nodes = {
        "root": {"inputs": {"nixpkgs": "nixpkgs", "x": "x"}},
        "nixpkgs": {"locked": {"narHash": "sha256-PKGS", "rev": rev}},
        "x": {"locked": {"narHash": "sha256-AAAA", "rev": "r1"}, "inputs": {"nixpkgs": "nixpkgs"}},
    }
    (work / "flake.lock").write_text(json.dumps({"nodes": nodes, "root": "root", "version": 7}))
    (work / "home.py").write_text(
        'from minix_config import *\nhome = Home(user="ada", inputs=[Input("x", "github:a/b")])\n'
    )
    path = list(sys.path)
    try:
        assert compiler.main(["minix-compile", str(work / "home.py"), str(work / "home.json")]) == 0
    finally:
        sys.path[:] = path
        for name in [n for n in sys.modules if n.startswith("pynix_x")]:
            del sys.modules[name]
    expression = (
        HOME_EXPRESSION.replace("@ROOT@", str(ROOT))
        .replace("@PYNIX@", str(pynix))
        .replace("@WORK@", str(work))
        .replace("@REV@", rev)
        .replace("@NAR@", nar)
    )
    done = subprocess.run(
        [
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "--offline",
            "eval",
            "--json",
            "--impure",
            "--expr",
            expression,
        ],
        capture_output=True,
        text=True,
    )
    if "fetching" in done.stderr or "unable to download" in done.stderr:
        pytest.skip(f"the pinned nixpkgs of flake.lock is not in the store: {done.stderr[-300:]}")
    return done


def test_compile_records_the_input_package_hash(tmp_path: Path) -> None:
    done = evaluate_home(tmp_path, "sha256-AAAA")
    assert done.returncode == 0, done.stderr[-1500:]
    assert json.loads(done.stdout) == "ada"
    assert json.loads((tmp_path / "work" / "home.json").read_text())["input_packages"] == {"x": "sha256-AAAA"}


def test_a_lock_that_moved_after_the_compile_stops_the_evaluation(tmp_path: Path) -> None:
    """The guard does not read `flake.lock`: only `input_packages` catches a new input with an old `home.json`."""
    done = evaluate_home(tmp_path, "sha256-NEW")
    assert done.returncode != 0
    assert (
        "error: home.py:2: the package `pynix_x` was made from another version of `x` than flake.lock holds; "
        "run `minix apply` to make it again"
    ) in done.stderr
