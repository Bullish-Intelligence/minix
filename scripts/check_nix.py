#!/usr/bin/env python3
"""The Nix checks of minix: `lib.home`, the stub, the compiler package, and one `Input` on Home Manager.

Run from the repository root, through the devenv: `devenv shell -- uv run --no-sync python scripts/check_nix.py`.
The task `test:full` runs it after pytest. Each check prints one line, `PASS` or `FAIL`, and the script exits 1 when
a check fails.

The checks (the reference drvPaths come from the spikes SG35 and SG42c):

- `reference`: the reference homes T2 and T6prog (the form-C service) give the drvPath of the spikes when the session
  hook is off, and the drvPath that this script records when it is on. The hook is the only difference. Both
  cases leave out the `minix` package (`minix.package = null`), which every home holds by default. The drvPath with the
  package depends on the source of the flake, so the script checks that the home holds the package and not a value.
- `guard`: a changed `home.py`, a changed file that it read, a new file in a folder that it listed, and a file that it
  could not find stop the evaluation with one line. A file that Git does not know stops it too.
- `pair`: a `pynix_hm` that records another Home Manager or nixpkgs stops the evaluation and names both pairs.
- `input`: `Input(...)` on Home Manager. The typed classes of the input package and a native module give one drvPath.
- `advanced`: `Force`, `Before`, `After`, and `Order` of `nixdantic` work inside `options=[...]` (L128).
- `unit_hatch`: a second piece with the same unit name adds keys to the unit of a `Service` (I7). `X-SwitchMethod` joins
  the `[Unit]` section, and `ExecStart` and `Description` stay. A second `Unit_Description` is a conflict.
- `native_program`: `Service(program=Native(...), file=...)` runs the package that a Nix file of the workspace gives.
- `input_program`: the package of an input is the `program` of a `Service`, with no `file=`. `ExecStart=` holds the store
  path of the package of the input and its main program, not the `pkgs` package of the same name.
- `session`: a login shell loads `hm-session-vars.sh`, so `EDITOR` is set after a login.
- `desktop`: a home with `Hyprland`, `Noctalia`, `Foot`, and one `Theme` builds, holds the three config files and no
  Hyprland or Noctalia package, and gives the drvPath of a hand-written Home Manager module. A learner option beats
  the component (`Default`). `Hyprland --verify-config` accepts the Lua when the program is on `PATH` or in
  `$MINIX_HYPRLAND`; else the line says `SKIP`.
- `zellij`: the Zellij of `TerminalWorkspace` is `packages.<system>.zellij` of the locked input `pyllij`, at version
  `ZELLIJ_VERSION`, with no settings and no shell integration. A scratch `pyllij` input moves the home's Zellij with it.
- `compiler`: `minix-compile` runs with an empty environment. `minix` runs, and `aarch64-linux` evaluates.

- `named`: a second, named nixpkgs end to end (PF-2): `NixpkgsPin("old", COMMIT)` and `Program(old.pkgs.hello)` give the input
  `nixpkgs-old`, the package list `pynix_nixpkgs_old`, a compile, and a home that holds the `hello` of that commit. The
  case says `SKIP` when the pynix pin predates `NixpkgsPin`.

`MINIX_PYNIX_PATH=FOLDER` replaces the pin of `pynix` with a source folder (a pynix that no release holds yet). The
check of the shipped lock then differs from `flake.lock`, by design.

Name checks as arguments to run only those (`check_nix.py desktop`). Without an argument all checks run.

The scratch folder is `$MINIX_WORK` (default `minix-check` in the temporary folder). The raw log is
`$MINIX_LOGS/check_nix.log` (default `~/.local/state/basix/minix/check`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILES = ["flake.nix", "flake.lock", "pyproject.toml", "uv.lock", "README.md", "lib", "nix", "src"]
SYSTEM = "x86_64-linux"
# The Zellij that pyllij defines and measures (Zellij 0.45.1 from the shared nixpkgs pin). Raise it when pyllij moves.
ZELLIJ_VERSION = "0.45.1"

# The drvPath hashes of the reference homes of the spikes (SG35 and SG42c). The session hook is off, and so is the
# `minix` package (`minix.package = null`): the spikes had neither.
REFERENCE = {"T2": "gad8720wy5vda0h1dvivs1kibgvkbsgj", "T6prog": "jxga671wa3mnj1qpff9cvlwg2wpfjqvy"}
# The same homes with the session hook on (the default) and still no `minix` package. The hook adds `.profile`,
# `.bashrc`, and `.bash_profile`.
HOOKED = {"T2": "x4i79zjg63izd5v5ivymy34pl56i58qs", "T6prog": "fm810hazigccppm2khwhyryi0iqlys9q"}


WORK = Path(os.environ.get("MINIX_WORK", Path(tempfile.gettempdir()) / "minix-check"))
LOGS = Path(os.environ.get("MINIX_LOGS", Path.home() / ".local/state/basix/minix/check"))
LOG: list[str] = []
FAILED: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)
    LOG.append(line)


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, check: bool = True) -> str:
    """Run a command. Return its standard output. Raise with the standard error when it fails and `check` is set."""
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    LOG.append(f"$ {' '.join(command)}  (exit {result.returncode})\n{result.stdout}{result.stderr}")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed ({result.returncode}): {result.stderr.strip()[-1500:]}")
    return result.stdout if check else result.stdout + result.stderr


def nix(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return run(["nix", "--extra-experimental-features", "nix-command flakes", *args], cwd=cwd, check=check)


def hash_of(drv: str) -> str:
    return Path(drv.strip()).name.split("-", 1)[0]


# --- the scratch tree -----------------------------------------------------------------------------------------


def prepare_source() -> Path:
    """A clean copy of the files that the flake reads. A `path:` flake of the repository would copy `.devenv` too."""
    target = WORK / "src"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name in SPEC_FILES:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target / name)
    override = os.environ.get("MINIX_PYNIX_PATH")
    if override:
        nix("flake", "lock", "--override-input", "pynix", f"path:{override}", cwd=target)
    return target


class Tools:
    """The compiler package and the stub writer, as the command uses them."""

    def __init__(self, source: Path) -> None:
        self.source = source
        out = nix("build", "--no-link", "--print-out-paths", f"path:{source}#compiler").split()
        self.compiler = Path(out[-1]) / "bin" / "minix-compile"

    def workspace(self, name: str, home_py: str, files: dict[str, str] | None = None, lock: bool = True) -> Path:
        """A workspace: `home.py`, the managed stub, the compiled JSON, and the lock."""
        ws = WORK / "ws" / name
        shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        (ws / "home.py").write_text(home_py)
        for relative, text in (files or {}).items():
            (ws / relative).parent.mkdir(parents=True, exist_ok=True)
            (ws / relative).write_text(text)
        run([sys.executable, "-m", "minix_config.stub", str(ws), "--url", f"path:{self.source}"])
        if lock:
            nix("flake", "lock", cwd=ws)
            self.compile(ws)
        return ws

    def compile(self, ws: Path) -> None:
        run([str(self.compiler), "home.py", "home.json"], cwd=ws)

    def drv(self, ws: Path, user: str = "learner", flake: str | None = None) -> str:
        return hash_of(
            nix(
                "eval",
                "--raw",
                f"{flake or 'path:' + str(ws)}#homeConfigurations.{user}.activationPackage.drvPath",
                cwd=ws,
            )
        )

    def eval_error(self, ws: Path, flake: str | None = None, *extra: str) -> str:
        return nix(
            "eval",
            "--raw",
            f"{flake or 'path:' + str(ws)}#homeConfigurations.learner.activationPackage.drvPath",
            *extra,
            cwd=ws,
            check=False,
        )


def with_module(ws: Path, suffix: str, module: str) -> Path:
    """The same workspace with one more `extraModules` entry in the stub (a copy, for the check)."""
    copy = ws.with_name(ws.name + suffix)
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(ws, copy)
    stub = (copy / "flake.nix").read_text()
    line = '        system = "x86_64-linux";' if "inherit extraInputs" in stub else '      system = "x86_64-linux";'
    assert line in stub, "the stub has no system line"
    (copy / "flake.nix").write_text(stub.replace(line, line + f" extraModules = [ {module} ];"))
    return copy


def without_hook(ws: Path) -> Path:
    """The same workspace with the session hook off and the `minix` package left out (the spikes had neither)."""
    return with_module(ws, "-nohook", "{ programs.bash.enable = false; minix.package = null; }")


def without_package(ws: Path) -> Path:
    """The same workspace with the `minix` package left out and the session hook on."""
    return with_module(ws, "-nopackage", "{ minix.package = null; }")


# --- the checks -----------------------------------------------------------------------------------------------


def check_reference(tools: Tools) -> None:
    minix_out = nix("eval", "--raw", f"path:{tools.source}#packages.{SYSTEM}.minix.outPath").strip()
    for case in ("T2", "T6prog"):
        ws = tools.workspace(case, (ROOT / "tests/fixtures" / case / "home.py").read_text())
        plain = tools.drv(without_hook(ws))
        hooked = tools.drv(without_package(ws))
        full = tools.drv(ws)
        expect(
            f"reference {case} with the hook off equals the spike",
            plain == REFERENCE[case],
            f"{plain} (spike {REFERENCE[case]})",
        )
        expect(
            f"reference {case} with the hook on is the recorded drvPath",
            hooked == HOOKED[case],
            f"{hooked} (recorded {HOOKED[case]})",
        )
        expect(f"reference {case}: the hook changes the drvPath", hooked != plain, f"{hooked} against {plain}")
        # The default home holds the `minix` command of the flake (L113). Its drvPath follows the source, so the
        # script records it in the log and checks only that the package is in the list and moves the drvPath.
        packages = json.loads(
            nix(
                "eval",
                "--json",
                f"path:{ws}#homeConfigurations.learner.config.home.packages",
                "--apply",
                "ps: map (p: p.outPath) ps",
                cwd=ws,
            )
        )
        expect(
            f"reference {case}: the home holds the minix package of its flake",
            minix_out in packages,
            f"{minix_out} not in {packages}",
        )
        expect(f"reference {case}: the minix package changes the drvPath", full != hooked, f"{full} against {hooked}")
        say(f"     reference {case} with the hook on and the minix package {full}")


def check_more_cases(tools: Tools) -> None:
    sleep = tools.workspace("SLEEP", (ROOT / "tests/fixtures/SLEEP/home.py").read_text())
    expect("SLEEP (file= on a service) evaluates", len(tools.drv(sleep)) == 32, tools.drv(sleep))
    full = tools.workspace("N1", (ROOT / "tests/fixtures/N1/home.py").read_text())
    expect(
        "N1 (Neovim, Files, TerminalWorkspace, Git, File, Service) evaluates",
        len(tools.drv(full)) == 32,
        tools.drv(full),
    )


def check_guard(tools: Tools) -> None:
    base = (ROOT / "tests/fixtures/T2/home.py").read_text()
    # 1. The entry changed after the compile.
    ws = tools.workspace("guard-entry", base)
    (ws / "home.py").write_text(base + "# changed\n")
    out = tools.eval_error(ws)
    expect(
        "guard: a changed home.py stops with one line",
        "error: home.py changed: run `minix apply`" in out,
        first_error(out),
    )
    # 2. A file that the entry read changed; a file appeared in a listed folder; a file it looked for appeared.
    reader = (
        "from minix_config import *\n"
        "import os\n"
        'note = open("note.txt").read()\n'
        'parts = sorted(os.listdir("parts"))\n'
        "try:\n"
        '    later = open("later.txt").read()\n'
        "except FileNotFoundError:\n"
        "    later = ''\n"
        'home = Home(user="learner", files=[File(".config/note", note + " ".join(parts) + str(later))])\n'
    )

    def make(name: str) -> Path:
        ws = tools.workspace(name, reader, {"note.txt": "one\n", "parts/a.conf": "a"}, lock=False)
        nix("flake", "lock", cwd=ws)
        run([str(tools.compiler), "home.py", "home.json"], cwd=ws)
        return ws

    ws = make("guard-read")
    expect("guard: the compiled reader evaluates", len(tools.drv(ws)) == 32, "")
    (ws / "note.txt").write_text("two\n")
    out = tools.eval_error(ws)
    expect(
        "guard: a changed file that the entry read stops with one line",
        "error: note.txt changed: run `minix apply`" in out,
        first_error(out),
    )
    ws = make("guard-dir")
    (ws / "parts" / "b.conf").write_text("b")
    out = tools.eval_error(ws)
    expect(
        "guard: a new file in a listed folder stops",
        "error: parts/ now holds other files: run `minix apply`" in out,
        first_error(out),
    )
    ws = make("guard-absent")
    (ws / "later.txt").write_text("x")
    out = tools.eval_error(ws)
    expect(
        "guard: a file that appeared stops",
        "error: later.txt appeared (the last compile did not find it): run `minix apply`" in out,
        first_error(out),
    )
    # 3. A file that Git does not know: a Git flake sees tracked files only.
    ws = make("guard-git")
    run(["git", "init", "-q"], cwd=ws)
    run(["git", "add", "-A"], cwd=ws)
    run(["git", "rm", "-q", "--cached", "note.txt"], cwd=ws)
    out = tools.eval_error(ws, f"git+file://{ws}")
    wanted = "error: note.txt is not in the flake. Run `git add note.txt`, then `minix apply`"
    expect("guard: a file that Git does not know stops with the git add line", wanted in out, first_error(out))
    # 4. A read outside the workspace stops the compile.
    outside = tools.workspace(
        "guard-outside",
        "from minix_config import *\nopen('/etc/hostname').read()\nhome = Home(user='learner')\n",
        lock=False,
    )
    out = run([str(tools.compiler), "home.py", "home.json"], cwd=outside, check=False)
    expect(
        "guard: a read outside the workspace stops the compile",
        "reads /etc/hostname, which is outside the workspace" in out,
        out.strip()[:200],
    )


def check_pair(tools: Tools) -> None:
    flake = f'(builtins.getFlake "path:{tools.source}")'
    mismatch = nix(
        "eval",
        "--json",
        "--impure",
        "--expr",
        f'{flake}.lib.pairCheck {{ recorded = {{ home-manager = "{"a" * 40}"; nixpkgs = "{"b" * 40}"; }}; '
        f'locked = {{ home-manager = "{"a" * 40}"; nixpkgs = "{"c" * 40}"; hm-nixpkgs = "{"c" * 40}"; }}; }}',
    )
    expect(
        "pair: a mismatch names both pairs",
        "Home Manager aaaaaaaaaaaa and nixpkgs bbbbbbbbbbbb" in mismatch and "nixpkgs cccccccccccc" in mismatch,
        mismatch.strip(),
    )
    match = nix(
        "eval",
        "--json",
        "--impure",
        "--expr",
        f'{flake}.lib.pairCheck {{ recorded = {{ home-manager = "{"a" * 40}"; nixpkgs = "{"b" * 40}"; }}; '
        f'locked = {{ home-manager = "{"a" * 40}"; nixpkgs = "{"b" * 40}"; hm-nixpkgs = "{"b" * 40}"; }}; }}',
    )
    expect("pair: a match gives null", match.strip() == "null", match.strip())
    # End to end: the same stub, with a `pynix` whose `pynix_hm` records another nixpkgs.
    fake = WORK / "fake-pynix"
    shutil.rmtree(fake, ignore_errors=True)
    real = pynix_source()
    (fake / "src" / "pynix_hm").mkdir(parents=True)
    shutil.copy2(real / "src" / "decoder.nix", fake / "src" / "decoder.nix")
    record = (real / "src" / "pynix_hm" / "_generation.py").read_text()
    other = "0" * 40
    old_nixpkgs = [line for line in record.splitlines() if '"nixpkgs"' in line][0].split('"')[3]
    (fake / "src" / "pynix_hm" / "_generation.py").write_text(record.replace(old_nixpkgs, other))
    ws = tools.workspace("pair", (ROOT / "tests/fixtures/T2/home.py").read_text())
    out = tools.eval_error(ws, None, "--override-input", "minix/pynix", f"path:{fake}", "--no-write-lock-file")
    expect(
        "pair: pynix_hm from another nixpkgs stops the evaluation",
        f"nixpkgs {other[:12]}" in out
        and f"nixpkgs {old_nixpkgs[:12]}" in out
        and "pynix_hm was generated from" in out,
        first_error(out),
    )
    expect("pair: the same stub evaluates with the real pynix", len(tools.drv(ws)) == 32, "")


def pynix_source() -> Path:
    """The locked `pynix` source: the store path of the node `pynix` of `flake.lock`, or `$MINIX_PYNIX_PATH`."""
    if os.environ.get("MINIX_PYNIX_PATH"):
        return Path(os.environ["MINIX_PYNIX_PATH"])
    node = json.loads((ROOT / "flake.lock").read_text())["nodes"]["pynix"]["locked"]
    expr = f"(builtins.fetchTree (builtins.fromJSON ''{json.dumps(node)}'')).outPath"
    return Path(nix("eval", "--raw", "--impure", "--expr", expr).strip())


def pynix_names_pins() -> bool:
    """Whether the pynix of the lock has the class `NixpkgsPin` and builds a package list for it (`nixpkgsInputs`)."""
    source = pynix_source()
    return (
        "nixpkgsInputs" in (source / "nix" / "input-packages.nix").read_text()
        and "class NixpkgsPin" in (source / "src" / "pynix_native" / "nixpkgs.py").read_text()
    )


DEMO_HOME = """from minix_config import *
import pynix_demo as demo

