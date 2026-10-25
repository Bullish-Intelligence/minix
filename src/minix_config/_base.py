"""Shared parts of the curated tier: the component base, positional arguments, validators, and the user unit."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, ClassVar

from nixdantic import Expect, JsonValue, Model, Plain, Text
from pydantic import ConfigDict, PrivateAttr, StringConstraints
from pynix_hm.systemd.user import User as HmSystemdUser
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
    working_dir: bool = False,
    *,
    environment: dict[str, str] | None = None,
    desktop: bool = False,
    restart_delay: str = "",
) -> Iterator[Model]:
    """The two Home Manager values of one user unit (a flat field and an extra key of `Service` merge badly: G-S14-1).

    `environment` becomes `Environment=` lines. `desktop` starts the unit with the graphical session (`PartOf`, `After`,
    and `WantedBy` name `graphical-session.target`). `restart_delay` is `RestartSec` (009-D46, SG39).
    """
    # `Environment` is a typed flat field of the generated class. It must sit in this first value: inside the `Service`
    # dict of the second value it is moved to the flat field and then lost (observed, SG39).
    flat: dict[str, object] = (
        {"Service_Environment": [_environment_line(k, v) for k, v in environment.items()]} if environment else {}
    )
    yield HmSystemdUser(
        services={
            name: User_Services_Value(
                Unit_Description=f"minix service {name}",
                Service_ExecStart=exec_start,
                **flat,
            )
        }
    )
    service: dict[str, JsonValue] = {"Restart": restart}
    if working_dir:
        service["WorkingDirectory"] = "%h"
    if restart_delay:
        service["RestartSec"] = restart_delay
    target = "graphical-session.target" if desktop else "default.target"
    yield HmSystemdUser(
        services={
            name: User_Services_Value(
                Service=service,
                **({"Unit": {"PartOf": [target], "After": [target]}} if desktop else {}),
                Install={"WantedBy": [target]},
            )
        }
    )


def _environment_line(key: str, value: str) -> str:
    """One `Environment=` assignment. systemd reads `%` as a specifier and a space as a separator, so quote."""
    text = f"{key}={value}".replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{text}"' if any(c in value for c in " \t\"'\\") else text
