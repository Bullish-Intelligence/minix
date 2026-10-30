"""The wrapper `minix-compile` of `nix/compiler.nix` keeps the packages of the caller's shell out of the compile.

The test builds the compiler from the flake. It needs `nix` and the sources of the lock in the store, and no network:
a missing prerequisite skips it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("nix") is None, reason="nix is not installed")

NIX = ["nix", "--extra-experimental-features", "nix-command flakes", "--offline"]


@pytest.fixture(scope="module")
def compiler() -> Path:
    done = subprocess.run(
        [*NIX, "build", "--no-write-lock-file", ".#compiler", "--no-link", "--print-out-paths"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        pytest.skip(f"the compiler does not build here: {done.stderr[-300:]}")
    return Path(done.stdout.strip().splitlines()[-1]) / "bin" / "minix-compile"


def compile_with(compiler: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Compile a home that imports `leaked_mod`, which sits in a folder outside the workspace."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "leaked_mod.py").write_text('OWNER = "ada"\n')
    work = tmp_path / "work"
    work.mkdir()
    (work / "home.py").write_text("from minix_config import *\nimport leaked_mod\nhome = Home(user=leaked_mod.OWNER)\n")
    shell = {"NIX_PYTHONPATH": str(lib), "NIX_PYTHONPREFIX": str(lib), "NIX_PYTHONEXECUTABLE": "/bin/false"}
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("NIX_PYTHON", "PYTHON"))}
    environment.update(shell)
    return subprocess.run(
        [str(compiler), "home.py", "home.json"], cwd=work, env=environment, capture_output=True, text=True
    )


def test_a_module_on_nix_pythonpath_is_not_importable(compiler: Path, tmp_path: Path) -> None:
    done = compile_with(compiler, tmp_path)
    assert done.returncode == 1
    assert done.stderr.rstrip("\n") == "home.py:2: ModuleNotFoundError: No module named 'leaked_mod'"


def test_the_module_imports_when_the_helper_sits_in_the_workspace(compiler: Path, tmp_path: Path) -> None:
    """The control: the wrapper still serves a helper module of the workspace."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "leaked_mod.py").write_text('OWNER = "ada"\n')
    (work / "home.py").write_text("from minix_config import *\nimport leaked_mod\nhome = Home(user=leaked_mod.OWNER)\n")
    done = subprocess.run(
        [str(compiler), "home.py", "home.json"],
        cwd=work,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
    )
    assert (done.returncode, done.stderr) == (0, "")