home = Home(
    user="learner",
    programs=[Program(pkgs.ripgrep)],
    inputs=[Input("demo", "path:{url}", modules=["demo"])],
    options=[demo.Demo(enable=True, greeting="hi", count=2)],
)
"""
DEMO_NATIVE_HOME = """from minix_config import *

home = Home(
    user="learner",
    programs=[Program(pkgs.ripgrep)],
    native_modules=[NativeModule("demo.nix")],
    inputs=[Input("demo", "path:{url}")],
)
"""
DEMO_NATIVE = """{ inputs, ... }:
{
  imports = [ inputs.demo.homeManagerModules.demo ];
  programs.demo = { enable = true; greeting = "hi"; count = 2; };
}
"""


def check_input(tools: Tools) -> None:
    demo = WORK / "inputs" / "demo"
    shutil.rmtree(demo, ignore_errors=True)
    shutil.copytree(ROOT / "tests/fixtures/inputs/demo", demo)
    url = str(demo)
    # The typed home: stub, lock, input package, then the compile that pairs the package with the lock.
    typed = tools.workspace("input-typed", DEMO_HOME.format(url=url), lock=False)
    stub = (typed / "flake.nix").read_text()
    expect(
        "input: the stub follows nixpkgs and home-manager",
        'inputs.demo.inputs.nixpkgs.follows = "nixpkgs";' in stub
        and 'inputs.demo.inputs.home-manager.follows = "minix/home-manager";' in stub,
        "",
    )
    nix("flake", "lock", cwd=typed)
    (typed / ".basix").mkdir()
    nix("build", "--out-link", ".basix/pynix", f"path:{typed}#packages.{SYSTEM}.input-packages", cwd=typed)
    classes = sorted(p.name for p in (typed / ".basix/pynix").iterdir())
    expect("input: the input package pynix_demo is made", classes == ["pynix_demo"], str(classes))
    tools.compile(typed)
    compiled = json.loads((typed / "home.json").read_text())
    expect(
        "input: the compiled JSON names the module",
        compiled["inputs"][0]["modules"] == ["demo"],
        str(compiled["inputs"]),
    )
    typed_drv = tools.drv(typed)
    # The native home: the same values through a native module.
    native = tools.workspace("input-native", DEMO_NATIVE_HOME.format(url=url), {"demo.nix": DEMO_NATIVE}, lock=False)
    nix("flake", "lock", cwd=native)
    (native / ".basix").mkdir()
    nix("build", "--out-link", ".basix/pynix", f"path:{native}#packages.{SYSTEM}.input-packages", cwd=native)
    tools.compile(native)
    native_drv = tools.drv(native)
    expect(
        "input: typed classes and a native module give one drvPath",
        typed_drv == native_drv,
        f"{typed_drv} against {native_drv}",
    )
    # The value reaches Nix: another value gives another drvPath.
    changed = DEMO_HOME.format(url=url).replace('greeting="hi"', 'greeting="yo"')
    (typed / "home.py").write_text(changed)
    tools.compile(typed)
    expect("input: a changed typed value changes the drvPath", tools.drv(typed) != typed_drv, "")
    # A `NixpkgsPin(...)` pin next to an input: the input follows it, and it gets no class package (009-D53).
    pin = json.loads((ROOT / "flake.lock").read_text())["nodes"]["nixpkgs"]["locked"]["rev"]
    pinned_home = (
        DEMO_NATIVE_HOME.format(url=url)
        .replace(
            'inputs=[Input("demo", "path:' + url + '")]',
            f'inputs=[Input("demo", "path:{url}", nixpkgs=old)]',
        )
        .replace("home = Home(", f'old = NixpkgsPin("old", "{pin}")\n\nhome = Home(')
    )
    pinned = tools.workspace("input-pin", pinned_home, {"demo.nix": DEMO_NATIVE}, lock=False)
    stub = (pinned / "flake.nix").read_text()
    expect(
        "input: NixpkgsPin(...) becomes the input nixpkgs-old, and the input follows it",
        f'inputs.nixpkgs-old.url = "github:NixOS/nixpkgs/{pin}";' in stub
        and 'inputs.demo.inputs.nixpkgs.follows = "nixpkgs-old";' in stub,
        "",
    )
    nix("flake", "lock", cwd=pinned)
    (pinned / ".basix").mkdir()
    build = ["build", "--out-link", ".basix/pynix", f"path:{pinned}#packages.{SYSTEM}.input-packages"]
    if pynix_names_pins():
        nix(*build, cwd=pinned)
        made = sorted(p.name for p in (pinned / ".basix/pynix").iterdir())
        expect(
            "input: a nixpkgs pin gets a package list and no class package",
            made == ["pynix_demo", "pynix_nixpkgs_old"],
            str(made),
        )
    else:
        # A pynix that predates `nixpkgsInputs` cannot build the list of the pin: the build stops, and the line says why.
        text = nix(*build, cwd=pinned, check=False)
        expect(
            "input: a pynix without `nixpkgsInputs` refuses a nixpkgs pin",
            "nixpkgsInputs" in text,
            first_error(text),
        )
    # A stale package: the input moves and the lock follows, but the package stays. The compile names the input.
    (demo / "module.nix").write_text((demo / "module.nix").read_text() + "\n# moved\n")
    nix("flake", "update", "demo", cwd=typed)
    result = subprocess.run([str(tools.compiler), "home.py", "home.json"], cwd=typed, capture_output=True, text=True)
    expect(
        "input: a package from another version of the input stops the compile",
        result.returncode == 1
        and "was made from another version of `demo` than flake.lock holds; run `minix apply` to make it again"
        in result.stderr,
        result.stderr.strip(),
    )


OLD_COMMIT = "d233902339c02a9c334e7e593de68855ad26c4cb"
OLD_HASH = "sha256-30sZNZoA1cqF5JNO9fVX+wgiQYjB7HJqqJ4ztCDeBZE="
NAMED_HOME = """from minix_config import *

