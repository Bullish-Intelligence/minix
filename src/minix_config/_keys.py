"""Key and modifier types of a Hyprland binding, and the base of a value that a capability takes (G29c split)."""

from __future__ import annotations

import re
from typing import Annotated

from nixdantic import Plain
from pydantic import AfterValidator, ConfigDict
from pydantic_core import PydanticCustomError

_KEY_NAMES = {
    "return",
    "space",
    "tab",
    "escape",
    "backspace",
    "left",
    "right",
    "up",
    "down",
    "print",
    "delete",
    "home",
    "end",
    "prior",
    "next",
    "minus",
    "equal",
    "comma",
    "period",
    "slash",
}


def _key(value: str) -> str:
    """A key is one letter or digit, F1 to F24, or one of a short list of names."""
    if (
        re.fullmatch(r"[A-Za-z0-9]", value)
        or re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", value)
        or value.lower() in _KEY_NAMES
    ):
        return value
    raise PydanticCustomError(
        "bind_key",
        "must be one key: a letter or digit (T, 5), F1 to F24, or a name such as Return, space, Tab, Escape, left, up. "
        "A bind has one key; write the modifiers in `mods`",
    )


_MODS = ("SUPER", "SHIFT", "CTRL", "ALT")


def _mods(value: str) -> str:
    """Modifiers are joined with '+', for example SUPER+SHIFT. An empty string means no modifier."""
    if value == "":
        return value
    parts = value.split("+")
    bad = [p for p in parts if p not in _MODS]
    if bad:
        raise PydanticCustomError(
            "bind_mods", "must hold only SUPER, SHIFT, CTRL, or ALT, joined by '+' (for example SUPER+SHIFT)"
        )
    if len(set(parts)) != len(parts):
        raise PydanticCustomError("bind_mods_twice", "must not name a modifier twice")
    return value


Key = Annotated[str, AfterValidator(_key)]
Mods = Annotated[str, AfterValidator(_mods)]


class Part(Plain):
    """The base of a value that a capability takes (not a capability itself)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, use_attribute_docstrings=True)
