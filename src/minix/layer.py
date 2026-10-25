"""The minix layer: what `minix` passes to the engine of `pynix_engine` (009-D26, CONCEPT section 4).

The engine names no layer. This file holds every fact that is specific to minix: the file names, the build attribute,
the switch (the activation of a Home Manager generation), the health query of the user units, and the plain words.
minix never uses `sudo`.
"""

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pynix_engine import EXIT_STOPPED, Command, Fail, Layer, Plan, Session, Words

from minix import desktop

# 009-D29: minix passes these two features to every Nix call, so it works on a host that has not turned them on.
NIX_FEATURES = ("--extra-experimental-features", "nix-command flakes")

# The health query (S16): every user unit on one line each, `UNIT LOAD ACTIVE SUB`.
HEALTH_COMMAND: Command = ("systemctl", "--user", "list-units", "--all", "--no-legend", "--plain", "--no-pager")
UNIT_SUFFIXES = (".service", ".socket", ".timer", ".path")
RESET_FAILED: Command = ("systemctl", "--user", "reset-failed")

# Commit identity when git knows no author: a first `minix apply` on a new account (S16 proposal 3).
FALLBACK_IDENTITY = ("minix", "minix@localhost")

DEFAULT_FOLDER = "~/minix"


@dataclass(frozen=True)
class MinixWords(Words):
    """The plain lines of the engine (009-D15) and the lines that only minix prints."""

    lock_failed: str = "The inputs could not be locked. Nothing was built, switched, or saved.\n{detail}"
    wrong_account: str = (
        '{entry} names the account "{named}", but you are "{running}". '
        'minix changes only your own home. Set user="{running}" in {entry}.'
    )
    no_entry: str = "Found no {entry} in {folder}. Run {name} init to make one."
    basix_workspace: str = (
        "{path} is a Basix workspace. {name} does not change it.\n"
        "To change the machine, run: basix apply\n"
        "To change your own home, go to {folder} and run: {name} apply"
    )
    no_linger: str = (
        "warning: lingering is off for {user}, so your user services stop when you log out. "
        "To keep them running, ask an administrator to run: loginctl enable-linger {user}"
    )
    scratch_ignored: str = "added {names} to .gitignore"
    inputs_failed: str = "warning: the flake inputs could not be rooted: {detail}"
    inputs_build_failed: str = "The input packages could not be built. Nothing was built, switched, or saved.\n{detail}"
    program_missing: str = "{names} is not on this machine"
    programs_missing: str = "{names} are not on this machine"
    program_missing_basix: str = "{names} is not on this machine; add Desktop() to system.py"
    programs_missing_basix: str = "{names} are not on this machine; add Desktop() to system.py"
    roll_failed: str = (
        "tried to roll back, but that also failed (exit {code}). "
        "The running home may still be the new one. The next {name} apply switches again"
    )
    hyprland_bad: str = (
        "Hyprland does not accept the config: {detail}. Nothing was switched or saved. The home did not change."
    )
    hyprland_unchecked: str = "warning: Hyprland could not check the config (exit {code}), so minix skipped the check"


WORDS = MinixWords()


def profile_path(env: Mapping[str, str], home: Path | None = None) -> Path:
    """The Home Manager profile of the account: the link to the active generation."""
    state = env.get("XDG_STATE_HOME")
    base = Path(state) if state else (home or Path(env.get("HOME", str(Path.home())))) / ".local" / "state"
    return base / "nix" / "profiles" / "home-manager"


def active_generation(profile: Path) -> str | None:
    """The store path of the active home generation, or None when the account has none yet."""
    return os.path.realpath(profile) if os.path.lexists(profile) else None


def generation_numbers(profile: Path, store_path: str) -> list[int]:
    """The numbers of the generations of the profile that link to `store_path`."""
    numbers: list[int] = []
    for link in profile.parent.glob("home-manager-*-link"):
        number = re.fullmatch(r"home-manager-(\d+)-link", link.name)
        if number and os.path.realpath(link) == store_path:
            numbers.append(int(number.group(1)))
    return sorted(numbers)


def unsettled_path(profile: Path) -> Path:
    """The mark that a switch started and did not end well. It lives beside the profile it describes."""
    return profile.with_name(profile.name + ".unsettled")


def delete_commands(profile: Path, built: str) -> list[Command]:
    """The commands that delete the generations of `built` from the profile (a failed generation leaves no trace)."""
    return [
        ["nix-env", "--profile", str(profile), "--delete-generations", str(number)]
        for number in generation_numbers(profile, built)
    ]


def roll_back_commands(profile: Path, earlier: str | None, built: str) -> list[Command]:
    """Activate the earlier generation again, reset the units, and delete the failed generation (S16 case 5).

    The activation of an earlier home does not always move the profile, so minix sets the profile to the earlier
    generation itself, before it deletes the failed one (nix-env refuses to delete the current generation). The last
    command clears the unsettled mark. A rollback that fails on the way leaves the mark, so the next apply switches.

    No command means that there is no earlier home to return to.
    """
    if earlier is None or earlier == built or not (Path(earlier) / "activate").exists():
        return []
    numbers = generation_numbers(profile, earlier)
    pin = ["--switch-generation", str(numbers[-1])] if numbers else ["--set", earlier]
    return [
        [f"{earlier}/activate"],
        list(RESET_FAILED),
        ["nix-env", "--profile", str(profile), *pin],
        *delete_commands(profile, built),
        ["rm", "-f", str(unsettled_path(profile))],
    ]


def plan(session: Session, built: str) -> Plan:
    """How minix switches to `built`: the Home Manager activation. It needs no `sudo` (S16)."""
    desktop.check(session, built)  # the missing-program line and the Hyprland check (009-D25, L28)
    profile = profile_path(session.env)
    earlier = active_generation(profile)  # read before the activation moves the profile
    mark = unsettled_path(profile)
    # A failed switch can leave the profile at the new generation (a failed rollback, a killed run). The mark says so:
    # the profile alone cannot tell a good switch from a bad one.
    unchanged = built == earlier and not mark.exists()
    if not unchanged:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(f"{built}\n")
    return Plan(
        unchanged=unchanged,
        reset=[list(RESET_FAILED)],  # an old failed unit must not fail the new home
        activate=[[f"{built}/activate"]],  # the activation also links the new generation into the profile
        finish=[["rm", "-f", str(mark)]],  # the switch and the health check passed
        restore=lambda: roll_back_commands(profile, earlier, built),
    )


def minix_layer(
    user: str,
    *,
    folder: str = DEFAULT_FOLDER,
    compile: Callable[[Session], None] | None = None,
    preflight: Callable[[Session], None] | None = None,
) -> Layer:
    """The layer of the account `user`. `compile` replaces the default compile step of the engine."""
    return Layer(
        name="minix",
        entry="home.py",
        json_file="home.json",
        folder=folder,
        noun="home",
        compiler_bin="minix-compile",
        build_attr=f".#homeConfigurations.{user}.activationPackage",
        health_command=HEALTH_COMMAND,
        unit_suffixes=UNIT_SUFFIXES,
        plan=plan,
        on_fail_env="MINIX_ON_FAIL",
        compile=compile,
        preflight=preflight,
        fallback_identity=FALLBACK_IDENTITY,
        words=WORDS,
    )


def stopped(message: str) -> Fail:
    """A run that stops before any change (exit 2)."""
    return Fail(message, EXIT_STOPPED)
