"""Compile a home file: `python -m minix_config.compile home.py OUT.json`.

The output holds `"source_sha256"`: the SHA-256 (hex) of the bytes of the home file. Nix computes the same value
with `builtins.hashFile "sha256" ./home.py`, and `lib.home` throws when the two differ (the file changed
after the last `minix apply`). It also holds `"reads"`: the hash of every file in the workspace that the entry read, and
the names in every folder that it listed (009-D45). A read outside the workspace stops the compile.

The entry loader. First it reads the `Input(...)` and `NixpkgsPin(...)` calls from the syntax tree and checks the input
packages against `flake.lock` (009-D48, 009-D49). Then it runs the file, takes its `home`, and writes the compiled
JSON. An error prints one plain line that starts with `FILE:LINE:COLUMN:` and exits 1. Pydantic writes the text of a
validation error (`nixdantic.Plain`, `Expect`, and the validators of `minix_config`). This file finds the place in the
source and prints the line. It prints no Python traceback.

The output holds `"input_packages"` when the file has flake inputs: the `narHash` of each input package. `lib.home` checks
it against `flake.lock` at evaluation, so a lock that moved after the compile stops the evaluation.

The generated input packages (`pynix_NAME`) and the name lists of a `NixpkgsPin` sit in `.basix/pynix` of the workspace.
The loader puts that folder and the workspace on the search path of Python.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib
import json
import re
import runpy
import sys
import traceback
from pathlib import Path

from pydantic import ValidationError


def _positional(title: str) -> tuple[str, ...]:
    import minix_config

    return getattr(getattr(minix_config, title, None), "_positional", ())


def _place(tree: ast.Module, line: int | None, loc: tuple[object, ...], kind: str, title: str = "") -> tuple[int, int]:
    """Line and column (from 1) of the wrong value, or of the wrong keyword for an unknown name. G29c: also a positional argument."""
    positional = _positional(title) if title else ()
    index = positional.index(loc[0]) if loc and loc[0] in positional else None

    def argument(n: ast.Call) -> ast.AST | None:
        if index is not None and isinstance(n.func, ast.Name) and n.func.id == title and index < len(n.args):
            return n.args[index]
        return next((k for k in n.keywords or [] if k.arg == loc[0]), None)

    calls = (
        [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and line is not None
            and n.lineno <= line <= (n.end_lineno or n.lineno)
            and argument(n) is not None
        ]
        if loc
        else []
    )
    if (
        not calls and title
    ):  # G29c: a positional or variadic argument of a nested call, for example `Run(pkgs.python3, "-m", 8080)`
        named = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == title
                and line is not None
                and n.lineno <= line <= (n.end_lineno or n.lineno)
            ),
            None,
        )
        if named is not None:
            if title == "Run" and loc and loc[0] == "args":
                k = (loc[1] + 1) if len(loc) > 1 and isinstance(loc[1], int) else 1
                if k < len(named.args):
                    return named.args[k].lineno, named.args[k].col_offset + 1
            return named.lineno, named.col_offset + 1
    if not calls:  # a missing setting, or no match: the call itself
        call = next((n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.lineno == line), None)
        return (call.lineno, call.col_offset + 1) if call else (line or 1, 1)
    arg = argument(max(calls, key=lambda n: n.lineno))
    if arg is None:  # `calls` holds only calls that have the argument, so this is a guard for the type checker
        return line or 1, 1
    node: ast.AST = arg if kind == "extra_forbidden" or not isinstance(arg, ast.keyword) else arg.value
    for part in (
        loc[1:] if kind != "extra_forbidden" else ()
    ):  # go into a list or a call: `ports[0]`, `settings=X(AllowUsers=...)`
        if isinstance(part, int) and isinstance(node, (ast.List, ast.Tuple)) and part < len(node.elts):
            node = node.elts[part]
        elif isinstance(node, ast.Call):
            node = next((k.value for k in node.keywords if k.arg == part), node)
    return getattr(node, "lineno", line or 1), getattr(node, "col_offset", 0) + 1


def _given(value: object) -> str:
    try:
        text = json.dumps(value) if isinstance(value, (str, int, float, bool, list, dict, type(None))) else repr(value)
    except TypeError:  # a list or dict that holds a component (S14: the loader of S10 raised here)
        text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _path(loc: tuple[object, ...]) -> str:
    return "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else str(p)) for i, p in enumerate(loc))


def _option(ctx: dict[str, object]) -> str:
    """Q16: the Home Manager option of the field, where a generated root class knows it."""
    return f" (Home Manager option {ctx['option']})" if ctx.get("option") else ""


def _validation(name: str, tree: ast.Module, line: int | None, error: ValidationError, where: str = "") -> list[str]:
    lines = []
    for item in error.errors(include_url=False):
        ctx, kind, loc = item.get("ctx") or {}, item["type"], tuple(item["loc"])
        if kind == "no_main_program":  # G29a: a model validator has no field in `loc`; the wrong value is the package
            loc = ("program",)
        row, col = _place(tree, line, loc, kind, error.title)
        found = (
            ""
            if kind
            in {
                "missing",
                "extra_forbidden",
                "duplicate",
                "no_main_program",
                "nested_class",
                "env_name",
                "env_value",
                "state_twice",
            }
            or not loc
            else f", found {_given(item['input'])}"
        )
        hint = " ".join(str(ctx.get("hint", "")).split())  # the help of the field, from its docstring
        text = f"{item['msg']}{found}"
        text += "" if text.endswith((".", "?")) else "."
        owner = _input_of(error.title)  # a class of an input package names its input (SG42b)
        owner = f" (class {error.title} of the input `{owner}`)" if owner else ""
        lines.append(
            f"{name}:{row}:{col}: {error.title}{'.' + _path(loc) if loc else ''} {text}{' ' + hint if hint else ''}{_option(ctx)}{owner}{where}"
        )
    return lines


def _unknown_name(name: str, tree: ast.Module, line: int | None, missing: str) -> list[str]:
    import minix_config

    assigned = [t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
    if missing in minix_config.__all__:  # a minix name that the file does not import (`pkgs`, `Home`)
        spot = next(
            (n for n in ast.walk(tree) if getattr(n, "lineno", None) == line and getattr(n, "id", None) == missing),
            None,
        )
        return [
            f"{name}:{line or 1}:{getattr(spot, 'col_offset', 0) + 1 if spot else 1}: `{missing}` is not imported. Add the line `from minix_config import *` at the top of {name}."
        ]
    if missing[:1].islower():  # SV1: a name of the learner (a variable), not a name of minix
        spot = next(
            (n for n in ast.walk(tree) if getattr(n, "lineno", None) == line and getattr(n, "id", None) == missing),
            None,
        )
        close = difflib.get_close_matches(missing, assigned, n=1)
        advice = f" Did you mean `{close[0]}`?" if close else ""
        return [
            f"{name}:{line or 1}:{getattr(spot, 'col_offset', 0) + 1 if spot else 1}: `{missing}` is not defined in {name}.{advice}"
            f" The names you defined are: {', '.join(assigned) or 'none'}."
        ]
    known = [
        n
        for n in minix_config.__all__
        if n[:1].isupper()
        and isinstance(getattr(minix_config, n), type)
        and n not in {"ConfigRef", "Native", "Package", "Text"}
    ]
    close = difflib.get_close_matches(missing, known, n=1)
    spot = next(
        (
            n
            for n in ast.walk(tree)
            if getattr(n, "lineno", None) == line
            and (getattr(n, "id", None) == missing or getattr(n, "name", None) == missing)
        ),
        None,
    )
    advice = f" Did you mean `{close[0]}`?" if close else ""
    return [
        f"{name}:{line or 1}:{getattr(spot, 'col_offset', 0) + 1 if spot else 1}: `{missing}` is not a minix name.{advice} The names are: {', '.join(known)}."
    ]


_CTOR = re.compile(
    r"^(\w+)\.__init__\(\) (got some positional-only arguments passed as keyword arguments|got an unexpected keyword argument|missing \d+ required positional|takes \d+ positional)"
)


def _constructor(name: str, tree: ast.Module, line: int | None, error: TypeError) -> list[str] | None:
    """G29a: a capability called with the wrong shape of arguments: `File(path="a", text="b")`, `Program(name="x")`, `File("a")`."""
    import minix_config

    found = _CTOR.match(str(error))
    cls = getattr(minix_config, found.group(1), None) if found else None
    if found is None or cls is None or not hasattr(cls, "_positional"):
        return None
    order = list(cls._positional)
    settings = [f for f in cls.model_fields if f not in order]
    call = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and n.lineno == line and getattr(n.func, "id", None) == found.group(1)
        ),
        None,
    )
    shape = ", ".join(w.upper() for w in order)
    text = f"{found.group(1)} takes {' and '.join(w.upper() for w in order)} by position, without the setting name: {found.group(1)}({shape})."
    if settings:
        text += f" Write the settings as keywords: {', '.join(settings)}."
    return [f"{name}:{line or 1}:{call.col_offset + 1 if call else 1}: {text}"]


def _source_line(error: BaseException, name: str) -> int | None:
    frames = [f for f in traceback.extract_tb(error.__traceback__) if Path(f.filename).name == name]
    return frames[-1].lineno if frames else None


def _syntax(name: str, error: SyntaxError) -> list[str]:
    """G-S4-1: one plain line for a Python syntax error. No traceback."""
    text = " ".join(str(error.msg).split())
    text += "" if text.endswith((".", "?")) else "."
    return [f"{name}:{error.lineno or 1}:{error.offset or 1}: syntax error: {text}"]


def report(source: Path, error: BaseException) -> list[str]:
    from nixdantic import PriorityError
    from pynix_pkgs import PackageError

    from minix_config import ComponentError

    name = source.name
    if (
        isinstance(error, SyntaxError) and Path(str(error.filename or "")).name == name
    ):  # the file itself does not parse
        return _syntax(name, error)
    if isinstance(error, OSError) and Path(str(error.filename or "")).name == name:
        return [f"{name}:1:1: cannot read the file: {error.strerror}."]
    try:
        tree = ast.parse(source.read_text())
    except (
        SyntaxError,
        ValueError,
        UnicodeDecodeError,
    ) as inner:  # an error of another kind, and the file does not parse
        return (
            _syntax(name, inner) if isinstance(inner, SyntaxError) else [f"{name}:1:1: cannot read the file: {inner}."]
        )
    if isinstance(
        error, ComponentError
    ):  # a capability failed inside `emit`: the traceback of the inner error has the line
        inner = error.error
        if isinstance(inner, ValidationError):
            return _validation(name, tree, _source_line(inner, name), inner, f" (in {error.label})")
        return [f"{error.where}: the capability {error.label} failed: {type(inner).__name__}: {inner}"]
    line = _source_line(error, name)
    if isinstance(error, TypeError) and (text := _constructor(name, tree, line, error)):
        return text
    if isinstance(error, ValidationError):
        return _validation(name, tree, line, error)
    if isinstance(error, ImportError) and "cannot import name" in str(error):
        return _unknown_name(name, tree, line, str(error).split("'")[1])
    if isinstance(error, PackageError):  # a package name that the package list does not have (009-D19)
        spot = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.lineno == line and n.attr == error.word),
            None,
        )
        spot = spot or next(
            (n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.lineno == line and n.value == error.word),
            None,
        )
        col = (
            (spot.end_col_offset - len(error.word) + 1 if isinstance(spot, ast.Attribute) else spot.col_offset + 2)
            if spot and spot.end_col_offset
            else 1
        )
        return [f"{name}:{line or 1}:{col}: {error}"]
    if isinstance(error, AttributeError) and " has no class " in str(
        error
    ):  # a name of the flat namespace (`hm.Firewall`) that has no class
        word = str(error).split(" has no class ")[1].split(".")[0]
        spot = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.lineno == line and n.attr == word), None
        )
        col = (spot.end_col_offset - len(word) + 1) if spot and spot.end_col_offset else 1
        return [f"{name}:{line or 1}:{col}: {error}"]
    if isinstance(error, PriorityError):  # a wrapper where it has no meaning: one line, at the call
        wrappers = {"Force", "Default", "Before", "After", "Order"}
        call = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and n.lineno == line and getattr(n.func, "id", "") in wrappers
            ),
            None,
        )
        return [f"{name}:{line or 1}:{call.col_offset + 1 if call else 1}: {error}"]
    if isinstance(error, NameError) and error.name:
        return _unknown_name(name, tree, line, error.name)
    return [f"{name}:{line or 1}: {type(error).__name__}: {error}"]


def _warnings(source: Path) -> None:
    """009-D37: print one warning line for each `args` that holds a shell character. The exit code stays 0."""
    from minix_config import _base

    if not _base.WARNINGS:
        return
    tree = ast.parse(source.read_text())
    for where, field, chars in _base.WARNINGS:
        title, _, attr = field.partition(".")
        line = int(where.rpartition(":")[2])
        call = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == title
                and n.lineno <= line <= (n.end_lineno or n.lineno)
                and any(k.arg == attr for k in n.keywords)
            ),
            None,
        )
        arg = next((k.value for k in call.keywords if k.arg == attr), None) if call else None
        print(
            f"{source.name}:{arg.lineno if arg else line}:{arg.col_offset + 1 if arg else 1}: warning: {field} holds `{chars}`. "
            f"No shell runs, so it is a plain argument. To use a shell, write program=pkgs.bash, args=\"-c '...'\".",
            file=sys.stderr,
        )


# The packages of `pynix` itself. Every other `pynix_NAME` package is an input package (009-D47).
CORE_PACKAGES = {"pynix_os", "pynix_hm", "pynix_pkgs", "pynix_native", "pynix_engine", "pynix_flake_parts"}
COMMAND = "minix apply"
MODULE_ATTR = "homeManagerModules"  # the flake output that holds the modules of an input on this layer


def _input_source(top: str) -> dict[str, str]:
    """The `SOURCE` record of a generated package, or an empty dict when the package records none."""
    try:
        return dict(importlib.import_module(f"{top}._generation").SOURCE)
    except (ImportError, AttributeError):
        return {}


def _input_of(title: str) -> str | None:
    """The flake input whose input package holds the class `title`, or None (SG42b)."""
    for top in sorted({n.split(".")[0] for n in sys.modules if n.startswith("pynix_")} - CORE_PACKAGES):
        record = _input_source(top)
        if record.get("input") and any(
            hasattr(m, title) for n, m in list(sys.modules.items()) if n.startswith(top + ".")
        ):
            return record["input"]
    return None


def _check_inputs(source: Path, packages: Path) -> list[str]:
    """Read the `Input(...)` and `NixpkgsPin(...)` calls from the syntax tree, and check their packages against `flake.lock`.

    Returns the plain lines that stop the compile: a call that is not a literal, or a package that does not pair with
    the lock (009-D48, 009-D49). A change of the generator version is no error (010-D5).
    """
    from pynix_native import DiscoveryError, check_pairs, discover, error_lines, read_lock

    try:
        found = discover(source)
    except DiscoveryError as error:
        return [str(error)]
    if not found.inputs and not found.nixpkgs:
        return []
    lock_file = source.parent / "flake.lock"
    if not lock_file.is_file():
        first = found.inputs[0] if found.inputs else found.nixpkgs[0]
        return [f"{first.source}: flake.lock does not exist yet; run `{COMMAND}` to lock it"]
    return error_lines(check_pairs(found, read_lock(lock_file), packages, COMMAND))


def _input_packages(source: Path, packages: Path) -> dict[str, str]:
    """The `narHash` of the package of each flake input, from `_generation.py`. `lib.home` checks it again at evaluation."""
    from pynix_native import DiscoveryError, discover, input_package_name, read_source

    try:
        found = discover(source)
    except DiscoveryError:
        return {}
    record: dict[str, str] = {}
    for item in found.inputs:
        have = read_source(packages / input_package_name(item.name) / "_generation.py") if item.flake else None
        if have and have.get("narHash"):
            record[item.name] = have["narHash"]
    return dict(sorted(record.items()))


def main(argv: list[str]) -> int:
    from nixdantic.reads import Reads
    from pynix_native import reset_nixpkgs, set_apply_command, set_module_attr

    from . import _base

    _base.WARNINGS.clear()  # `main` can run again in one process: a test does it
    reset_nixpkgs()
    source, out = Path(argv[1]), Path(argv[2])
    root = source.resolve().parent
    packages = root / ".basix" / "pynix"  # where `minix apply` puts `pynix_NAME`
    set_apply_command(COMMAND)
    set_module_attr(MODULE_ATTR)  # an `Input` names the Home Manager word, not `nixosModules`
    if lines := _check_inputs(source, packages):
        print("\n".join(lines), file=sys.stderr)
        return 1
    if packages.is_dir():
        sys.path.insert(0, str(packages))
    sys.path.append(str(root))  # allow a helper module in the workspace
    try:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()  # the bytes that the run reads
        with Reads(source, out) as reads:
            scope = runpy.run_path(str(source), run_name="__minix__")
            from minix_config import Home

            home = scope.get("home")
            if not isinstance(home, Home):
                print(f"{source.name}: the file must set `home = Home(...)`", file=sys.stderr)
                return 1
            compiled = home.compile()
    except Exception as error:  # every learner error becomes plain lines
        for text in report(source, error):
            print(text, file=sys.stderr)
        return 1
    _warnings(source)
    if lines := reads.refusals():  # a read outside the workspace (009-D45)
        print("\n".join(lines), file=sys.stderr)
        return 1
    compiled["source_sha256"] = digest
    if records := _input_packages(source, packages):
        compiled["input_packages"] = records
    compiled["reads"] = reads.result()
    out.write_text(json.dumps(compiled, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
