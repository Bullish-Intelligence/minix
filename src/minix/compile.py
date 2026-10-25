"""The compile step of `minix apply` (CONCEPT 4.1, step 1; 009-D49).

Order: read the `Input(...)` calls from the syntax tree of `home.py`, write the stub when they changed, lock the new
inputs, make the input packages (every run, when the entry has inputs: 010-D13), build the compiler from the workspace
lock, run it, check the account, and root the flake inputs. A wrong value stops here with one plain line, and nothing
else changes.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pynix_engine import Session, steps
from pynix_native import DiscoveryError, Found, discover

from minix.layer import stopped
from minix.release import locked_node, read_lock
from minix.workspace import stub_url
from minix_config.stub import DEFAULT_MINIX_URL, write_stub

# The flake output that builds the input packages (`pynix_NAME`) of the workspace, proposed with `p5-lib`.
INPUT_PACKAGES_ATTR = ".#input-packages"
PACKAGES_LINK = Path(".basix") / "pynix"
IGNORED = ".basix/"


def read_found(session: Session) -> Found:
    """The `Input` and `NixpkgsPin` calls of `home.py`. Stop with one line when the file is missing or unreadable."""
    entry = session.ws / session.layer.entry
    if not entry.is_file():
        raise stopped(session.word("no_entry"))
    try:
        return discover(entry)
    except DiscoveryError as error:
        raise stopped(str(error)) from None


def ensure_ignored(session: Session) -> None:
    """Keep `.basix/` out of git: it holds a link to the store, and the stage step refuses such a link."""
    if session.git("check-ignore", "-q", str(PACKAGES_LINK), check=False).code == 0:
        return
    ignore = session.ws / ".gitignore"
    old = ignore.read_text() if ignore.exists() else ""
    ignore.write_text(old + ("" if not old or old.endswith("\n") else "\n") + IGNORED + "\n")
    session.say(session.word("scratch_ignored", names=IGNORED))


def lock_inputs(session: Session) -> None:
    """Lock the inputs of the stub. A new lock must not look like an edit, so apply stages it afterwards."""
    result = session.run(["nix", "flake", "lock", "--no-warn-dirty"])
    if result.code != 0:
        raise stopped(session.word("lock_failed", detail=steps.plain(steps.trim_nix_error(result.out))))


def build_input_packages(session: Session) -> None:
    """Build the input packages and link them to `.basix/pynix`. The compiler and ty read that folder."""
    (session.ws / PACKAGES_LINK).parent.mkdir(exist_ok=True)
    argv = ["nix", "build", "--out-link", str(session.ws / PACKAGES_LINK), "--print-out-paths", "--no-warn-dirty"]
    result = session.run([*argv, INPUT_PACKAGES_ATTR])
    if result.code != 0:
        detail = steps.plain(steps.trim_nix_error(result.out))
        raise stopped(session.word("inputs_build_failed", detail=detail))


def make_input_packages(session: Session, found: Found) -> None:
    """Build the input packages on every run when the entry has inputs that need them (010-D13)."""
    if any(item.flake for item in found.inputs) or found.nixpkgs:
        build_input_packages(session)


def check_account(session: Session, user: str, previous: bytes | None) -> None:
    """Refuse a `home.py` that names another account (009-D2, BSX-383). Put the old `home.json` back."""
    json_file = session.ws / session.layer.json_file
    try:
        named = json.loads(json_file.read_text()).get("user")
    except (OSError, ValueError, AttributeError):
        return  # the compiler wrote what it wrote; the build step reports a file that is not usable
    if named == user:
        return
    if previous is None:
        json_file.unlink(missing_ok=True)
    else:
        json_file.write_bytes(previous)
    raise stopped(session.word("wrong_account", named=named, running=user))


def root_inputs(session: Session) -> None:
    """Root the sources of all flake inputs, so that an offline apply works after a garbage collection (S16)."""
    result = session.run(["nix", "flake", "archive", "--json", "--no-write-lock-file", "--no-warn-dirty"])
    try:
        if result.code != 0:
            raise ValueError(steps.trim_nix_error(result.out))
        tree = json.loads(result.out[result.out.index("{") :])
        paths: set[str] = set()
        todo = [tree]
        while todo:
            node = todo.pop()
            paths.add(node["path"])
            todo.extend(node.get("inputs", {}).values())
    except (ValueError, KeyError, TypeError) as error:
        session.say(session.word("inputs_failed", detail=steps.plain(str(error)).strip() or "no answer"))
        return
    for old in session.state.glob("inputs*"):
        old.unlink()
    session.run(["nix-store", "--realise", "--indirect", "--add-root", str(session.state / "inputs"), *sorted(paths)])


@dataclass(frozen=True)
class Compiler:
    """The compile step of the minix layer. `Layer.compile` calls it with the session."""

    user: str
    system: str
    read_inputs: Callable[..., frozenset[str] | None]  # (url, locked=lock node of the input) -> names

    def __call__(self, session: Session) -> None:
        ws = session.ws
        found = read_found(session)
        self.write_stub(session, found)
        steps.stage(session)  # the flake sees tracked files only
        make_input_packages(session, found)
        tool = steps.build_compiler(session)
        json_file = ws / session.layer.json_file
        previous = json_file.read_bytes() if json_file.exists() else None
        steps.run_compiler(session, tool)
        check_account(session, self.user, previous)
        root_inputs(session)

    def write_stub(self, session: Session, found: Found) -> None:
        """Write the managed stub. Lock the inputs when the stub changed or no lock exists."""
        stub = session.ws / "flake.nix"
        before = stub.read_text() if stub.exists() else None
        lock = read_lock(session.ws / "flake.lock")  # {} when there is none
        # The lock names the tree of each input. Its source in the store is read first, so a folder that is gone does
        # not matter (011 P3).
        names = {
            item.name: self.read_inputs(item.url, locked=locked_node(lock, item.name) or None)
            for item in found.inputs
            if item.flake
        }
        url = stub_url(session.ws) or DEFAULT_MINIX_URL
        try:
            result = write_stub(session.ws, found, url, system=self.system, input_names=names)
        except ValueError as error:  # a flake.nix that minix did not write
            raise stopped(str(error)) from None
        for line in result.warnings:
            session.say(line)
        if before != (stub.read_text() if stub.exists() else None) or not (session.ws / "flake.lock").exists():
            lock_inputs(session)
