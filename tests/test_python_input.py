"""The Python code of an input (`Input(python=...)`): build, compile, evaluation, and errors.

The test makes two local inputs: `comps` is a flake, and `tools` is not (`flake=False`). It builds their input packages
with the real `lib.inputPackages` and the real `nix/input-packages.nix` of the locked pynix. Then it compiles a `home.py`
that imports their code. The test needs `nix` and the pinned nixpkgs and pynix in the store, and no network. A missing
prerequisite skips it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from minix_config import _base
from minix_config import compile as compiler

ROOT = Path(__file__).resolve().parents[1]
NIX = ["nix", "--extra-experimental-features", "nix-command flakes", "--offline"]
IMPORT = "from minix_config import *\n"

pytestmark = pytest.mark.skipif(shutil.which("nix") is None, reason="nix is not installed")

COMPS = """\
from minix_config import File


def motd(text):
    return File(".motd", text)


def broken():
    raise RuntimeError("boom")
"""
TOOLS = "def banner(text):\n    return text.upper()\n"

#: The inputs of the stub, as Nix sees them: a flake and a plain source tree. `@ROOT@` is this repository.
INPUTS = """
let
  lock = (builtins.fromJSON (builtins.readFile "@ROOT@/flake.lock")).nodes;
  fetch = node: builtins.fetchTree (builtins.removeAttrs node.locked [ "dir" ]);
  pkgs = import (fetch lock.nixpkgs) { system = "x86_64-linux"; };
  minix = import "@ROOT@/lib/home.nix" {
    self = null;
    inputs = {
      nixpkgs = { inherit (pkgs) lib; legacyPackages.x86_64-linux = pkgs; rev = lock.nixpkgs.locked.rev; };
      home-manager = { };
      pynix = fetch lock.pynix;
    };
  };
  comps = builtins.getFlake "path:@WORLD@/comps";
  tools = builtins.fetchTree { type = "path"; path = "@WORLD@/tools"; };
in
"""
BUILD = (
    INPUTS
    + """
minix.inputPackages {
  extraInputs = { inherit comps tools; };
  pythonDirs = { comps = "src"; tools = "lib"; };
}
"""
)
HASHES = INPUTS + "{ comps = comps.narHash; tools = tools.narHash; nixpkgs = lock.nixpkgs.locked; }"

#: `lib.home` with a stub Home Manager that returns its arguments. The stub `pynix_hm` record pairs with the stub revisions.
HOME = """
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
in
(minix.home {
  compiled = "@WORK@/home.json";
  source = "@WORK@/home.py";
  extraInputs = { comps.narHash = "@COMPS@"; tools.narHash = "@TOOLS@"; };
}).homeConfigurations.ada.extraSpecialArgs.home.user
"""
REV = "a" * 40


@dataclass(frozen=True)
class World:
    """The built input packages (`packages`), and the `narHash` of each input."""

    packages: Path
    comps: str
    tools: str
    nixpkgs: dict[str, str]


def nix(*args: str, expression: str) -> subprocess.CompletedProcess[str]:
    done = subprocess.run([*NIX, *args, "--impure", "--expr", expression], capture_output=True, text=True)
    if "fetching" in done.stderr or "unable to download" in done.stderr:
        pytest.skip(f"the pinned nixpkgs or pynix of flake.lock is not in the store: {done.stderr[-300:]}")
    assert done.returncode == 0, done.stderr[-1500:]
    return done


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    base = tmp_path_factory.mktemp("world")
    (base / "comps" / "src" / "comps").mkdir(parents=True)
    (base / "comps" / "flake.nix").write_text("{ outputs = { self }: { }; }\n")
    (base / "comps" / "src" / "comps" / "__init__.py").write_text(COMPS)
    (base / "tools" / "lib").mkdir(parents=True)
    (base / "tools" / "lib" / "tools.py").write_text(TOOLS)
    (base / "tools" / "README.md").write_text("Not Python code.\n")

    def fill(text: str) -> str:
        return text.replace("@ROOT@", str(ROOT)).replace("@WORLD@", str(base))

    link = base / "packages"
    nix("build", "--out-link", str(link), expression=fill(BUILD))
    hashes = json.loads(nix("eval", "--json", expression=fill(HASHES)).stdout)
    return World(link, hashes["comps"], hashes["tools"], hashes["nixpkgs"])


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """`compile.main` changes `sys.path` and `_base.ROOT`, and the imported code stays in `sys.modules`. Undo all three."""
    path, root = list(sys.path), _base.ROOT
    yield
    sys.path[:] = path
    _base.ROOT = root
    for name in [n for n in sys.modules if n.split(".")[0] in {"comps", "tools", "pynix_comps", "pynix_tools"}]:
        del sys.modules[name]


def lock_node(narhash: str) -> dict:
    return {"locked": {"narHash": narhash, "type": "path"}, "inputs": {"nixpkgs": "nixpkgs"}}


def workspace(tmp_path: Path, world: World, text: str) -> Path:
    """A workspace with `home.py`, a `flake.lock` that holds the built inputs, and `.basix/pynix` linked to the build."""
    work = tmp_path / "work"
    (work / ".basix").mkdir(parents=True)
    (work / ".basix" / "pynix").symlink_to(world.packages)
    nodes = {
        "root": {"inputs": {"nixpkgs": "nixpkgs", "comps": "comps", "tools": "tools"}},
        "nixpkgs": {"locked": world.nixpkgs},
        "comps": lock_node(world.comps),
        "tools": lock_node(world.tools),
    }
    (work / "flake.lock").write_text(json.dumps({"nodes": nodes, "root": "root", "version": 7}))
    (work / "home.py").write_text(text)
    return work


def compile_home(work: Path) -> tuple[int, dict | None]:
    out = work / "home.json"
    code = compiler.main(["minix-compile", str(work / "home.py"), str(out)])
    return code, json.loads(out.read_text()) if out.exists() else None


HOME_PY = (
    IMPORT
    + """\
