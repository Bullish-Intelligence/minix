"""The curated tier of minix: `Home` and the items that a `home.py` file names.

One root call per file (`Home(...)`), keyword fields, typed list items, `pkgs.NAME` for packages, the identity of a
thing as positional arguments, its settings as keywords (009-Q8). `from minix_config import *` brings the learner names
of `__all__` and no other name (009-D23).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, get_args, get_origin

from nixdantic import ConfigRef, Expect, JsonValue, Model, Native, Package, Plain, Root, RootOnly, Text, Unset, fragment
from pydantic import AfterValidator, ConfigDict, PrivateAttr, StringConstraints, model_validator
from pydantic_core import PydanticCustomError
from pynix_hm import all as hm
from pynix_hm.home.__options__ import Home as HmHome
from pynix_native import OWN, Input, NativeModule, NixpkgsPin, input_json, registered_nixpkgs
from pynix_pkgs import pkgs

from ._base import _learner_site
from ._components import File, Files, Git, Neovim, Program, TerminalWorkspace
from ._desktop import Colours, Foot, Hyprland, Noctalia, Theme
from ._service import Bind, Service

# The learner names only: `from minix_config import *` brings exactly these (009-D23). `Run` stays out (reserve).
__all__ = [
    "OWN",
    "Bind",
    "Colours",
    "ConfigRef",
    "File",
    "Files",
    "Foot",
    "Git",
    "Home",
    "Hyprland",
    "Input",
    "Native",
    "NativeModule",
    "Neovim",
    "NixpkgsPin",
    "Noctalia",
    "Package",
    "Program",
    "Service",
    "TerminalWorkspace",
    "Text",
    "Theme",
    "hm",
    "pkgs",
]


def _unique(field: str, what: str):
    """A list validator: two items must not share `field` (Home Manager would merge them, not refuse them)."""

    def check(items: list) -> list:
        seen: set[str] = set()
        for item in items:
            key = getattr(item, field)
            if key in seen:
                raise PydanticCustomError(
                    "duplicate",
                    'holds {what} "{key}" twice: Home Manager would merge the two, so keep only one',
                    {"what": what, "key": key},
                )
            seen.add(key)
        return items

    return AfterValidator(check)


UserName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,31}$"),
    Expect("an account name (a lower case letter, then up to 31 lower case letters, digits, or '-')"),
]


def _hm_releases() -> tuple[str, ...]:
    """The releases that the generated `home.stateVersion` accepts (a `Literal` in `pynix_hm`)."""
    stack = [HmHome.model_fields["stateVersion"].annotation]
    while stack:
        found = stack.pop()
        if get_origin(found) is Literal:
            return tuple(str(v) for v in get_args(found))
        stack.extend(get_args(found))
    return ()


def _release(value: str) -> str:
    known = _hm_releases()
    if known and value not in known:
        raise PydanticCustomError(
            "state_version",
            'must be a release of Home Manager: {first} to {last}, for example "26.05"',
            {"first": known[0], "last": known[-1]},
        )
    return value


StateVersion = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{2}\.(05|11)$"),
    Expect('a release name such as "26.05" (two digits for the year, then .05 or .11)'),
    AfterValidator(_release),
]
Tool = Annotated[
    Git | Neovim | Files | TerminalWorkspace | Hyprland | Noctalia | Foot | Program,
    Expect(
        "a program: Git(...), Neovim(...), Files(), TerminalWorkspace(), Hyprland(...), Noctalia(...), Foot(...), "
        "or Program(pkgs.NAME)"
    ),
]
ServiceItem = Annotated[
    Service, Expect('a Service object, for example Service("web", program=pkgs.python3, args="-m http.server 8080")')
]


class ComponentError(Exception):
    """A value that a capability emits is not valid. It names the learner line of the capability."""

    def __init__(self, where: str, label: str, error: Exception):
        super().__init__(f"{where} ({label})")
        self.where, self.label, self.error = where, label, error


class Home(Plain):
    """One home: the programs, files, and services of your account."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, use_attribute_docstrings=True)
    _where: str = PrivateAttr(default="")

    user: UserName
    """Your account name: lower-case letters, digits, and '-', at most 32 characters."""
    state_version: StateVersion | None = None
    """The release of Home Manager that first set up this home, for example "26.05". `minix init` writes it. Never change it to a newer release: it protects the data of programs."""
    programs: list[Tool] = []
    """Programs for your account, for example `Git(...)`, `Neovim(...)`, or `Program(pkgs.ripgrep)`."""
    files: Annotated[
        list[Annotated[File, Expect('a File object, for example File(".config/a", "text")')]],
        _unique("path", "the path"),
    ] = []
    """Files in your home folder, for example `File(".config/demo.conf", "...")`."""
    services: Annotated[list[ServiceItem], _unique("name", "the service")] = []
    """Services that run in your account, for example `Service("web", program=pkgs.python3, args="-m http.server 8080")`."""
    options: list[Annotated[Model, RootOnly]] = []
    """Home Manager options that no capability covers, written with a class of `pynix_hm`."""
    native_modules: list[NativeModule] = []
    """Native Home Manager module files of the workspace folder, for example `NativeModule("shell.nix")`."""
    inputs: list[Input] = []
    """Extra flake inputs for the native modules, for example `Input("nirinit", "github:amaanq/nirinit")`."""
    values: dict[str, JsonValue] = {}
    """Values that a native module reads as `home.values.NAME`."""

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            user: UserName,
            state_version: StateVersion | None = None,
            programs: list[Tool] = [],
            files: list[File] = [],
            services: list[Service] = [],
            options: list[Root] = [],
            native_modules: list[NativeModule] = [],
            inputs: list[Input] = [],
            values: dict[str, JsonValue] = {},
        ) -> None:
            """One home: the programs, files, and services of your account.

            Args:
                user: Your account name: lower-case letters, digits, and '-', at most 32 characters.
                state_version: The release of Home Manager that first set up this home, for example "26.05". `minix init` writes it.
                programs: Programs for your account, for example `Git(...)`, `Neovim(...)`, or `Program(pkgs.ripgrep)`.
                files: Files in your home folder, for example `File(".config/demo.conf", "...")`.
                services: Services that run in your account, for example `Service("web", program=pkgs.python3, args="-m http.server 8080")`.
                options: Home Manager options that no capability covers, written with a class of `pynix_hm`.
                native_modules: Native Home Manager module files of the workspace folder, for example `NativeModule("shell.nix")`.
                inputs: Extra flake inputs for the native modules, for example `Input("nirinit", "github:amaanq/nirinit")`.
                values: Values that a native module reads as `home.values.NAME`.
            """
            ...

    @model_validator(mode="after")
    def _state_once(self) -> Home:
        """009-D39: the state version is set by the field or by an option, not by both."""
        if self.state_version is not None and any(
            isinstance(i, HmHome) and i.stateVersion is not Unset for i in self.options
        ):
            raise PydanticCustomError(
                "state_twice", "sets the state version twice: keep state_version, and remove stateVersion from options"
            )
        return self

    def model_post_init(self, context: object, /) -> None:
        self._where = _learner_site()

    def compile(self) -> dict[str, JsonValue]:
        """The compiled home: one fragment for each capability, with the learner line of each."""
        fragments: list[dict[str, JsonValue]] = []
        for component in (*self.programs, *self.files, *self.services):
            try:
                for value in component.emit():
                    fragments.append(fragment(value, source=component._where, label=type(component).__name__))
            except Exception as error:  # the component failed: name the learner line of the component
                raise ComponentError(component._where, type(component).__name__, error) from error
        if self.state_version is not None:
            version = HmHome(stateVersion=self.state_version)  # ty: ignore[invalid-argument-type]
            fragments.append(fragment(version, source=self._where, label="Home"))
        for value in self.options:
            fragments.append(fragment(value, source=self._where, label=type(value).__name__))
        out: dict[str, JsonValue] = {"user": self.user, "fragments": list(fragments)}
        if self.native_modules:  # native modules of the workspace folder (009-D40)
            out["modules"] = [
                {"file": m.file, "source": self._where + " (Home)", "root": "workspace"} for m in self.native_modules
            ]
        if self.inputs:
            out["inputs"] = [input_json(i, self._where) for i in self.inputs]  # ty: ignore[invalid-assignment]
        if registered_nixpkgs():  # every NixpkgsPin(...) of the entry (009-D53)
            out["nixpkgs"] = registered_nixpkgs()  # ty: ignore[invalid-assignment]
        if self.values:
            out["values"] = dict(self.values)
        return out
