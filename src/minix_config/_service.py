"""Form C (009-Q7, L118): fields on the service, no command object: `Service("web", program=pkgs.python3, args="-m http.server 8080")`."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, overload

from nixdantic import Expect, Model, Text
from pydantic import AfterValidator, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError
from pynix_pkgs._pkgref import Pkg, Prog

from . import _base
from ._base import Component, Restart, ServiceName, unit
from ._keys import Key, Mods, Part
from ._run import Run

__all__ = ["Bind", "Service", "Step", "Timer"]

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


StartTime = Annotated[
    str,
    StringConstraints(pattern=r"^([0-9]+(ms|s|min|h)?|infinity)?$"),
    Expect('a time such as "90s" or "15min", or "infinity"'),
]
LimitTime = Annotated[
    str, StringConstraints(pattern=r"^([0-9]+(ms|s|min|h)?)?$"), Expect('a time such as "10s", "500ms", or "1min"')
]
Virtualization = Annotated[
    str,
    StringConstraints(pattern=r"^(!?[a-z][a-z0-9-]*)?$"),
    Expect('a kind of virtualization such as "no", "vm", "container", "kvm", or "!container"'),
]
LimitBurst = Annotated[int, Field(ge=1), Expect("a whole number of 1 or more")]
ExitStatus = Annotated[int, Field(ge=0, le=255), Expect("an exit status, a whole number from 0 to 255")]
_UNIT_SUFFIXES = (".service", ".target", ".socket", ".timer", ".path", ".mount", ".slice", ".scope")


def _one_line(value: str) -> str:
    """A value that systemd reads as one line."""
    if "\n" in value or "\r" in value:
        raise PydanticCustomError("one_line", "must be on one line, and this value holds a line break")
    return value


def _calendar(value: str) -> str:
    """A calendar time for `OnCalendar`. The syntax of systemd is large, so check the shape only: one line of text."""
    _one_line(value)
    if not value.strip():
        raise PydanticCustomError("calendar_empty", 'must be a calendar time such as "daily", and this value is empty')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PydanticCustomError(
            "calendar_control", "must hold only printable characters, and this value holds a control character"
        )
    return value


def _path_text(value: str) -> str:
    """The systemd text of a path: `~` and `~/x` become `%h` and `%h/x`, and `%` becomes `%%`. A space stays as it is."""
    _one_line(value)
    if value == "~" or value.startswith("~/"):
        return "%h" + value[1:].replace("%", "%%")
    if value.startswith("/"):
        return value.replace("%", "%%")
    raise PydanticCustomError("path", 'must be an absolute path or start with ~/, found "{path}"', {"path": value})


def _working_dir(value: str) -> str:
    """The systemd text of `working_dir`: empty stays empty."""
    return _path_text(value) if value else ""


def _condition_path(value: str) -> str:
    """The systemd text of a condition path: a leading `!` negates the condition."""
    return "!" + _path_text(value[1:]) if value.startswith("!") else _path_text(value)


def _unit_name(value: str) -> str:
    """The unit name for a bare name (`db` is `db.service`). A name with a unit suffix stays as it is."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", value):
        raise PydanticCustomError(
            "unit_name",
            'must be a unit name such as "db" or "network.target", found "{name}"',
            {"name": value},
        )
    return value if value.endswith(_UNIT_SUFFIXES) else value + ".service"


def _checked(convert):
    """A validator that runs `convert` to refuse a bad value, and keeps the value the learner wrote."""

    def check(value: str) -> str:
        convert(value)
        return value

    return AfterValidator(check)


WorkingDir = Annotated[str, Expect('an absolute path, "~", or a path that starts with "~/"'), _checked(_working_dir)]
ConditionPath = Annotated[
    str,
    Expect('an absolute path or a path that starts with "~/", with a leading "!" to negate'),
    _checked(_condition_path),
]
UnitName = Annotated[str, Expect('a unit name such as "db" or "network.target"'), _checked(_unit_name)]
Calendar = Annotated[
    str, Expect('a calendar time such as "daily", "hourly", or "Mon *-*-* 09:00"'), AfterValidator(_calendar)
]