from comps import motd
from tools import banner

home = Home(
    user="ada",
    inputs=[
        Input("comps", "path:/x/comps", python="src"),
        Input("tools", "path:/x/tools", flake=False, python="lib"),
    ],
    files=[motd("hello"), File(".banner", banner("hi"))],
)
"""
)
HOME_LINE = 5  # the line of `Home(...)` in HOME_PY: the site of its inputs


def test_a_home_imports_the_code_of_an_input(tmp_path: Path, world: World) -> None:
    work = workspace(tmp_path, world, HOME_PY)
    code, data = compile_home(work)
    assert code == 0
    assert data is not None
    files = {k: v["text"] for f in data["fragments"] for k, v in f["value"].get("file", {}).items()}
    assert files == {".motd": "hello", ".banner": "HI"}
    assert [f["source"] for f in data["fragments"]] == ["comps/__init__.py:5 (File)", "home.py:11 (File)"]
    assert data["reads"]["files"] == {}
    assert {i["name"]: i.get("python") for i in data["inputs"]} == {"comps": "src", "tools": "lib"}
    assert data["input_packages"] == {"comps": world.comps, "tools": world.tools}


def evaluate(work: Path, world: World, comps: str, tools: str) -> subprocess.CompletedProcess[str]:
    """Evaluate the home of `work` with inputs of the given `narHash` (the stub `pynix` holds only the pair record)."""
    pynix = work.parent / "pynix"
    (pynix / "src" / "pynix_hm").mkdir(parents=True, exist_ok=True)
    (pynix / "src" / "pynix_hm" / "_generation.py").write_text(
        f'SOURCE = {{\n    "home-manager": "{REV}",\n    "nixpkgs": "{REV}",\n}}\n'
    )
    expression = (
        HOME.replace("@ROOT@", str(ROOT))
        .replace("@PYNIX@", str(pynix))
        .replace("@WORK@", str(work))
        .replace("@REV@", REV)
        .replace("@COMPS@", comps)
        .replace("@TOOLS@", tools)
    )
    done = subprocess.run([*NIX, "eval", "--json", "--impure", "--expr", expression], capture_output=True, text=True)
    if "fetching" in done.stderr or "unable to download" in done.stderr:
        pytest.skip(f"the pinned nixpkgs of flake.lock is not in the store: {done.stderr[-300:]}")
    return done


def test_the_home_evaluates(tmp_path: Path, world: World) -> None:
    work = workspace(tmp_path, world, HOME_PY)
    assert compile_home(work)[0] == 0
    done = evaluate(work, world, world.comps, world.tools)
    assert done.returncode == 0, done.stderr[-1500:]
    assert json.loads(done.stdout) == "ada"


@pytest.mark.parametrize("moved", ["comps", "tools"])
def test_a_moved_input_stops_the_evaluation(tmp_path: Path, world: World, moved: str) -> None:
    """The code of an input is pinned: a lock that moved after the compile fails, also for an input that is no flake."""
    work = workspace(tmp_path, world, HOME_PY)
    assert compile_home(work)[0] == 0
    hashes = {"comps": world.comps, "tools": world.tools, moved: "sha256-NEW"}
    done = evaluate(work, world, hashes["comps"], hashes["tools"])
    assert done.returncode != 0
    assert (
        f"error: home.py:{HOME_LINE}: the package `pynix_{moved}` was made from another version of `{moved}` than flake.lock holds; "
        "run `minix apply` to make it again"
    ) in done.stderr


def test_an_error_in_the_code_of_an_input_names_its_file(
    tmp_path: Path, world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    text = HOME_PY.replace("import motd", "import broken, motd").replace("home = Home(", "broken()\nhome = Home(")
    work = workspace(tmp_path, world, text)
    code, data = compile_home(work)
    assert (code, data) == (1, None)
    line = text.splitlines().index("broken()") + 1
    assert capsys.readouterr().err.rstrip("\n") == f"comps/__init__.py:9: RuntimeError: boom (from home.py:{line})"


def test_the_build_links_the_code_next_to_the_package_records(world: World) -> None:
    """A flake=False input with `python` also gets a `pynix_NAME` record, so the pair check covers it."""
    assert sorted(p.name for p in world.packages.iterdir()) == ["comps", "pynix_comps", "pynix_tools", "tools.py"]
    assert (world.packages / "pynix_tools" / "_generation.py").is_file()
