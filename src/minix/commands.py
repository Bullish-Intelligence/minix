"""The four commands of `minix`: `init`, `apply`, `undo`, and `update`. Each returns the exit code.

A command gets a `Context`: the runner, the clock, the output, the environment, and the account. The CLI builds the
real one. The tests build one with fakes.
"""

import fcntl
import getpass
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pynix_engine import (
    EXIT_FAILED,
    EXIT_STOPPED,
    Clock,
    Engine,
    Fail,
    Layer,
    Runner,
    Session,
    SubprocessRunner,
    SystemClock,
    steps,
)
from pynix_native import DiscoveryError, discover, discover_source

from minix.compile import Compiler, ensure_ignored, make_input_packages
from minix.layer import FALLBACK_IDENTITY, minix_layer
from minix.nix import NixRunner
from minix.release import (
    changelog_sections,
    input_source,
    locked_node,
    nixpkgs_ages,
    option_paths,
    read_lock,
    read_release,
    relevant_lines,
    tag_name,
)
from minix.seedlock import workspace_lock
from minix.workspace import environment_home, find_workspace, host_system, init_workspace, tilde, warn_no_linger
from minix_config.entry import written_entry
from minix_config.stub import DEFAULT_MINIX_URL, input_names, write_stub

GITIGNORE = ".basix/\nresult\n"


def say_flush(text: str) -> None:
    print(text, flush=True)


@dataclass
class Context:
    """Everything that a command reaches outside itself."""

    user: str
    home: Path
    cwd: Path
    env: Mapping[str, str]
    runner: Runner = field(default_factory=SubprocessRunner)
    clock: Clock = field(default_factory=SystemClock)
    say: Callable[[str], None] = say_flush
    system: str | None = None  # None: the system of the host
    share: Path | None = None  # the folder `share/minix` of the package (`MINIX_SHARE`): the seed of the lock
    read_inputs: Callable[..., frozenset[str] | None] = input_names  # the names in `inputs` of a flake input
    wall_time: Callable[[], float] = time.time  # for the age of a nixpkgs

    @property
    def nix(self) -> Runner:
        """The runner that adds the Nix features to every `nix` command (009-D29)."""
        return NixRunner(self.runner)


def real_context() -> Context:
    env = dict(os.environ)
    share = env.get("MINIX_SHARE")
    return Context(
        user=getpass.getuser(),
        home=environment_home(env),
        cwd=Path.cwd(),
        env=env,
        share=Path(share) if share else None,
    )


# --- apply and undo ---------------------------------------------------------------------------------
def workspace_layer(ctx: Context, ws: Path) -> Layer:
    """The layer of this account, with the folder name in plain words."""
    user = ctx.user

    def preflight(session: Session) -> None:
        ensure_ignored(session)  # before the stage step, which refuses the link in `.basix/`
        warn_no_linger(session, user)

    return minix_layer(
        user,
        folder=tilde(ws, ctx.home),
        compile=Compiler(user, ctx.system or host_system(), ctx.read_inputs),
        preflight=preflight,
    )


def run_engine(ctx: Context, explicit: Path | None, verbose: bool, action: str) -> int:
    try:
        ws = find_workspace(explicit, ctx.cwd, ctx.home, ctx.env)
        layer = workspace_layer(ctx, ws)
    except Fail as failure:
        ctx.say(str(failure))
        return failure.code
    engine = Engine(layer, ws, runner=ctx.nix, clock=ctx.clock, say=ctx.say, env=ctx.env, verbose=verbose)
    return engine.apply() if action == "apply" else engine.undo()


def apply(ctx: Context, directory: Path | None = None, verbose: bool = False) -> int:
    """Compile `home.py`, build the home, activate it, check it, then save the files (CONCEPT 4.1)."""
    return run_engine(ctx, directory, verbose, "apply")


def undo(ctx: Context, directory: Path | None = None, verbose: bool = False) -> int:
    """Drop an edit that minix did not apply; else go back to the save before the last one. Then apply (L25)."""
    return run_engine(ctx, directory, verbose, "undo")


# --- init -------------------------------------------------------------------------------------------
def git(ctx: Context, ws: Path, *args: str) -> None:
    result = ctx.runner.run(["git", *args], cwd=ws)
    if result.code != 0:
        raise Fail(f"git {' '.join(args[:2])} failed (exit {result.code}):\n{result.out.strip()}")


def knows_author(ctx: Context, ws: Path) -> bool:
    return all(ctx.runner.run(["git", "config", key], cwd=ws).code == 0 for key in ("user.name", "user.email"))