old = NixpkgsPin("old", "{commit}")

home = Home(user="learner", programs=[Program(old.pkgs.hello)])
"""


def check_named(tools: Tools) -> None:
    """A second, named nixpkgs end to end, as `minix apply` runs it: the stub, the lock, the package list, the compile,
    and the home with a package of the pinned commit (PF-2)."""
    if not pynix_names_pins():
        say("SKIP named: the pynix pin predates `NixpkgsPin`; re-pin pynix, or set MINIX_PYNIX_PATH")
        return
    ws = tools.workspace("named", NAMED_HOME.format(commit=OLD_COMMIT), lock=False)
    expect(
        "named: the stub holds the input nixpkgs-old",
        f'inputs.nixpkgs-old.url = "github:NixOS/nixpkgs/{OLD_COMMIT}";' in (ws / "flake.nix").read_text(),
        "",
    )
    nix("flake", "lock", cwd=ws)
    (ws / ".basix").mkdir()
    nix("build", "--out-link", ".basix/pynix", f"path:{ws}#packages.{SYSTEM}.input-packages", cwd=ws)
    made = sorted(p.name for p in (ws / ".basix/pynix").iterdir())
    names = json.loads((ws / ".basix/pynix/pynix_nixpkgs_old/names.json").read_text())
    expect(
        "named: the package holds pynix_nixpkgs_old with the name `hello`",
        made == ["pynix_nixpkgs_old"] and names.get("hello") == ["hello", 1],
        str(made),
    )
    tools.compile(ws)
    apply = (
        '(ps: builtins.concatStringsSep " " (map (p: p.drvPath) (builtins.filter (p: (p.pname or "") == "hello") ps)))'
    )
    got = nix("eval", "--raw", f"path:{ws}#homeConfigurations.learner.config.home.packages", "--apply", apply, cwd=ws)
    want = nix(
        "eval",
        "--raw",
        "--impure",
        "--expr",
        f'(import (builtins.fetchTree {{ type = "github"; owner = "NixOS"; repo = "nixpkgs"; rev = "{OLD_COMMIT}"; '
        f'narHash = "{OLD_HASH}"; }}) {{ system = "{SYSTEM}"; }}).hello.drvPath',
    )
    expect("named: the home holds the `hello` of the pinned commit", got.strip() == want.strip() != "", f"{got} {want}")


ADVANCED_HOME = """from minix_config import *
from nixdantic import After, Before, Force, Order
from pynix_hm.programs.bash import Bash

