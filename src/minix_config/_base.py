"""Shared parts of the curated tier: the component base, positional arguments, validators, and the user unit."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, ClassVar

from nixdantic import Expect, Model, Plain, Text
from pydantic import ConfigDict, PrivateAttr, StringConstraints
from pynix_hm.systemd.user import User as HmSystemdUser
from pynix_hm.systemd.user import User_Automounts_Value as User_Timers_Value  # Home Manager shares one class for them
from pynix_hm.systemd.user import User_Services_Value

_HERE = Path(__file__).resolve().parent

ServiceName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
    Expect("a service name (a lower case letter, then lower case letters, digits, '_' or '-')"),
]
Restart = Annotated[
    str,
    StringConstraints(pattern=r"^(no|on-success|on-failure|on-abnormal|on-abort|on-watchdog|always)$"),
    Expect("one of no, on-success, on-failure, on-abnormal, on-abort, on-watchdog, always"),
]


def _learner_site() -> str:
    """`file:line` of the first caller outside this package and Pydantic: the line of the learner."""
    for frame in inspect.stack()[2:]:
        path = Path(frame.filename)
        if _HERE in path.resolve().parents or {"pydantic", "nixdantic"} & set(path.parts) or path.name.startswith("<"):
            continue
        return f"{path.name}:{frame.lineno}"
    return "unknown"


class Component(Plain):
    """The base of every part of a home. A component is always an instance.

    A component may take its identity as positional arguments (`_positional` names them); the settings are keywords.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, use_attribute_docstrings=True)
    _where: str = PrivateAttr(default="")
    _positional: ClassVar[tuple[str, ...]] = ()

    def __init__(self, *args: object, **data: object) -> None:
        cls, names = type(self), type(self)._positional
        if len(args) > len(names):
            raise TypeError(
                f"{cls.__name__}.__init__() takes {len(names) + 1} positional arguments but {len(args) + 1} were given"
            )
        kw = [n for n in names if n in data]
        if kw:
            raise TypeError(
                f"{cls.__name__}.__init__() got some positional-only arguments passed as keyword arguments: '{', '.join(kw)}'"
            )
        if len(args) < len(names):
            raise TypeError(f"{cls.__name__}.__init__() missing {len(names) - len(args)} required positional arguments")
        super().__init__(**dict(zip(names, args, strict=True)), **data)

    __init__.__pydantic_base_init__ = True  # ty: ignore[unresolved-attribute]

    def model_post_init(self, context: object, /) -> None:
        self._where = _learner_site()

    def emit(self) -> Iterator[Model]:
        """The Home Manager values of this component. Override it."""
        return iter(())


WARNINGS: list[tuple[str, str, str]] = []  # (file:line, field, the shell characters), read by the loader


def unit(
    name: str,
    exec_start: str | Text,
    restart: str,
    *,
    environment: dict[str, str] | None = None,
    desktop: bool = False,
    restart_delay: str = "",
    kind: str = "simple",
    working_dir: str = "",
    only_if_exists: list[str] | None = None,
    only_if_glob: list[str] | None = None,
    only_if_virtualization: str = "",
    after: list[str] | None = None,
    wants: list[str] | None = None,
    pre_start: list[Text] | None = None,
    timeout_start: str = "",
    kill_mode: str = "control-group",
    start_limit_interval: str = "",
    start_limit_burst: int | None = None,
    no_restart_on_exit: list[int] | None = None,
    timer: dict[str, Any] | None = None,
) -> Iterator[Model]:
    """The one Home Manager value of a user unit.

    `environment` becomes `Environment=` lines. `desktop` starts the unit with the graphical session (`PartOf`, `After`,
    and `WantedBy` name `graphical-session.target`). `restart_delay` is `RestartSec` (009-D46). The other keywords hold
    systemd text that is ready to write (`working_dir`, the paths, and the unit names). A default or empty value writes
    no key. `timer` holds the keys of a `[Timer]` section: the value then also holds a timer unit of the same name, and
    the timer (not the service) is wanted by `timers.target`.
    """
    # The typed flat fields and the extra keys of one head (`Service`, `Unit`) join key by key (pynix 0.3.0).
    # Each key sits in one place only: the join keeps the extra key when both set the same leaf.
    flat: dict[str, object] = (
        {"Service_Environment": [_environment_line(k, v) for k, v in environment.items()]} if environment else {}
    )
    target = "graphical-session.target" if desktop else "default.target"
    service: dict[str, Any] = {"Restart": restart}
    if restart_delay:
        service["RestartSec"] = restart_delay
    if kind != "simple":
        service["Type"] = kind
    if working_dir:
        service["WorkingDirectory"] = working_dir
    if pre_start:
        service["ExecStartPre"] = pre_start
    if timeout_start:
        service["TimeoutStartSec"] = timeout_start
    if kill_mode != "control-group":
        service["KillMode"] = kill_mode
    if no_restart_on_exit:
        service["RestartPreventExitStatus"] = " ".join(str(n) for n in no_restart_on_exit)
    head: dict[str, Any] = {}
    if desktop:
        head["PartOf"] = [target]
    if desktop or after:
        head["After"] = list(dict.fromkeys([*([target] if desktop else []), *(after or [])]))
    if wants:
        head["Wants"] = list(dict.fromkeys(wants))
    if only_if_exists:
        head["ConditionPathExists"] = only_if_exists
    if only_if_glob:
        head["ConditionPathExistsGlob"] = only_if_glob
    if only_if_virtualization:
        head["ConditionVirtualization"] = only_if_virtualization
    if start_limit_interval:
        head["StartLimitIntervalSec"] = start_limit_interval
    if start_limit_burst is not None:
        head["StartLimitBurst"] = start_limit_burst
    # The service and its timer are two keys of one value: `services` and `timers` of the same head.
    yield HmSystemdUser(
        services={
            name: User_Services_Value(
                Unit_Description=f"minix service {name}",
                Service_ExecStart=exec_start,
                **flat,
                Service=service,
                **({"Unit": head} if head else {}),
                **({} if timer else {"Install": {"WantedBy": [target]}}),
            )
        },
        **(
            {
                "timers": {
                    name: User_Timers_Value(
                        Unit_Description=f"minix timer {name}", Timer=timer, Install={"WantedBy": ["timers.target"]}
                    )
                }
            }
            if timer
            else {}
        ),
    )


def _environment_line(key: str, value: str) -> str:
    """One `Environment=` assignment. systemd reads `%` as a specifier and a space as a separator, so quote."""
    text = f"{key}={value}".replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{text}"' if any(c in value for c in " \t\"'\\") else text