def _run(program: Pkg, file: str, args: str) -> Run:
    return Run._make(program, file, tuple(shlex.split(args)))


def _check_main(program: Pkg, file: str, field: str) -> None:
    """009-D36: a package with no main program needs `file=`."""
    if not file and not isinstance(program, Prog):
        raise PydanticCustomError(
            "no_main_program",
            'must be a package with a main program, and `{attr}` has none. Name the file: file="NAME"'
            if field != "Bind"
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


class Step(Part):
    """One command that runs before a service starts. The service starts when every step has succeeded."""

    program: Pkg
    """The program to run: `pkgs.NAME`. Basix finds its file (the main program of the package)."""
    file: str = ""
    """A file of the package other than its main program, for example `file="sleep"` with `pkgs.coreutils`."""
    args: str = ""
    """The arguments as one string, for example "-sf http://localhost:8080". Spaces split the words; quotes group them. No shell runs."""

    if TYPE_CHECKING:

        @overload
        def __init__(self, *, program: Prog, args: str = "") -> None: ...

        @overload
        def __init__(self, *, program: Pkg, file: str, args: str = "") -> None: ...

        def __init__(self, *, program, file="", args="") -> None:
            """One command that runs before a service starts. The service starts when every step has succeeded.

            Args:
                program: The program to run: `pkgs.NAME`. Basix finds its file (the main program of the package).
                file: A file of the package other than its main program, for example `file="sleep"` with `pkgs.coreutils`.
                args: The arguments as one string, for example "-sf http://localhost:8080". No shell runs.
            """
            ...

    @model_validator(mode="after")
    def _main(self) -> Step:
        _check_main(self.program, self.file, "Step")
        return self

    def model_post_init(self, context: object, /) -> None:
        from ._base import _learner_site

        _warn_shell(_learner_site(), "Step.args", self.program, self.args)

    def command_text(self) -> Text:
        return _run(self.program, self.file, self.args).systemd()


class Timer(Part):
    """A calendar rule that starts a service, for example `Timer("daily")`. Without linger, a user timer runs only while you are logged in. A Basix host turns linger on, and `minix apply` warns when it is off."""

    _positional: ClassVar[tuple[str, ...]] = ("on_calendar",)
    on_calendar: Calendar
    """When the timer starts the service, as a systemd calendar time on one line, for example "daily", "hourly", or "Mon *-*-* 09:00"."""
    persistent: bool = True
    """Run a start that was missed while the machine was off, at the next chance. False skips a missed start."""
    randomized_delay: LimitTime = ""
    """A random wait of up to this time before each start, for example "30min". Empty means no wait."""

    if TYPE_CHECKING:

        def __init__(
            self, on_calendar: Calendar, /, *, persistent: bool = True, randomized_delay: LimitTime = ""
        ) -> None:
            """A calendar rule that starts a service, for example `Timer("daily")`. Without linger, a user timer runs only while you are logged in. A Basix host turns linger on, and `minix apply` warns when it is off.

            Args:
                on_calendar: When the timer starts the service, as a systemd calendar time on one line, for example "daily", "hourly", or "Mon *-*-* 09:00".
                persistent: Run a start that was missed while the machine was off, at the next chance. False skips a missed start.
                randomized_delay: A random wait of up to this time before each start, for example "30min". Empty means no wait.
            """
            ...
    else:

        def __init__(self, on_calendar, /, **data):
            super().__init__(on_calendar=on_calendar, **data)

        __init__.__pydantic_base_init__ = True

    def section(self) -> dict[str, bool | str]:
        """The keys of the `[Timer]` section."""
        keys: dict[str, bool | str] = {"OnCalendar": self.on_calendar, "Persistent": self.persistent}
        if self.randomized_delay:
            keys["RandomizedDelaySec"] = self.randomized_delay
        return keys


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
    type: Literal["simple", "exec", "forking", "oneshot", "notify", "dbus", "idle"] = "simple"
    """How systemd sees that the service is up, for example "exec" (up when the program has started) or "oneshot"."""
    working_dir: WorkingDir = ""
    """The folder where the program runs, for example "~/work" or "/var/lib/app". Empty means the home folder."""
    only_if_exists: list[ConditionPath] = []
    """Paths that must exist, for example ["~/.config/app/ready"]. A "!" at the start negates. A failed condition skips the unit and does not fail it."""
    only_if_glob: list[ConditionPath] = []
    """Patterns that must match a path, for example ["/dev/ttyUSB*"]. A failed condition skips the unit and does not fail it."""
    only_if_virtualization: Virtualization = ""
    """The kind of machine the service needs, for example "no", "vm", "container", or "!container". A failed condition skips the unit and does not fail it."""
    after: list[UnitName] = []
    """Units that start before this service, for example ["db"]. A bare name means NAME.service, another Service. A user unit cannot order itself after a system unit, because the user manager does not see the units of the system. To wait for a thing of the system, use an `only_if_*` condition or a `pre_start` step that waits with a bound."""
    wants: list[UnitName] = []
    """Units that this service pulls in when it starts, for example ["db"]. A bare name means NAME.service, another Service. A user unit cannot order itself after, or pull in, a system unit, because the user manager does not see the units of the system. To wait for a thing of the system, use an `only_if_*` condition or a `pre_start` step that waits with a bound."""
    pre_start: list[
        Annotated[Step, Expect('a Step object, for example Step(program=pkgs.curl, args="-sf http://localhost:8080")')]
    ] = []
    """Commands that run in order before the program starts, for example [Step(program=pkgs.coreutils, file="sleep", args="2")]. The service does not start when a step fails."""
    timeout_start: StartTime = ""
    """How long the start may take, for example "90s" or "15min". "infinity" sets no limit. Empty means the systemd default."""
    kill_mode: Literal["control-group", "mixed", "process"] = "control-group"
    """What a stop ends: "control-group" (default, every process), "mixed", or "process" (the main process only)."""
    start_limit_interval: LimitTime = ""
    """The time window of `start_limit_burst`, for example "60s". Empty means the systemd default."""
    start_limit_burst: LimitBurst | None = None
    """The most starts in the window of `start_limit_interval`, for example 5. More starts stop the service. None means the systemd default."""
    no_restart_on_exit: list[ExitStatus] = []
    """Exit codes after which systemd does not restart the service, for example [75, 76]."""
    timer: Timer | None = None
    """A calendar rule that starts the service, for example `Timer("daily")`. The timer starts the service, so it does not start at login. Without linger, a user timer runs only while you are logged in. A Basix host turns linger on, and `minix apply` warns when it is off. Example: `Service("backup", program=pkgs.restic, args="backup notes", working_dir="~", type="oneshot", timer=Timer("daily"))`. Not with `starts_with="desktop"`."""

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
            type: Literal["simple", "exec", "forking", "oneshot", "notify", "dbus", "idle"] = "simple",
            working_dir: WorkingDir = "",
            only_if_exists: list[ConditionPath] = [],
            only_if_glob: list[ConditionPath] = [],
            only_if_virtualization: Virtualization = "",
            after: list[UnitName] = [],
            wants: list[UnitName] = [],
            pre_start: list[Step] = [],
            timeout_start: StartTime = "",
            kill_mode: Literal["control-group", "mixed", "process"] = "control-group",
            start_limit_interval: LimitTime = "",
            start_limit_burst: LimitBurst | None = None,
            no_restart_on_exit: list[ExitStatus] = [],
            timer: Timer | None = None,
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
            type: Literal["simple", "exec", "forking", "oneshot", "notify", "dbus", "idle"] = "simple",
            working_dir: WorkingDir = "",
            only_if_exists: list[ConditionPath] = [],
            only_if_glob: list[ConditionPath] = [],
            only_if_virtualization: Virtualization = "",
            after: list[UnitName] = [],
            wants: list[UnitName] = [],
            pre_start: list[Step] = [],
            timeout_start: StartTime = "",
            kill_mode: Literal["control-group", "mixed", "process"] = "control-group",
            start_limit_interval: LimitTime = "",
            start_limit_burst: LimitBurst | None = None,
            no_restart_on_exit: list[ExitStatus] = [],
            timer: Timer | None = None,
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
            type="simple",
            working_dir="",
            only_if_exists=[],
            only_if_glob=[],
            only_if_virtualization="",
            after=[],
            wants=[],
            pre_start=[],
            timeout_start="",
            kill_mode="control-group",
            start_limit_interval="",
            start_limit_burst=None,
            no_restart_on_exit=[],
            timer=None,
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
                type: How systemd sees that the service is up, for example "exec" (up when the program has started) or "oneshot".
                working_dir: The folder where the program runs, for example "~/work" or "/var/lib/app". Empty means the home folder.
                only_if_exists: Paths that must exist, for example ["~/.config/app/ready"]. A "!" at the start negates. A failed condition skips the unit and does not fail it.
                only_if_glob: Patterns that must match a path, for example ["/dev/ttyUSB*"]. A failed condition skips the unit and does not fail it.
                only_if_virtualization: The kind of machine the service needs, for example "no", "vm", "container", or "!container". A failed condition skips the unit and does not fail it.
                after: Units that start before this service, for example ["db"]. A bare name means NAME.service, another Service. A user unit cannot order itself after a system unit, because the user manager does not see the units of the system. To wait for a thing of the system, use an `only_if_*` condition or a `pre_start` step that waits with a bound.
                wants: Units that this service pulls in when it starts, for example ["db"]. A bare name means NAME.service, another Service. A user unit cannot order itself after, or pull in, a system unit, because the user manager does not see the units of the system. To wait for a thing of the system, use an `only_if_*` condition or a `pre_start` step that waits with a bound.
                pre_start: Commands that run in order before the program starts, for example [Step(program=pkgs.coreutils, file="sleep", args="2")]. The service does not start when a step fails.
                timeout_start: How long the start may take, for example "90s" or "15min". "infinity" sets no limit. Empty means the systemd default.
                kill_mode: What a stop ends: "control-group" (default, every process), "mixed", or "process" (the main process only).
                start_limit_interval: The time window of `start_limit_burst`, for example "60s". Empty means the systemd default.
                start_limit_burst: The most starts in the window of `start_limit_interval`, for example 5. More starts stop the service. None means the systemd default.
                no_restart_on_exit: Exit codes after which systemd does not restart the service, for example [75, 76].
                timer: A calendar rule that starts the service, for example `Timer("daily")`. The timer starts the service, so it does not start at login. Without linger, a user timer runs only while you are logged in. A Basix host turns linger on, and `minix apply` warns when it is off. Example: `Service("backup", program=pkgs.restic, args="backup notes", working_dir="~", type="oneshot", timer=Timer("daily"))`. Not with `starts_with="desktop"`.
            """
            ...

    @model_validator(mode="after")
    def _main(self) -> Service:
        _check_main(self.program, self.file, "Service")
        if self.timer is not None and self.starts_with == "desktop":
            raise PydanticCustomError(
                "timer_desktop",
                'cannot have a timer with starts_with="desktop": the timer starts the service. Keep one of the two',
            )
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
            kind=self.type,
            working_dir=_working_dir(self.working_dir),
            only_if_exists=[_condition_path(p) for p in self.only_if_exists],
            only_if_glob=[_condition_path(p) for p in self.only_if_glob],
            only_if_virtualization=self.only_if_virtualization,
            after=[_unit_name(n) for n in self.after],
            wants=[_unit_name(n) for n in self.wants],
            pre_start=[step.command_text() for step in self.pre_start],
            timeout_start=self.timeout_start,
            kill_mode=self.kill_mode,
            start_limit_interval=self.start_limit_interval,
            start_limit_burst=self.start_limit_burst,
            no_restart_on_exit=self.no_restart_on_exit,
            timer=self.timer.section() if self.timer else None,
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