home = Home(
    user="learner",
    options=[
        Bash(enable=True, historySize={force}, initExtra="# normal 1000\\n"),
        Bash(historySize=10, initExtra=Before("# before 500\\n")),
        Bash(initExtra=After("# after 1500\\n")),
        Bash(initExtra=Order(2000, "# late 2000\\n")),
    ],
)
"""


def check_advanced(tools: Tools) -> None:
    """The advanced import (L128): `Force` wins over a plain value, and `Before`, `After`, `Order` order a text."""
    ws = tools.workspace("advanced", ADVANCED_HOME.format(force="Force(20)"))
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    )
    bashrc = (Path(out.split()[-1]) / "home-files" / ".bashrc").read_text()
    expect(
        "advanced: Force(20) wins over the plain value 10", "HISTSIZE=20" in bashrc and "HISTSIZE=10" not in bashrc, ""
    )
    marks = [line for line in bashrc.splitlines() if line.startswith("# ") and line.endswith(("00", "000"))]
    expect(
        "advanced: Before, After, and Order put the lines in order",
        marks == ["# before 500", "# normal 1000", "# after 1500", "# late 2000"],
        str(marks),
    )
    say(f"     advanced drvPath {tools.drv(ws)}")
    # Without `Force` the two values conflict: Nix names the option.
    plain = tools.workspace("advanced-plain", ADVANCED_HOME.format(force="20"))
    text = tools.eval_error(plain)
    expect(
        "advanced: without Force the two values conflict",
        "programs.bash.historySize" in text and "conflicting definition values" in text,
        first_error(text),
    )


HATCH_HOME = """from minix_config import *
from pynix_hm.systemd.user import User, User_Services_Value

