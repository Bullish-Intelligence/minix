"""The command object, form B1: `Run(pkgs.python3, "-m", "http.server", "8080")` (SV1, with the typed program of SV4)."""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING

from nixdantic import Plain, Text
from pydantic import ConfigDict
from pynix_pkgs._pkgref import Exe, PackageError, Pkg, Prog

__all__ = ["Run"]


class NoMainProgram(PackageError):
    """`Run` on a package that has no main program (in reserve; the decided form uses `Service(... file=...)`)."""

    def __init__(self, attr: str):
        super().__init__(f'`{attr}` has no main program to run. Name the file with file="NAME".', attr)


_SAFE = re.compile(r"^[A-Za-z0-9_@+=:,./-]+$")


def _systemd_word(word: str) -> str:
    """One word of an `ExecStart` line (systemd quoting: `%` and `$` are special, a space needs quotes)."""
    word = word.replace("%", "%%").replace("$", "$$")
    if _SAFE.match(word):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Run(Plain):
    """A command that runs a program: `Run(pkgs.python3, "-m", "http.server", "8080")`.

    Basix finds the file of the program itself (the `meta.mainProgram` of the package), so you write no path.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, use_attribute_docstrings=True)

    program: Pkg
    """The program: `pkgs.NAME`."""
    file: str = ""
    """The file below `bin/`, when it is not the main program of the package."""
    args: list[str] = []
    """The arguments, one string for each word."""

    if TYPE_CHECKING:

        def __init__(self, program: Prog, /, *args: str) -> None:
            """Run the main program of a package.

            Args:
                program: The program: `pkgs.NAME`.
                args: The arguments, one string for each word.
            """
            ...
    else:

        def __init__(self, program, /, *args):
            if isinstance(program, Pkg) and not isinstance(program, Prog):
                raise NoMainProgram(program.attr)
            Plain.__init__(self, program=program, args=list(args))

        __init__.__pydantic_base_init__ = True

    @classmethod
    def _make(cls, program: Pkg, file: str, args: tuple[str, ...]) -> Run:
        return cls.model_validate({"program": program, "file": file, "args": list(args)})

    def _head(self) -> list:
        return [Exe(attr=self.program.attr)] if not self.file else [self.program, f"/bin/{self.file}"]

    def systemd(self) -> Text:
        """The `ExecStart` text: systemd quoting, no shell."""
        return Text(*self._head(), *(" " + _systemd_word(a) for a in self.args))

    def shell(self) -> Text:
        """The text for a shell (Hyprland `exec_cmd`): shell quoting."""
        return Text(*self._head(), *(" " + shlex.quote(a) for a in self.args))
