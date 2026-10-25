"""The desktop steps of `minix apply` (009-D25, 009-D30, L28).

minix writes the config files of Hyprland and Noctalia. It installs neither program. After the build, before the
switch, this module does two things:

- It says which of the two programs the machine lacks. On a Basix machine the line also says how to add them.
- It runs `Hyprland --verify-config` on the built `hyprland.lua`. A file that Hyprland refuses stops the apply, so
  the learner never meets the error at the next login (S18 cases c09 and c10).

Both steps read the built home, not `home.py`: the files in the generation show what the home holds.
"""

import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pynix_engine import EXIT_STOPPED, Fail, Session


@dataclass(frozen=True)
class Desktop:
    """A desktop program that minix configures: its name, its command, and the file that proves the home configures it."""

    name: str
    command: str
    config: str


HYPRLAND = Desktop("Hyprland", "Hyprland", "home-files/.config/hypr/hyprland.lua")
NOCTALIA = Desktop("Noctalia", "noctalia", "home-files/.config/noctalia/config.toml")
PROGRAMS = (HYPRLAND, NOCTALIA)

# The command of a Basix machine (the core carries it). A plain host does not.
BASIX_COMMAND = "basix"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Lua cuts a long file name to "...TAIL" in its messages, so match any start. Only the name at the start is the file.
_CONFIG_FILE = re.compile(r"^[^\s:]*hyprland\.lua")


def on_path(command: str, env: Mapping[str, str]) -> bool:
    """Whether `command` is a program on the PATH of this run."""
    return shutil.which(command, path=env.get("PATH", "")) is not None


def configured(built: str, program: Desktop) -> bool:
    """Whether the built home writes a config file for `program`."""
    return (Path(built) / program.config).is_file()


def names_line(session: Session, missing: list[Desktop], env: Mapping[str, str]) -> str:
    """The one line for the programs that this machine lacks. A Basix machine gets the way to add them (009-D25)."""
    names = " and ".join(program.name for program in missing)
    key = "program_missing" if len(missing) == 1 else "programs_missing"
    if on_path(BASIX_COMMAND, env):
        key += "_basix"
    return session.word(key, names=names)


def verify_hyprland(session: Session, built: str) -> None:
    """Run `Hyprland --verify-config` on the built file. Stop with one line when Hyprland refuses the file (L28)."""
    config = Path(built) / HYPRLAND.config
    with tempfile.TemporaryDirectory(prefix="minix-hyprland-") as runtime:  # a private folder: no live session sees it
        result = session.run(
            ["env", f"XDG_RUNTIME_DIR={runtime}", HYPRLAND.command, "--verify-config", "-c", str(config)]
        )
    text = _ANSI.sub("", result.out)
    if result.code == 0:
        return
    if "Config parsing result" not in text:  # Hyprland did not reach the file: this is no verdict on the file
        session.say(session.word("hyprland_unchecked", code=result.code))
        return
    lines = [line.strip() for line in text.split("Config parsing result", 1)[1].splitlines()]
    detail = next((line for line in lines if line.strip(":= ")), "the file has an error")
    detail = _CONFIG_FILE.sub("hyprland.lua", detail)
    raise Fail(session.word("hyprland_bad", detail=detail), EXIT_STOPPED)


def check(session: Session, built: str) -> None:
    """The desktop steps for the built home `built`: the missing-program line, then the Hyprland check."""
    present = [program for program in PROGRAMS if configured(built, program)]
    missing = [program for program in present if not on_path(program.command, session.env)]
    if missing:
        session.say(names_line(session, missing, session.env))
    if HYPRLAND in present and HYPRLAND not in missing:
        verify_hyprland(session, built)