home = Home(
    user="learner",
    services=[Service("d", program=pkgs.coreutils, file="sleep", args="1000")],
    options=[User(services={"d": User_Services_Value(@EXTRA@)})],
)
"""


def unit_section(unit: str, name: str) -> str:
    """The text of one section of a unit file. Home Manager writes the sections in any order."""
    found = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", unit, re.MULTILINE | re.DOTALL)
    return found.group(1) if found else ""


def check_unit_hatch(tools: Tools) -> None:
    """I7: a second piece for the unit `d` joins the unit of the `Service`. A key that both set is a conflict."""
    ws = tools.workspace("hatch", HATCH_HOME.replace("@EXTRA@", 'Unit_X_SwitchMethod="keep-old"'))
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    )
    unit = (Path(out.split()[-1]) / "home-files" / ".config" / "systemd" / "user" / "d.service").read_text()
    say(unit.rstrip())
    head = unit_section(unit, "Unit")
    expect("unit_hatch: X-SwitchMethod=keep-old is in [Unit]", "X-SwitchMethod=keep-old" in head, unit)
    expect("unit_hatch: Description=minix service d stays", "Description=minix service d\n" in head, unit)
    expect("unit_hatch: ExecStart= stays", "ExecStart=" in unit_section(unit, "Service"), unit)
    clash = tools.workspace("hatch-clash", HATCH_HOME.replace("@EXTRA@", 'Unit_Description="other"'))
    text = tools.eval_error(clash)
    expect(
        "unit_hatch: a second Unit_Description conflicts", "conflicting definition values" in text, first_error(text)
    )


NATIVE_HOME = """from minix_config import *

home = Home(user="learner", services=[Service("d", program=Native("pkg.nix", ["daemon"]), file="d", args="--a")])
"""
NATIVE_PKG = "{ pkgs, ... }: { daemon = pkgs.writeShellScriptBin \"d\" ''true''; }"


def check_native_program(tools: Tools) -> None:
    """A `Native` package is the `program` of a `Service`: `ExecStart` holds the store path of the package."""
    ws = tools.workspace("native-program", NATIVE_HOME, files={"pkg.nix": NATIVE_PKG})
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    )
    unit = (Path(out.split()[-1]) / "home-files" / ".config" / "systemd" / "user" / "d.service").read_text()
    say(unit.rstrip())
    line = next((x for x in unit.splitlines() if x.startswith("ExecStart=")), "")
    expect(
        "native_program: ExecStart= holds the store path of the package",
        re.fullmatch(r"ExecStart=/nix/store/[a-z0-9]{32}-d/bin/d --a", line) is not None,
        line,
    )


NIX_DEMO_HOME = """from minix_config import *
from pynix_nix_demo.pkgs import nix_demo_pkgs

home = Home(
    user="learner",
    services=[Service("d", program=nix_demo_pkgs.daemon, args="--a")],
    inputs=[Input("nix-demo", "path:{url}")],
)
"""


def check_input_program(tools: Tools) -> None:
    """The package of an input is the `program` of a `Service`, with no `file=`: `ExecStart=` runs the main program of
    that package, not the package of `pkgs` with the same name."""
    demo = WORK / "inputs" / "nix-demo"
    shutil.rmtree(demo, ignore_errors=True)
    shutil.copytree(ROOT / "tests/fixtures/inputs/nix-demo", demo)
    ws = tools.workspace("input-program", NIX_DEMO_HOME.format(url=demo), lock=False)
    nix("flake", "lock", cwd=ws)
    (ws / ".basix").mkdir()
    nix("build", "--out-link", ".basix/pynix", f"path:{ws}#packages.{SYSTEM}.input-packages", cwd=ws)
    tools.compile(ws)
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    )
    unit = (Path(out.split()[-1]) / "home-files" / ".config" / "systemd" / "user" / "d.service").read_text()
    say(unit.rstrip())
    line = next((x for x in unit.splitlines() if x.startswith("ExecStart=")), "")
    expect(
        "input_program: ExecStart= runs the main program of the package of the input",
        re.fullmatch(r"ExecStart=/nix/store/[a-z0-9]{32}-daemon-input/bin/daemon-input-bin --a", line) is not None,
        line,
    )


DESKTOP_HOME = """from minix_config import *

