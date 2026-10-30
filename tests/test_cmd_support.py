"""Shared set-up for the command tests: a fake machine and a workspace with real git and the real `minix_config`.

Git runs for real in a temporary folder, with its own identity. Nix, the compiler, the activation, and `systemctl` are
fakes: the `World` answers them and keeps the state that a real machine would keep (the Home Manager profile).
"""

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pynix_engine import Result
from pynix_engine.testing import FakeClock, FakeRunner

from minix.commands import Context
from minix.layer import HEALTH_COMMAND, NIX_FEATURES, RESET_FAILED
from minix_config.stub import DEFAULT_MINIX_URL

NIX = ("nix", *NIX_FEATURES)
GOOD_UNITS = "web.service loaded active running Web\n"
BAD_UNITS = GOOD_UNITS + "api.service loaded failed failed Api\n"
LOOPING_UNITS = "api.service loaded activating auto-restart Api\n"
COMPILE_ERROR = "home.py:6:40: Git.email must be an email address, for example you@example.org, found 42."
NIXPKGS_REV = "e7439b6b14ad3cc35d05608ebca9bce01a25f5f8"
STUB_URL = DEFAULT_MINIX_URL
SOURCE_REV = "a" * 40
SOURCE_HASH = "sha256-AAAA"


def git_env(config: Path | None = None) -> dict[str, str]:
    """The environment of every real git call: no outer setting (a persona, a global file) reaches the test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=str(config or os.devnull), GIT_CONFIG_NOSYSTEM="1")
    return env


class HybridRunner:
    """Send `git` to a real process and every other command to the fake runner."""

    def __init__(self, fake: FakeRunner, env: dict[str, str]) -> None:
        self.fake = fake
        self.env = env
        self.git_calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, cwd: Path | None = None, on_line: Callable[[str], None] | None = None):
        if tuple(argv[:1]) != ("git",):
            return self.fake.run(argv, cwd=cwd, on_line=on_line)
        self.git_calls.append(tuple(argv))
        done = subprocess.run(list(argv), cwd=cwd, env=self.env, capture_output=True, text=True)
        out = done.stdout + done.stderr
        if on_line:
            for line in out.splitlines():
                on_line(line)
        return Result(done.returncode, out)


def minix_lock() -> str:
    """The `flake.lock` of minix itself, as the package carries it in `share/minix/`."""
    pin = {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": NIXPKGS_REV, "narHash": "sha256-NIXPKGS"}
    return json.dumps(
        {
            "nodes": {
                "root": {"inputs": {"nixpkgs": "nixpkgs", "home-manager": "home-manager"}},
                "nixpkgs": {"locked": pin, "original": {"type": "github", "owner": "NixOS", "repo": "nixpkgs"}},
                "home-manager": {
                    "inputs": {"nixpkgs": ["nixpkgs"]},
                    "locked": {"type": "github", "owner": "nix-community", "repo": "home-manager", "rev": "e" * 40},
                    "original": {"type": "github", "owner": "nix-community", "repo": "home-manager"},
                },
            },
            "root": "root",
            "version": 7,
        }
    )


def write_share(share: Path) -> None:
    """The folder `share/minix` of the package: the source of this build and the lock of minix."""
    share.mkdir(parents=True, exist_ok=True)
    source = {"rev": SOURCE_REV, "narHash": SOURCE_HASH, "lastModified": 1700000000, "revCount": 42}
    (share / "source.json").write_text(json.dumps(source))
    (share / "flake.lock").write_text(minix_lock())


def lock_json(nar_hash: str = "sha256-AAAA", rev: str = "a" * 40, ref: str = "release", inputs=()) -> str:
    """A `flake.lock` with the `minix` input and the named extra inputs (each with its own nar hash)."""
    nodes: dict[str, object] = {
        "root": {"inputs": {"minix": "minix", "nixpkgs": "nixpkgs", **{name: name for name in inputs}}},
        "minix": {
            "locked": {"type": "git", "rev": rev, "narHash": nar_hash},
            "original": {"type": "git", "url": STUB_URL, "ref": ref},
        },
        "nixpkgs": {"locked": {"type": "github", "rev": NIXPKGS_REV, "narHash": "sha256-NIXPKGS"}},
    }
    for name in inputs:
        nodes[name] = {
            "inputs": {"nixpkgs": ["nixpkgs"]},
            "locked": {"type": "github", "rev": "b" * 40, "narHash": f"sha256-{name}"},
        }
    return json.dumps({"nodes": nodes, "root": "root", "version": 7})


@dataclass
class World:
    """The fake machine: the Home Manager profile, the health answer, the compiler, and the answers of the commands."""

    tmp: Path
    ws: Path
    user: str = "ada"
    units: str = GOOD_UNITS
    linger: str = "yes"
    login_shell: str = "/bin/bash"  # the seventh field of `getent passwd`
    getent_code: int = 0
    compiled_fragments: list[dict[str, object]] = field(default_factory=list)  # what the compiler writes
    compile_error: str | None = None
    compiled_user: str | None = None  # the user that the compiler writes; None: the account
    build_error: str | None = None
    activate_code: int = 0
    earlier_relinks: bool = True  # False: the activation of an earlier home leaves the profile where it is (011 P3)
    switch_code: int = 0  # the exit code of `nix-env --switch-generation`
    generations: int = 0
    next_home: str = "home-1"
    health_code: int = 0
    lock_code: int = 0
    update_to: str | None = None  # the lock text that `nix flake update` writes
    source_version: str = "0.1.0"
    new_version: str = "0.1.1"
    input_packages_code: int = 0
    activated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    switched: list[str] = field(default_factory=list)
    updated: bool = False
    no_source: bool = False
    input_names: frozenset[str] | None = frozenset({"nixpkgs"})  # the names in `inputs` of an input flake

    @property
    def state(self) -> Path:
        return self.tmp / "state"

    @property
    def profiles(self) -> Path:
        return self.state / "nix" / "profiles"

    @property
    def profile(self) -> Path:
        return self.profiles / "home-manager"

    def store(self, name: str) -> Path:
        path = self.tmp / "store" / name
        (path / "bin").mkdir(parents=True, exist_ok=True)
        (path / "activate").write_text("#!/bin/sh\n")
        return path

    def activate(self, home: Path) -> None:
        """What the Home Manager activation does to the profile: link a new generation."""
        self.generations += 1
        self.profiles.mkdir(parents=True, exist_ok=True)
        link = self.profiles / f"home-manager-{self.generations}-link"
        link.symlink_to(home)
        self.profile.unlink(missing_ok=True)
        self.profile.symlink_to(link.name)

    def current_link(self) -> str | None:
        """The name of the generation link that the profile points to."""
        return os.readlink(self.profile) if os.path.lexists(self.profile) else None

    def running(self) -> str | None:
        return os.path.realpath(self.profile) if os.path.lexists(self.profile) else None

    def install(self, name: str) -> Path:
        """Put a home generation in the profile, as an earlier apply did."""
        home = self.store(name)
        self.activate(home)
        return home


@dataclass
class Setup:
    world: World
    fake: FakeRunner
    runner: HybridRunner
    clock: FakeClock
    lines: list[str]
    home: Path
    ctx: Context
    share: Path

    @property
    def ws(self) -> Path:
        return self.home / "minix"

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=self.ws, env=self.runner.env, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done.stdout

    def commits(self) -> list[str]:
        return self.git("log", "--format=%s").splitlines()

    def nix_calls(self, *words: str) -> list[tuple[str, ...]]:
        return [call for call in self.fake.calls if call[: len(NIX)] == NIX and call[len(NIX) :][: len(words)] == words]

    def log(self) -> str:
        return (self.ws / ".git" / "minix" / "last.log").read_text()

    def trace(self) -> list[str]:
        """The calls that matter, in order, as short labels."""
        labels: list[str] = []
        for call in self.fake.calls:
            label = _label(call)
            if label and (label != "health" or not labels or labels[-1] != "health"):
                labels.append(label)
        return labels


def _label(call: tuple[str, ...]) -> str | None:
    if call[: len(NIX)] == NIX:
        rest = call[len(NIX) :]
        if rest[:2] == ("flake", "lock"):
            return "lock"
        if rest[:2] == ("flake", "update"):
            return "update"
        if rest[:2] == ("flake", "archive"):
            return "archive"
        if rest[:1] == ("build",):
            if rest[-1].endswith("#compiler"):
                return "compiler"
            return "packages" if rest[-1].endswith("#input-packages") else "build"
    if call[:1] == ("nix-store",):
        return "root"
    if call[:1] == ("nix-env",):
        return "switch" if "--switch-generation" in call else "delete"
    if call[0].endswith("minix-compile"):
        return "compile"
    if call[0].endswith("/activate"):
        return "activate:" + Path(call[0]).parent.name
    if call[:3] == ("systemctl", "--user", "reset-failed"):
        return "reset"
    if call[:3] == ("systemctl", "--user", "list-units"):
        return "health"
    return None


def install_handlers(fake: FakeRunner, world: World) -> None:
    """Teach the fake runner the commands of a minix apply."""

    def build(argv):
        target = argv[-1]
        if target.endswith("#compiler"):
            if world.build_error and world.build_error.startswith("compiler"):
                return Result(1, "error: compiler build failed\n")
            return Result(0, str(world.store("minix-compiler")) + "\n")
        if target.endswith("#input-packages"):
            if world.input_packages_code:
                return Result(world.input_packages_code, "error: attribute 'input-packages' missing\n")
            return Result(0, str(world.tmp / "store" / "input-packages") + "\n")
        if world.build_error:
            return Result(1, f"error:\n  {world.build_error}\n")
        return Result(0, str(world.store(world.next_home)) + "\n")

    def compile_home(argv):
        if world.compile_error:
            return Result(1, world.compile_error + "\n")
        Path(argv[2]).write_text(
            json.dumps({"user": world.compiled_user or world.user, "fragments": world.compiled_fragments})
        )
        return Result(0, "")

    def activation(argv):
        world.activated.append(Path(argv[0]).parent.name)
        if world.activate_code == 0 or Path(argv[0]).parent.name != world.next_home:
            if Path(argv[0]).parent.name == world.next_home or world.earlier_relinks:
                world.activate(Path(argv[0]).parent)
            return Result(0, "Activating home-manager\n")
        world.activate(Path(argv[0]).parent)  # the failed activation had already linked the profile
        return Result(world.activate_code, "activation failed\n")

    def delete(argv):
        if "--switch-generation" in argv:
            if world.switch_code:
                return Result(world.switch_code, "error: cannot switch\n")
            world.switched.append(argv[-1])
            world.profile.unlink(missing_ok=True)
            world.profile.symlink_to(f"home-manager-{argv[-1]}-link")
            return Result(0)
        if world.current_link() == f"home-manager-{argv[-1]}-link":
            return Result(1, "error: cannot delete the current generation\n")  # as nix-env does
        world.deleted.append(argv[-1])
        link = world.profiles / f"home-manager-{argv[-1]}-link"
        link.unlink(missing_ok=True)
        return Result(0)

    def archive(argv):
        if world.no_source:
            return Result(1, "error: cannot fetch\n")
        source = world.tmp / "source"
        source.mkdir(exist_ok=True)
        version = world.new_version if world.updated else world.source_version
        (source / "pyproject.toml").write_text(f'[project]\nname = "minix"\nversion = "{version}"\n')
        tree = {"path": str(source), "inputs": {"minix": {"path": str(source), "inputs": {}}}}
        return Result(0, "warning: noise\n" + json.dumps(tree) + "\n")

    def flake_update(argv):
        if world.update_to is None:
            return Result(1, "error: input 'nope' does not exist\n")
        (world.ws / "flake.lock").write_text(world.update_to)
        world.updated = True
        return Result(0, "")

    fake.on(NIX + ("build",), build)

    def lock(argv):
        if world.lock_code:
            return Result(world.lock_code, "error: cannot lock\n")
        stub = (world.ws / "flake.nix").read_text()
        names = re.findall(r"^\s*inputs\.(?!minix\b|nixpkgs[.-])([\w-]+)\.url", stub, flags=re.MULTILINE)
        (world.ws / "flake.lock").write_text(lock_json(inputs=names))
        return Result(0, "")

    fake.on(NIX + ("flake", "lock"), lock)
    fake.on(NIX + ("flake", "archive"), archive)
    fake.on(NIX + ("flake", "update"), flake_update)
    fake.on(("nix-env",), delete)
    fake.on(("rm",), lambda argv: Path(argv[-1]).unlink(missing_ok=True) or Result(0))
    fake.add(lambda argv: compile_home(argv) if argv[0].endswith("minix-compile") else None)
    fake.add(lambda argv: activation(argv) if argv[0].endswith("/activate") else None)
    fake.on(HEALTH_COMMAND, lambda argv: Result(world.health_code, world.units))
    fake.on(("loginctl",), lambda argv: Result(0, world.linger + "\n"))
    fake.on(
        ("getent",),
        lambda argv: (
            Result(world.getent_code, f"{world.user}:x:1000:100::/home/{world.user}:{world.login_shell}\n")
            if world.getent_code == 0
            else Result(world.getent_code, "")
        ),
    )
    fake.on(RESET_FAILED, Result(0))


def make_setup(tmp_path: Path, **world_options: object) -> Setup:
    """A home folder with the account `ada`, a fake machine, and a context that runs real git."""
    home = tmp_path / "home"
    home.mkdir()
    world = World(tmp_path, home / "minix", **world_options)  # ty: ignore[invalid-argument-type]
    share = tmp_path / "share" / "minix"
    write_share(share)
    fake = FakeRunner()
    install_handlers(fake, world)
    runner = HybridRunner(fake, git_env())
    clock = FakeClock()
    lines: list[str] = []
    env = {"HOME": str(home), "XDG_STATE_HOME": str(world.state)}
    ctx = Context(
        user=world.user,
        home=home,
        cwd=home,
        env=env,
        runner=runner,
        clock=clock,
        say=lines.append,
        system="x86_64-linux",
        share=share,
        read_inputs=lambda url, locked=None: world.input_names,
    )
    return Setup(world, fake, runner, clock, lines, home, ctx, share)


def initialized(tmp_path: Path, **world_options: object) -> Setup:
    """A setup whose workspace `~/minix` exists: `minix init` ran, and the record of its calls is clear."""
    from minix import commands

    setup = make_setup(tmp_path, **world_options)
    assert commands.init(setup.ctx) == 0
    setup.lines.clear()
    setup.fake.calls.clear()
    setup.runner.git_calls.clear()
    setup.ctx.cwd = setup.ws
    return setup