def init(ctx: Context, directory: Path | None = None, url: str | None = None) -> int:
    """Make the workspace offline: the stub, the lock, `home.py`, `.gitignore`, and the first save (009-D2, G9).

    The folder is `--dir`, else `MINIX_WORKSPACE` (with `~` expanded), else `~/minix`. Missing parent folders are made.
    A folder that holds files, `home.py` included, is not touched: init makes a new workspace only.

    The lock comes from the files that the package carries (`ctx.share`), and only for the default URL of minix: the
    files describe this build, not a source that the learner names with `--url`.
    """
    ws = init_workspace(directory, ctx.cwd, ctx.home, ctx.env)
    folder = tilde(ws, ctx.home)
    made = next((path for path in (*ws.parents[::-1], ws) if not path.exists()), None)  # the top folder that init makes
    try:
        if ws.exists() and (not ws.is_dir() or any(ws.iterdir())):
            raise Fail(f"{folder} exists and is not empty. minix init makes a new folder; give another with --dir.")
        system = ctx.system or host_system()
        entry = written_entry(ctx.user)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "home.py").write_text(entry)
        result = write_stub(ws, discover_source(entry, "home.py"), url or DEFAULT_MINIX_URL, system=system)
        lock = workspace_lock(ctx.share, DEFAULT_MINIX_URL) if ctx.share and url is None else None
        if lock is not None:
            (ws / "flake.lock").write_text(lock)
        (ws / ".gitignore").write_text(GITIGNORE)
        git(ctx, ws, "init", "-q", "-b", "main")
        git(ctx, ws, "add", "-A")
        name, email = FALLBACK_IDENTITY
        identity = [] if knows_author(ctx, ws) else ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        git(ctx, ws, *identity, "commit", "-q", "-m", "minix init")
    except (Fail, ValueError) as failure:
        if made is not None:
            shutil.rmtree(made, ignore_errors=True)
        ctx.say(str(failure))
        return failure.code if isinstance(failure, Fail) else 1
    ctx.say(f"created {folder}")
    for line in result.warnings:
        ctx.say(line)
    if lock is None:
        ctx.say("this minix carries no lock, so the first minix apply locks the inputs and needs the network")
    ctx.say(f"next: edit {folder}/home.py, then run: minix apply")
    return 0


# --- update -----------------------------------------------------------------------------------------
def archive_tree(session: Session) -> dict[str, Any] | None:
    """The tree of the locked sources (`nix flake archive --json`), or None when Nix cannot give it."""
    result = session.run(["nix", "flake", "archive", "--json", "--no-write-lock-file", "--no-warn-dirty"], quiet=True)
    try:
        tree = json.loads(result.out[result.out.index("{") :])
    except ValueError:
        return None
    return tree if isinstance(tree, dict) else None


def print_changelog(
    ctx: Context,
    session: Session,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    sources: tuple[str | None, str | None],
) -> None:
    """The part of the `pynix` changelog, between the old and the new tag, that names an option that home.py sets."""
    old_node, new_node = locked_node(before, "minix", "pynix"), locked_node(after, "minix", "pynix")
    old_tag = tag_name(old_node.get("original", {}).get("ref")) or tag_name(old_node.get("locked", {}).get("ref"))
    new_tag = tag_name(new_node.get("original", {}).get("ref")) or tag_name(new_node.get("locked", {}).get("ref"))
    if not new_node or old_node.get("locked", {}).get("rev") == new_node.get("locked", {}).get("rev"):
        return
    try:
        text = (Path(sources[1] or "") / "CHANGELOG.md").read_text()
    except OSError:
        ctx.say("pynix changed, but its changelog could not be read")
        return
    try:
        compiled = json.loads((session.ws / session.layer.json_file).read_text())
    except (OSError, ValueError):
        compiled = {}
    paths = option_paths(compiled)
    shown = False
    for tag, body in changelog_sections(text, old_tag):
        lines = relevant_lines(body, paths)
        if lines:
            ctx.say(f"{tag} changes options that home.py sets:")
            for line in lines:
                ctx.say(f"  {line}")
            shown = True
    if not shown:
        ctx.say(f"{old_tag or 'unknown'} -> {new_tag or 'unknown'}: no change to an option that home.py sets")


def update(ctx: Context, name: str = "minix", directory: Path | None = None, verbose: bool = False) -> int:
    """Move the lock of `minix` (or of the input `name`), print what changed, and make the input packages.

    It prints the old and new release names, the age of each pinned nixpkgs, and the part of the `pynix` changelog
    that names an option of this workspace (009-D10, 009-D54). It does not switch: the learner runs `apply`.
    """
    try:
        ws = find_workspace(directory, ctx.cwd, ctx.home, ctx.env)
        layer = workspace_layer(ctx, ws)
    except Fail as failure:
        ctx.say(str(failure))
        return failure.code
    session = Session(layer, ws, ctx.nix, ctx.clock, ctx.say, ctx.env, verbose)
    lock = (session.state / "lock").open("w")  # the lock of `apply`: one command at a time
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        ctx.say(session.word("busy"))
        return EXIT_FAILED
    session.open_log()
    try:
        tree_before = archive_tree(session)
        lock_before = read_lock(ws / "flake.lock")
        before = read_release(lock_before, name, input_source(tree_before, name))
        pynix_before = input_source(tree_before, "minix", "pynix")
        result = session.run(["nix", "flake", "update", name, "--no-warn-dirty"])
        if result.code != 0:
            ctx.say(f"The update failed. The lock did not change.\n{steps.plain(steps.trim_nix_error(result.out))}")
            return EXIT_STOPPED
        tree_after = archive_tree(session)
        lock_after = read_lock(ws / "flake.lock")
        after = read_release(lock_after, name, input_source(tree_after, name))
        if before.nar_hash and before.nar_hash == after.nar_hash:
            ctx.say(f"{name} is up to date ({after.label})")
        else:
            ctx.say(f"{name} {before.named(after)} -> {after.named(before)}")
        for line in nixpkgs_ages(lock_after, ctx.wall_time()):
            ctx.say(line)
        print_changelog(
            ctx, session, lock_before, lock_after, (pynix_before, input_source(tree_after, "minix", "pynix"))
        )
        try:
            make_input_packages(session, discover(ws / "home.py"))
        except DiscoveryError:
            pass  # the next apply reports a home.py that cannot be read
        except Fail as failure:
            ctx.say(str(failure))
            return failure.code
        ctx.say("next: run minix apply to use it")
        return 0
    finally:
        lock.close()
        session.close()