theme = Theme(accent="ff8800", colours=Colours(background="1e1e2e", foreground="cdd6f4"), font="monospace", font_size=12)

home = Home(
    user="learner",
    programs=[
        Hyprland(theme=theme, gaps_out=40, border_size=4, binds=[Bind("SUPER", "T", program=pkgs.foot, args="-T minix-terminal")]),
        Noctalia(theme=theme, bar_thickness=28),
        Foot(theme=theme),
        Git(name="Ada Lovelace", email="ada@example.org"),
        Neovim(default_editor=True, ty=False),
    ],
)
"""
DESKTOP_NATIVE_HOME = """from minix_config import *

home = Home(user="learner", native_modules=[NativeModule("desktop.nix")])
"""
# The home of `DESKTOP_HOME` by hand (the file `hm/minix.nix` of the spike 007 S8, with the typed values of the check).
# `package = null` on the two programs that the machine brings. Foot has no null: Home Manager adds the package.
DESKTOP_NATIVE = """{ pkgs, ... }:
{
  wayland.windowManager.hyprland = {
    enable = true;
    package = null;
    portalPackage = null;
    configType = "lua";
    systemd.enable = false;
    extraConfig = ''
@LUA@
    '';
  };
  programs.noctalia = {
    enable = true;
    package = null;
    settings = {
      bar.main.thickness = 28;
      theme.mode = "dark";
    };
  };
  programs.foot = {
    enable = true;
    settings = {
      main.font = "monospace:size=12";
      colors-dark = { background = "1e1e2e"; foreground = "cdd6f4"; };
    };
  };
  programs.git = {
    enable = true;
    settings.user = { name = "Ada Lovelace"; email = "ada@example.org"; };
  };
  programs.neovim = { enable = true; defaultEditor = true; sideloadInitLua = true; plugins = [ ]; };
}
"""
# The change pairs of the spike S18 (A: the theme, B: a bind and the bar). Each pair gives two homes and two drvPaths.
CHANGE_HEAD = 'from minix_config import *\n\ntheme = Theme(accent={accent}, colours=Colours(background={bg}, foreground="cdd6f4"))\n'
CHANGES = {
    "A": (
        CHANGE_HEAD.format(accent='"33ccff"', bg='"1e1e2e"')
        + 'home = Home(user="learner", programs=[Hyprland(theme=theme, gaps_out=8, border_size=2), '
        "Noctalia(theme=theme, bar_thickness=44), Foot(theme=theme)])\n",
        CHANGE_HEAD.format(accent='"ff5555"', bg='"3b1e2e"')
        + 'home = Home(user="learner", programs=[Hyprland(theme=theme, gaps_out=40, border_size=6), '
        "Noctalia(theme=theme, bar_thickness=44), Foot(theme=theme)])\n",
    ),
    "B": (
        CHANGE_HEAD.format(accent='"33ccff"', bg='"1e1e2e"')
        + 'home = Home(user="learner", programs=[Hyprland(), Noctalia(bar_thickness=44), Foot()])\n',
        CHANGE_HEAD.format(accent='"33ccff"', bg='"1e1e2e"')
        + 'home = Home(user="learner", programs=[Hyprland(binds=[Bind("SUPER", "T", program=pkgs.foot, '
        'args="-T minix-terminal")]), Noctalia(bar_thickness=28), Foot()])\n',
    ),
}
OVERRIDE_HOME = """from minix_config import *
from pynix_hm.programs.foot import Foot as HmFoot
from pynix_hm.programs.noctalia import Noctalia as HmNoctalia

