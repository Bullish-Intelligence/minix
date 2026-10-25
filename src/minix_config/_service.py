"""Form C (009-Q7, L118): fields on the service, no command object: `Service("web", program=pkgs.python3, args="-m http.server 8080")`."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, overload

from nixdantic import Expect, Model, Text
from pydantic import AfterValidator, StringConstraints, model_validator
from pydantic_core import PydanticCustomError
from pynix_pkgs._pkgref import Pkg, Prog

from . import _base
from ._base import Component, Restart, ServiceName, unit
from ._keys import Key, Mods, Part
from ._run import Run

__all__ = ["Bind", "Service"]

_SHELL = "|&;<>"
_SHELLS = {"bash", "zsh", "dash", "fish"}  # a program that is a shell: its `-c` text may hold these characters


def _environment(value: dict[str, str]) -> dict[str, str]:
    """009-D46: the names of environment variables and their values, one line each."""
    for key, text in value.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise PydanticCustomError(
                "env_name",
                'must name each variable with letters, digits, and "_", and not start with a digit, found "{key}"',
                {"key": key},
            )
        if "\n" in text or "\r" in text:
            raise PydanticCustomError(
                "env_value",
                'must hold each value on one line, and the value of "{key}" holds a line break',
                {"key": key},
            )
    return value


Environment = Annotated[
    dict[str, str], Expect('a dict of text, for example {"PORT": "8080"}'), AfterValidator(_environment)
]
RestartDelay = Annotated[
    str, StringConstraints(pattern=r"^[0-9]+(ms|s|min|h)?$"), Expect('a time such as "3s", "500ms", or "1min"')
]


def _run(program: Pkg, file: str, args: str) -> Run:
    return Run._make(program, file, tuple(shlex.split(args)))


def _check_main(program: Pkg, file: str, field: str) -> None:
    """009-D36: a package with no main program needs `file=`."""
    if not file and not isinstance(program, Prog):
        raise PydanticCustomError(
            "no_main_program",
            'must be a package with a main program, and `{attr}` has none. Name the file: file="NAME"'
            if field == "Service"
            else "must be a package with a main program, and `{attr}` has none",
            {"attr": program.attr},
        )


def _has_main(package: Pkg) -> Pkg:
    _check_main(package, "", "Bind")
    return package


def _warn_shell(where: str, field: str, program: Pkg, args: str) -> None:
    """009-D37: no shell runs, so `|`, `&`, `;`, `>`, `<` are plain arguments. The loader prints the warning."""
    found = sorted({c for c in args if c in _SHELL})
    if found and program.attr not in _SHELLS:
        _base.WARNINGS.append((where, field, "".join(found)))


class Service(Component):
    """A service that runs in your account: it starts at login and restarts by the rule `restart`."""

    _positional: ClassVar[tuple[str, ...]] = ("name",)
    name: ServiceName
    """The name of the service. Read its log with `journalctl --user -u NAME`."""
    program: Pkg
    """The program to run: `pkgs.NAME`. Basix finds its file (the main program of the package)."""
    file: str = ""
    """A file of the package other than its main program, for example `file="sleep"` with `pkgs.coreutils`."""
    args: str = ""
    """The arguments as one string, for example "-m http.server 8080". Spaces split the words; quotes group them. No shell runs."""
    restart: Restart = "on-failure"
    """When systemd starts the service again, for example "on-failure" or "always"."""
    restart_delay: RestartDelay = ""
    """The wait before a restart, for example "3s". Empty means the systemd default (100 ms)."""
    environment: Environment = {}
    """Variables that the program sees, for example {"PORT": "8080"}."""
    starts_with: Literal["login", "desktop"] = "login"
    """When the service starts: "login" (default) or "desktop", with your graphical session."""

    if TYPE_CHECKING:

        @overload
        def __init__(
            self,
            name: ServiceName,
            /,
            *,
            program: Prog,
            args: str = "",
            restart: Restart = "on-failure",
            restart_delay: RestartDelay = "",
            environment: Environment = {},
            starts_with: Literal["login", "desktop"] = "login",
        ) -> None: ...

        @overload
        def __init__(
            self,
            name: ServiceName,
            /,
            *,
            program: Pkg,
            file: str,
            args: str = "",
            restart: Restart = "on-failure",
            restart_delay: RestartDelay = "",
            environment: Environment = {},
            starts_with: Literal["login", "desktop"] = "login",
        ) -> None: ...

        def __init__(
            self,
            name,
            /,
            *,
            program,
            file="",
            args="",
            restart="on-failure",
            restart_delay="",
            environment={},
            starts_with="login",
        ) -> None:
            """A service that runs in your account: it starts at login and restarts by the rule `restart`.

            Args:
                name: The name of the service. Read its log with `journalctl --user -u NAME`.
                program: The program to run: `pkgs.NAME`. Basix finds its file (the main program of the package).
                file: A file of the package other than its main program, for example `file="sleep"` with `pkgs.coreutils`.
                args: The arguments as one string, for example "-m http.server 8080". No shell runs.
                restart: When systemd starts the service again, for example "on-failure" or "always".
                restart_delay: The wait before a restart, for example "3s". Empty means the systemd default (100 ms).
                environment: Variables that the program sees, for example {"PORT": "8080"}.
                starts_with: When the service starts: "login" (default) or "desktop", with your graphical session.
            """
            ...

    @model_validator(mode="after")
    def _main(self) -> Service:
        _check_main(self.program, self.file, "Service")
        return self

    def model_post_init(self, context: object, /) -> None:
        super().model_post_init(context)
        _warn_shell(self._where, "Service.args", self.program, self.args)

    def emit(self) -> Iterator[Model]:
        yield from unit(
            self.name,
            _run(self.program, self.file, self.args).systemd(),
            self.restart,
            environment=self.environment,
            desktop=self.starts_with == "desktop",
            restart_delay=self.restart_delay,
        )


class Bind(Part):
    """One key binding of Hyprland: a key combination that starts a program."""

    _positional: ClassVar[tuple[str, ...]] = ("mods", "key")
    mods: Mods
    """The modifier keys, joined by '+': SUPER, SHIFT, CTRL, ALT. For example "SUPER" or "SUPER+SHIFT"."""
    key: Key
    """The key: a letter or digit, F1 to F24, or a name such as Return, space, Tab."""
    program: Annotated[Pkg, AfterValidator(_has_main)]
    """The program to start: `pkgs.NAME`."""
    args: str = ""
    """The arguments as one string. A shell runs the command, so shell quoting applies."""

    if TYPE_CHECKING:

        def __init__(self, mods: Mods, key: Key, /, *, program: Prog, args: str = "") -> None:
            """One key binding of Hyprland: a key combination that starts a program.

            Args:
                mods: The modifier keys, joined by '+': SUPER, SHIFT, CTRL, ALT. For example "SUPER" or "SUPER+SHIFT".
                key: The key: a letter or digit, F1 to F24, or a name such as Return, space, Tab.
                program: The program to start: `pkgs.NAME`.
                args: The arguments as one string.
            """
            ...
    else:

        def __init__(self, mods, key, /, **data):
            super().__init__(mods=mods, key=key, **data)

        __init__.__pydantic_base_init__ = True

    def model_post_init(self, context: object, /) -> None:
        from ._base import _learner_site

        _warn_shell(_learner_site(), "Bind.args", self.program, self.args)

    def command_text(self) -> Text:
        return _run(self.program, "", self.args).shell()
