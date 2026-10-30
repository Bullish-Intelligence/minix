"""The stub writer: the managed `flake.nix` of a minix workspace (009-D3, CONCEPT Appendix C).

The stub has one input `minix`. An `Input(...)` in the entry adds that input, with its `follows` (009-D51), and a
`NixpkgsPin(...)` adds the input `nixpkgs-NAME` (009-D53). An `Input` with `python=` also adds `pythonDirs` to the input
packages. The stub text with no `Input` does not change when the entry changes in any other way. The compiler reads the calls from the syntax tree (`pynix_native.discover`), so one apply
writes the stub, locks, makes the input packages, and compiles.

The stub writer never edits a file that a learner wrote. It owns `flake.nix` and writes it only when the text changes.

Use from the command:

    found = pynix_native.discover(workspace / "home.py")
    names = {item.name: input_names(item.url) for item in found.inputs if item.flake}
    result = write_stub(workspace, found, minix_url, system="x86_64-linux", input_names=names)

From a shell: `python -m minix_config.stub WORKSPACE [--url URL] [--system SYSTEM]`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from pynix_native import DiscoveryError, Found, FoundInput, FoundNixpkgsPin, discover

__all__ = [
    "DEFAULT_MINIX_URL",
    "DEFAULT_SYSTEM",
    "FIRST_LINE",
    "StubResult",
    "fixed_source_path",
    "input_list_expr",
    "input_names",
    "locked_input_names",
    "locked_list_expr",
    "stub_text",
    "write_stub",
]

DEFAULT_MINIX_URL = "git+https://github.com/Bullish-Intelligence/minix?ref=release"
"""The URL of `minix` in the stub (010-D15). The release channel is the branch `release`."""
DEFAULT_SYSTEM = "x86_64-linux"
FIRST_LINE = "# Managed by minix. Do not edit."

_URL = re.compile(r"[A-Za-z0-9+._:/?&=@%~#,-]+")
_SYSTEM = re.compile(r"[a-z0-9_]+-(linux|darwin)")


@dataclass(frozen=True)
class StubResult:
    """The result of `write_stub`: the file, whether its text changed, and the plain lines for the learner."""

    path: Path
    changed: bool
    warnings: tuple[str, ...]


def input_list_expr(url: str) -> str:
    """The Nix expression that lists the names in `inputs` of the `flake.nix` of the flake reference `url`.

    The `dir=` part of the reference names the folder of the flake inside the fetched source. `fetchTree` does not take
    it, so the expression removes it and reads `DIR/flake.nix`. The other parts (`ref`, `rev`) go to `fetchTree`.
    """
    return (
        f'let ref = builtins.parseFlakeRef "{url}";'
        ' src = builtins.fetchTree (builtins.removeAttrs ref [ "dir" ]);'
        ' in builtins.attrNames ((import (src + "/${ref.dir or "."}/flake.nix")).inputs or { })'
    )


_NIX32 = "0123456789abcdfghijklmnpqrsvwxyz"


def fixed_source_path(nar_hash: str, store_dir: str = "/nix/store", name: str = "source") -> str | None:
    """The store path that Nix gives to a fetched source with this `narHash` of a lock (`sha256-...`), or None.

    Nix puts a locked tree at a path that follows from its hash alone. The path is the "source" fixed-output path of a
    NAR hash: the fingerprint is hashed, folded to 20 bytes, and written in the Nix base 32.
    """
    kind, _, encoded = nar_hash.partition("-")
    try:
        digest = base64.b64decode(encoded, validate=True)
    except ValueError:
        return None
    if kind != "sha256" or len(digest) != 32:
        return None
    fingerprint = f"source:sha256:{digest.hex()}:{store_dir}:{name}"
    full = hashlib.sha256(fingerprint.encode()).digest()
    folded = bytearray(20)
    for index, byte in enumerate(full):
        folded[index % 20] ^= byte
    text = ""
    for n in range(31, -1, -1):
        bit, shift = n * 5, (n * 5) % 8
        i = bit // 8
        value = folded[i] >> shift
        if i < 19:
            value |= folded[i + 1] << (8 - shift)
        text += _NIX32[value & 0x1F]
    return f"{store_dir}/{text}-{name}"


def locked_list_expr(url: str, original: Mapping[str, object], source: str) -> str:
    """Like `input_list_expr`, but it reads the flake from the store path `source`: the locked tree of the input.

    The lock must still describe `url`: the expression stops when the original reference of the lock is another one.
    """
    return (
        f"let ref = builtins.parseFlakeRef {_nix_string(url)};"
        f" original = builtins.fromJSON {_nix_string(json.dumps(original))};"
        f" src = {_nix_string(source)};"
        " in if builtins.flakeRefToString ref != builtins.flakeRefToString original"
        ' then throw "the lock holds another reference than the URL"'
        ' else builtins.attrNames ((import (src + "/${ref.dir or "."}/flake.nix")).inputs or { })'
    )


def _eval_names(
    expr: str, offline: bool, run: Callable[..., subprocess.CompletedProcess[str]]
) -> frozenset[str] | None:
    command = [
        "nix",
        "--extra-experimental-features",
        "nix-command flakes",
        "eval",
        "--json",
        "--impure",
        "--expr",
        expr,
    ]
    if offline:
        command.insert(command.index("eval") + 1, "--offline")
    result = run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return frozenset(json.loads(result.stdout))
    except json.JSONDecodeError:
        return None


def locked_input_names(
    url: str,
    node: Mapping[str, object],
    *,
    store_dir: str = "/nix/store",
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> frozenset[str] | None:
    """The names in `inputs` of a flake input, read from the locked tree of the lock `node`. None when it cannot.

    The lock gives the hash of the tree, the hash gives the store path, and the read is offline. None means that the
    lock has no hash, the tree is not in the store, or the lock describes another reference than `url`.
    """
    locked, original = node.get("locked"), node.get("original")
    if not isinstance(locked, dict) or not isinstance(original, dict) or locked.get("type") == "path":
        return None
    nar_hash = locked.get("narHash")
    source = fixed_source_path(nar_hash, store_dir) if isinstance(nar_hash, str) else None
    if source is None or not Path(source).is_dir():
        return None
    return _eval_names(locked_list_expr(url, original, source), True, run)


def input_names(
    url: str,
    *,
    offline: bool = False,
    locked: Mapping[str, object] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> frozenset[str] | None:
    """The names in `inputs` of the `flake.nix` of a flake input, or None when Nix cannot read them (009-D51).

    The stub needs the list before it writes a `follows` line: a `follows` for an input that does not exist only warns,
    but a stub with no such line is cleaner. With the lock node of the input (`locked`), the read goes first to the
    locked tree in the store, offline (011 P3); the URL is the fallback. `run` is the seam for tests.
    """
    if locked:
        names = locked_input_names(url, locked, run=run)
        if names is not None:
            return names
    return _eval_names(input_list_expr(url), offline, run)


def _nix_string(value: str) -> str:
    """A Nix string literal. The values are checked before: this guards the Nix text anyway."""
    return json.dumps(value).replace("${", "\\${")


def _check_url(minix_url: str) -> None:
    if not _URL.fullmatch(minix_url) or ":" not in minix_url:
        raise ValueError(f"the minix URL {minix_url!r} is not a flake reference: write one such as {DEFAULT_MINIX_URL}")


def _follows(item: FoundInput, have: Collection[str] | None, system: str) -> tuple[list[str], list[str]]:
    """The input line and the `follows` lines of one flake input, and the plain lines for the learner."""
    name = item.name
    lines = [f"inputs.{name}.url = {_nix_string(item.url)};"]
    notes: list[str] = []
    if not item.flake:
        return [*lines, f"inputs.{name}.flake = false;"], notes
    if have is None:
        notes.append(f"{item.source}: could not read the inputs of `{name}`, so the stub has no follows line for it")
        return lines, notes
    choice = item.nixpkgs
    if "nixpkgs" in have:
        if choice is None:
            lines.append(f'inputs.{name}.inputs.nixpkgs.follows = "nixpkgs";')
        elif isinstance(choice, FoundNixpkgsPin):
            lines.append(f'inputs.{name}.inputs.nixpkgs.follows = "nixpkgs-{choice.name}";')
        # "own": no line, the input keeps its own nixpkgs
    elif isinstance(choice, FoundNixpkgsPin):
        notes.append(
            f"{item.source}: the input `{name}` has no nixpkgs input, so nixpkgs={choice.name} changes nothing for it"
        )
    if "home-manager" in have:
        lines.append(f'inputs.{name}.inputs.home-manager.follows = "minix/home-manager";')
        if choice is not None:  # 009-D53: Home Manager of minix goes with the nixpkgs of minix
            which = f"the nixpkgs `{choice.name}`" if isinstance(choice, FoundNixpkgsPin) else "its own nixpkgs"
            notes.append(
                f"{item.source}: the input `{name}` has home-manager and uses {which}. "
                "Home Manager here goes with the nixpkgs of minix. The two may not match."
            )
    return lines, notes


def stub_text(
    found: Found,
    minix_url: str = DEFAULT_MINIX_URL,
    *,
    system: str = DEFAULT_SYSTEM,
    input_names: Mapping[str, Collection[str] | None] | None = None,
) -> tuple[str, list[str]]:
    """The text of the managed `flake.nix` and the plain lines for the learner (notes and warnings).

    `found` is what `pynix_native.discover` read from `home.py`. `input_names` maps the name of each flake input to the
    names in its own `inputs` (see `input_names()` above); a missing name gives the note "could not read the inputs".
    """
    _check_url(minix_url)
    if not _SYSTEM.fullmatch(system):
        raise ValueError(f"the system {system!r} is not a Nix system such as {DEFAULT_SYSTEM}")
    names = input_names or {}
    header = [FIRST_LINE, "{", f"  inputs.minix.url = {_nix_string(minix_url)};"]
    call = [
        "(minix.lib.home {",
        "  compiled = ./home.json;",
        "  source = ./home.py;",
        f'  system = "{system}";',
    ]
    compiler = f"packages.{system}.compiler = minix.packages.{system}.compiler;"
    notes: list[str] = []
    if not found.inputs and not found.nixpkgs:
        body = [
            "  outputs = { self, minix }: {",
            f"    {compiler}",
            "    homeConfigurations = " + call[0],
            *("      " + line.lstrip() if i == 0 else "      " + line.strip() for i, line in enumerate(call[1:])),
            "    }).homeConfigurations;",
            "  };",
            "}",
        ]
        return "\n".join([*header, *body]) + "\n", notes
    header.append('  inputs.nixpkgs.follows = "minix/nixpkgs";')
    for pin in found.nixpkgs:
        header.append(f"  inputs.nixpkgs-{pin.name}.url = {_nix_string('github:NixOS/nixpkgs/' + pin.commit)};")
    for item in found.inputs:
        lines, more = _follows(item, names.get(item.name), system)
        header.extend("  " + line for line in lines)
        notes.extend(more)
    modules = " ".join(
        f"{i.name} = [ {' '.join(_nix_string(m) for m in i.modules)} ];" for i in found.inputs if i.flake and i.modules
    )
    python_dirs = " ".join(
        f"{i.name} = {_nix_string(i.python)};"
        for i in sorted(found.inputs, key=lambda i: i.name)
        if i.python is not None
    )
    body = [
        "  outputs = inputs@{ self, minix, ... }:",
        "    let",
        '      extraInputs = removeAttrs inputs [ "self" "minix" "nixpkgs" ];',
        "    in",
        "    {",
        f"      {compiler}",
        f"      packages.{system}.input-packages = minix.lib.inputPackages {{",
        "        inherit extraInputs;",
        f'        system = "{system}";',
        f"        modules = {{ {modules} }};" if modules else "        modules = { };",
        *([f"        pythonDirs = {{ {python_dirs} }};"] if python_dirs else []),
        "      };",
        "      homeConfigurations = (minix.lib.home {",
        "        compiled = ./home.json;",
        "        source = ./home.py;",
        f'        system = "{system}";',
        "        inherit extraInputs;",
        "      }).homeConfigurations;",
        "    };",
        "}",
    ]
    return "\n".join([*header, *body]) + "\n", notes


def write_stub(
    workspace: Path,
    found: Found,
    minix_url: str = DEFAULT_MINIX_URL,
    *,
    system: str = DEFAULT_SYSTEM,
    input_names: Mapping[str, Collection[str] | None] | None = None,
) -> StubResult:
    """Write `flake.nix` in `workspace` when its text changes. Return the file, `changed`, and the plain lines."""
    text, notes = stub_text(found, minix_url, system=system, input_names=input_names)
    path = workspace / "flake.nix"
    old = path.read_text() if path.is_file() else None
    if old is not None and not old.startswith(FIRST_LINE):
        raise ValueError(
            f"{path} is not a managed stub: its first line is not {FIRST_LINE!r}. Move the file away, then run the command again"
        )
    changed = old != text
    if changed:
        path.write_text(text)
    return StubResult(path, changed, tuple(notes))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m minix_config.stub", description="Write the managed flake.nix of a minix workspace."
    )
    parser.add_argument("workspace", type=Path, help="the folder that holds home.py")
    parser.add_argument("--url", default=DEFAULT_MINIX_URL, help="the flake URL of minix")
    parser.add_argument("--system", default=DEFAULT_SYSTEM, help="the system of the host")
    parser.add_argument(
        "--offline", action="store_true", help="read the inputs of each flake input from the Nix cache only"
    )
    args = parser.parse_args(argv)
    try:
        found = discover(args.workspace / "home.py")
    except DiscoveryError as error:
        print(error, file=sys.stderr)
        return 1
    names = {item.name: input_names(item.url, offline=args.offline) for item in found.inputs if item.flake}
    try:
        result = write_stub(args.workspace, found, args.url, system=args.system, input_names=names)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    for line in result.warnings:
        print(line, file=sys.stderr)
    print(f"{result.path.name} {'written' if result.changed else 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