home = Home(
    user="learner",
    programs=[Hyprland(), Noctalia(), Foot()],
    options=[HmFoot(enable=False), HmNoctalia(enable=False)],
)
"""


def desktop_lua(basix: str) -> str:
    """The `hyprland.lua` of `DESKTOP_HOME` as it must read: the default look, then the settings. Written out here."""
    from minix_config._desktop import BASE_LUA

    base = "".join(f"    {line}\n" for line in BASE_LUA.splitlines())
    return (
        f'local basix_default = "{basix}"\n'
        'local default_file = io.open(basix_default, "r")\n'
        "if default_file then\n"
        "    default_file:close()\n"
        "    dofile(basix_default)\n"
        "else\n"
        f"{base}"
        "end\n"
        'hl.config({ general = { gaps_out = 40, border_size = 4, col = { active_border = "rgb(ff8800)" } } })\n'
        'hl.bind("SUPER + T", hl.dsp.exec_cmd("${pkgs.foot}/bin/foot -T minix-terminal"))\n'
    )


def build_home(tools: Tools, ws: Path) -> Path:
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    )
    return Path(out.split()[-1])


def hyprland_program() -> str | None:
    """The program `Hyprland` for the check of the Lua: `$MINIX_HYPRLAND`, else the PATH. None when there is none."""
    return os.environ.get("MINIX_HYPRLAND") or shutil.which("Hyprland")


def verify_lua(program: str, lua: Path, scratch: Path) -> tuple[int, str]:
    """`Hyprland --verify-config` on a file, with a private runtime folder. Return the exit code and the output."""
    runtime = scratch / "runtime"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime)}
    done = subprocess.run([program, "--verify-config", "-c", str(lua)], env=env, capture_output=True, text=True)
    LOG.append(f"$ {program} --verify-config -c {lua}  (exit {done.returncode})\n{done.stdout}{done.stderr}")
    return done.returncode, done.stdout + done.stderr


def check_desktop(tools: Tools) -> None:
    """`Hyprland`, `Noctalia`, `Foot`, and `Theme` (009-D25): the files, the missing packages, and the equality."""
    ws = tools.workspace("desktop", DESKTOP_HOME)
    out = build_home(tools, ws)
    files = out / "home-files"
    lua = (files / ".config/hypr/hyprland.lua").read_text()
    wanted = desktop_lua("/etc/xdg/hypr/hyprland.lua").replace("${pkgs.foot}", "@FOOT@")
    foot_path = re.search(r'exec_cmd\("([^"]+)/bin/foot -T minix-terminal"\)', lua)
    expect(
        "desktop: hyprland.lua is the default look and then the settings",
        foot_path is not None
        and lua.split("-- extraConfig\n", 1)[-1].rstrip("\n")
        == wanted.replace("@FOOT@", foot_path.group(1)).rstrip("\n"),
        lua[:300],
    )
    noctalia = (files / ".config/noctalia/config.toml").read_text()
    expect(
        "desktop: noctalia/config.toml holds the bar and the mode",
        "[bar.main]\nthickness = 28" in noctalia and '[theme]\nmode = "dark"' in noctalia,
        noctalia,
    )
    foot_ini = (files / ".config/foot/foot.ini").read_text()
    expect(
        "desktop: foot.ini holds the font and the colours",
        "font=monospace:size=12" in foot_ini and "background=1e1e2e" in foot_ini and "foreground=cdd6f4" in foot_ini,
        foot_ini,
    )
    closure = nix("path-info", "-r", str(out)).split()
    names = [Path(p).name.split("-", 1)[1] for p in closure]
    expect(
        "desktop: the home holds no Hyprland and no Noctalia package",
        not any(n.startswith(("hyprland-", "noctalia-")) for n in names),
        str([n for n in names if n.startswith(("hyprland", "noctalia"))]),
    )
    expect(
        "desktop: Home Manager adds the foot package (the option has no null)",
        any(n.startswith("foot-") for n in names),
        "",
    )
    typed = tools.drv(ws)
    native_ws = tools.workspace(
        "desktop-native",
        DESKTOP_NATIVE_HOME,
        {
            "desktop.nix": DESKTOP_NATIVE.replace(
                "@LUA@", textwrap.indent(desktop_lua("/etc/xdg/hypr/hyprland.lua"), "      ").rstrip("\n")
            )
        },
    )
    native = tools.drv(native_ws)
    expect(
        "desktop: typed components and a hand-written module give one drvPath",
        typed == native,
        f"{typed} against {native}",
    )
    say(f"     desktop drvPath {typed}")
    plain = tools.drv(without_hook(ws))
    say(f"     desktop drvPath with the hook off {plain}")
    # The change pairs of S18: each pair gives two homes with two drvPaths, and both Lua files pass the check.
    program = hyprland_program()
    for pair, (before, after) in CHANGES.items():
        drvs = []
        for stage, text in (("before", before), ("after", after)):
            changed = tools.workspace(f"desktop-{pair}-{stage}", text)
            drvs.append(tools.drv(changed))
            if program is not None:
                scratch = WORK / "verify" / f"{pair}-{stage}"
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
                code, said = verify_lua(
                    program, build_home(tools, changed) / "home-files/.config/hypr/hyprland.lua", scratch
                )
                expect(
                    f"desktop: change {pair} {stage}: Hyprland accepts the file",
                    code == 0 and "config ok" in said,
                    said[-300:],
                )
        expect(f"desktop: change {pair} moves the drvPath", drvs[0] != drvs[1], str(drvs))
        say(f"     change {pair} drvPath {drvs[0]} then {drvs[1]}")
    # `Default` (L42): a learner option wins over the component and raises no conflict.
    over = tools.workspace("desktop-override", OVERRIDE_HOME)
    over_out = build_home(tools, over)
    expect(
        "desktop: a learner option beats the component (enable=False gives no file)",
        not (over_out / "home-files/.config/foot/foot.ini").exists()
        and not (over_out / "home-files/.config/noctalia/config.toml").exists()
        and (over_out / "home-files/.config/hypr/hyprland.lua").exists(),
        "",
    )
    # `Hyprland --verify-config` reads the Lua, in both branches: with no Basix file, and with one.
    program = hyprland_program()
    if program is None:
        say("SKIP desktop: Hyprland --verify-config (no Hyprland on PATH and no $MINIX_HYPRLAND)")
        return
    scratch = WORK / "verify"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    code, text = verify_lua(program, files / ".config/hypr/hyprland.lua", scratch)
    expect("desktop: Hyprland accepts the file with no Basix default", code == 0 and "config ok" in text, text[-300:])
    basix = scratch / "basix-default.lua"
    basix.write_text("hl.config({ general = { gaps_in = 7 } })\n")
    with_default = scratch / "hyprland.lua"
    with_default.write_text(
        (files / ".config/hypr/hyprland.lua").read_text().replace("/etc/xdg/hypr/hyprland.lua", str(basix))
    )
    code, text = verify_lua(program, with_default, scratch)
    expect("desktop: Hyprland accepts the file with a Basix default", code == 0 and "config ok" in text, text[-300:])
    unknown = scratch / "unknown.lua"
    unknown.write_text(with_default.read_text() + "hl.config({ general = { gap_out = 40 } })\n")
    code, text = verify_lua(program, unknown, scratch)
    expect(
        "desktop: Hyprland refuses an unknown key in extra_lua",
        code != 0 and "unknown config key 'general.gap_out'" in text,
        text[-300:],
    )
    broken = scratch / "broken.lua"
    broken.write_text(with_default.read_text() + "hl.bnd(1)\n")
    code, text = verify_lua(program, broken, scratch)
    expect(
        "desktop: Hyprland refuses a wrong call in extra_lua",
        code != 0 and "attempt to call a nil value (field 'bnd')" in text,
        text[-300:],
    )


ZELLIJ_HOME = """from minix_config import *

home = Home(user="learner", programs=[TerminalWorkspace()])
"""
# A scratch `pyllij` with a package that no other input holds. It builds nothing: the check only reads its `outPath`.
MARKER_PYLLIJ = """{
  outputs = _: {
    packages.x86_64-linux.zellij = derivation {
      name = "zellij-marker-0.0.0";
      version = "0.0.0";
      system = "x86_64-linux";
      builder = "/bin/sh";
      args = [ "-c" "" ];
    };
  };
}
"""


def check_zellij(tools: Tools) -> None:
    """The Zellij of `TerminalWorkspace` comes from the input `pyllij`, and only from there (Z2)."""
    ws = tools.workspace("zellij", ZELLIJ_HOME)
    expr = f'(builtins.getFlake "path:{tools.source}").inputs.pyllij.packages.{SYSTEM}.zellij'
    locked = nix("eval", "--raw", "--impure", "--expr", f"{expr}.outPath").strip()
    version = nix("eval", "--raw", "--impure", "--expr", f"{expr}.version").strip()
    expect(f"zellij: the input pyllij holds Zellij {ZELLIJ_VERSION}", version == ZELLIJ_VERSION, version)
    config = f"path:{ws}#homeConfigurations.learner.config"
    package = nix("eval", "--raw", f"{config}.programs.zellij.package.outPath", cwd=ws).strip()
    expect(
        "zellij: programs.zellij.package is the Zellij of the input pyllij",
        package == locked,
        f"{package} against {locked}",
    )
    packages = json.loads(
        nix("eval", "--json", f"{config}.home.packages", "--apply", "ps: map (p: p.outPath) ps", cwd=ws)
    )
    expect("zellij: the home holds that Zellij and no other", packages.count(locked) == 1, str(packages))
    bash = nix("eval", "--json", f"{config}.programs.zellij.enableBashIntegration", cwd=ws).strip()
    settings = nix("eval", "--json", f"{config}.programs.zellij.settings", cwd=ws).strip()
    expect("zellij: no shell integration and no settings", (bash, settings) == ("false", "{}"), f"{bash} {settings}")
    # The flow is real: another `pyllij` gives another Zellij in the home (nixpkgs is not the source).
    marker = WORK / "pyllij-marker"
    shutil.rmtree(marker, ignore_errors=True)
    marker.mkdir(parents=True)
    (marker / "flake.nix").write_text(MARKER_PYLLIJ)
    moved = nix(
        "eval",
        "--raw",
        "--override-input",
        "minix/pyllij",
        f"path:{marker}",
        f"{config}.programs.zellij.package.version",
        cwd=ws,
    ).strip()
    expect("zellij: another pyllij input moves the Zellij of the home", moved == "0.0.0", moved)


def check_session(tools: Tools) -> None:
    home = (
        "from minix_config import *\n"
        'home = Home(user="learner", options=[hm.Home(sessionVariables={"EDITOR": "ed"})])\n'
    )
    ws = tools.workspace("session", home)
    out = nix(
        "build", "--no-link", "--print-out-paths", f"path:{ws}#homeConfigurations.learner.activationPackage", cwd=ws
    ).split()[-1]
    profile = (Path(out) / "home-files" / ".profile").read_text()
    sh = [line for line in profile.splitlines() if "hm-session-vars.sh" in line]
    expect(
        "session: .profile loads hm-session-vars.sh",
        len(sh) == 1 and sh[0].startswith('. "/nix/store/'),
        profile.strip()[:200],
    )
    path = sh[0].split('"')[1]
    expect("session: the file sets EDITOR", 'export EDITOR="ed"' in Path(path).read_text(), path)
    login = run(
        ["sh", "-c", f'. "{Path(out) / "home-files" / ".profile"}" && printf %s "$EDITOR"'],
        env={"PATH": os.environ["PATH"]},
    )
    expect("session: a login shell has EDITOR set", login == "ed", login)


def check_packages(tools: Tools) -> None:
    ws = tools.workspace("compiler-env", (ROOT / "tests/fixtures/T2/home.py").read_text(), lock=False)
    result = subprocess.run(
        [str(tools.compiler), "home.py", "home.json"], cwd=ws, env={}, capture_output=True, text=True
    )
    expect(
        "compiler: compiles T2 with an empty environment",
        result.returncode == 0 and (ws / "home.json").is_file(),
        result.stderr.strip(),
    )
    wrapper = nix("build", "--no-link", "--print-out-paths", f"path:{tools.source}#minix").split()[-1]
    version = run([f"{wrapper}/bin/minix", "--version"])
    expect("minix: the command runs", version.startswith("minix "), version.strip())
    text = Path(f"{wrapper}/bin/minix").read_text()
    expect("minix: the wrapper puts pynix on PYTHONPATH", str(pynix_source()) in text, "")
    # The command reaches the library and the engine, and `source.json` names the source for `minix init`.
    real = next(
        m for m in re.findall(r"/nix/store/[a-z0-9]+-minix-[0-9.]+/bin/minix", text) if m != f"{wrapper}/bin/minix"
    )
    python = Path(Path(real).read_text().splitlines()[0].removeprefix("#!"))
    probe = "import minix_config.stub, minix_config.entry, pynix_engine, pynix_native; print('ok')"
    result = subprocess.run(
        [str(python), "-c", probe], capture_output=True, text=True, env={"PYTHONPATH": f"{pynix_source()}/src"}
    )
    expect(
        "minix: the package imports minix_config, pynix_engine, and pynix_native",
        result.stdout.strip() == "ok",
        result.stderr.strip()[-300:],
    )
    info = json.loads((Path(wrapper) / "share/minix/source.json").read_text())
    expect(
        "minix: share/minix/source.json names the source", str(info.get("narHash", "")).startswith("sha256-"), str(info)
    )
    lock = json.loads((Path(wrapper) / "share/minix/flake.lock").read_text())
    expect(
        "minix: share/minix/flake.lock is the lock of the flake",
        lock == json.loads((ROOT / "flake.lock").read_text()),
        "the shipped lock differs from flake.lock",
    )
    other = nix("eval", "--raw", f"path:{tools.source}#packages.aarch64-linux.compiler.drvPath")
    expect("aarch64-linux: the compiler evaluates", other.endswith(".drv"), other)


def first_error(text: str) -> str:
    lines = [line for line in text.splitlines() if "error:" in line]
    return lines[-1].strip() if lines else text.strip()[:200]


def expect(name: str, ok: bool, detail: str) -> None:
    say(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def main() -> int:
    started = time.time()
    WORK.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    say(f"minix Nix checks. Scratch {WORK}")
    source = prepare_source()
    tools = Tools(source)
    steps: list[Callable[[Tools], None]] = [
        check_reference,
        check_more_cases,
        check_guard,
        check_pair,
        check_input,
        check_named,
        check_advanced,
        check_unit_hatch,
        check_native_program,
        check_input_program,
        check_desktop,
        check_session,
        check_zellij,
        check_packages,
    ]
    wanted = sys.argv[1:]  # names of checks: `check_nix.py desktop` runs only `check_desktop`
    unknown = [name for name in wanted if f"check_{name}" not in {step.__name__ for step in steps}]
    if unknown:
        say(f"FAIL unknown check: {', '.join(unknown)}")
        return 1
    for step in steps:
        if wanted and step.__name__.removeprefix("check_") not in wanted:
            continue
        try:
            step(tools)
        except Exception as error:  # a step that cannot run is a failure, with its text
            expect(f"{step.__name__} ran", False, str(error))
    say(f"{len(FAILED)} failed, {round(time.time() - started)} s")
    (LOGS / "check_nix.log").write_text("\n".join(LOG) + "\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
