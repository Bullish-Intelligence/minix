"""The desktop capabilities of minix (S18, 009-D25): Hyprland, Noctalia, Foot, and a shared Theme.

A machine gets the programs from outside minix. On a Basix machine, `Desktop()` installs them and puts the default
look in `/etc/xdg`. These capabilities write the config files of your account only: `Hyprland` and `Noctalia` set
`package = null`, so Home Manager installs nothing. Home Manager has no `null` for the package of foot, so it adds
the foot package of the same version to the home.

The `Hyprland` capability builds one Lua text. The text loads the Basix default file when this machine has one. Else it
loads the base of minix. Then come your settings. Python checks the typed fields. Python cannot check `extra_lua`:
`minix apply` runs `Hyprland --verify-config` on the result (L28).

Each value that the learner may set in another way, for example with `options=[...]`, is a `Default(...)` (L42): the
learner value wins with no conflict.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, Literal

from nixdantic import ConfigRef, Default, Expect, JsonValue, Model, Native, Package, PathRef, Text
from pydantic import AfterValidator, Field, StringConstraints
from pydantic_core import PydanticCustomError
from pynix_hm.programs.foot import Foot as HmFoot
from pynix_hm.programs.noctalia import Noctalia as HmNoctalia
from pynix_hm.wayland.windowManager import WindowManager, WindowManager_Hyprland, WindowManager_Hyprland_Systemd

from ._base import Component
from ._keys import Part
from ._service import Bind

__all__ = ["Colours", "Foot", "Hyprland", "Noctalia", "Theme"]

#: Where Basix puts its default Hyprland file (`Desktop()`). Hyprland itself never reads this folder for a file.
BASIX_DEFAULT = "/etc/xdg/hypr/hyprland.lua"

#: The base of minix: the settings that a machine with no Basix default gets. It holds no program name except `foot`.
BASE_LUA = """hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })
hl.config({
    general = { gaps_in = 5, gaps_out = 20, border_size = 2, layout = "dwindle" },
    decoration = { rounding = 10 },
    misc = { force_default_wallpaper = 0, disable_hyprland_logo = true },
})
hl.bind("SUPER + Q", hl.dsp.exec_cmd("foot"))
hl.bind("SUPER + C", hl.dsp.window.close())
hl.bind("SUPER + V", hl.dsp.window.float({ action = "toggle" }))
hl.bind("SUPER + left", hl.dsp.focus({ direction = "left" }))
hl.bind("SUPER + right", hl.dsp.focus({ direction = "right" }))
hl.bind("SUPER + up", hl.dsp.focus({ direction = "up" }))
hl.bind("SUPER + down", hl.dsp.focus({ direction = "down" }))
for i = 1, 10 do
    local key = i % 10
    hl.bind("SUPER + " .. key, hl.dsp.focus({ workspace = i }))
    hl.bind("SUPER + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end
hl.bind("SUPER + mouse:272", hl.dsp.window.drag(), { mouse = true })
hl.bind("SUPER + mouse:273", hl.dsp.window.resize(), { mouse = true })
"""


def _hex(value: str) -> str:
    """A colour is six hex digits. Each wrong form gets its own reason."""
    if value.startswith("#"):
        raise PydanticCustomError("colour_hash", "must be written without '#': write 'ff8800', not '#ff8800'")
    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        raise PydanticCustomError(
            "colour_short", "must have six hex digits, rrggbb. Three digits are not enough: write 'ff8800', not 'f80'"
        )
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise PydanticCustomError(
            "colour", "must be a colour of six hex digits, rrggbb, for example 'ff8800' (digits 0-9 and letters a-f)"
        )
    return value.lower()


_MONITOR = re.compile(
    r"^(?P<name>[A-Za-z0-9_-]*),"  # output name; empty means every output
    r"(?P<mode>preferred|highres|highrr|\d{3,5}x\d{3,5}(@\d{1,3}(\.\d+)?)?),"
    r"(?P<pos>auto(-left|-right|-up|-down)?|-?\d{1,5}x-?\d{1,5}),"
    r"(?P<scale>auto|\d(\.\d{1,2})?)$"
)


def _monitor(value: str) -> str:
    """A monitor is `NAME,MODE,POSITION,SCALE`, for example `DP-1,1920x1080@60,0x0,1`."""
    if not _MONITOR.match(value):
        parts = value.split(",")
        if len(parts) != 4:
            raise PydanticCustomError(
                "monitor_parts",
                "must have four parts, separated by commas: NAME,MODE,POSITION,SCALE. "
                "Example: DP-1,1920x1080@60,0x0,1. Found {n} part(s)",
                {"n": len(parts)},
            )
        raise PydanticCustomError(
            "monitor_form",
            "must read NAME,MODE,POSITION,SCALE. NAME is the output (DP-1, HDMI-A-1, or empty for all). "
            "MODE is preferred, highres, or WIDTHxHEIGHT or WIDTHxHEIGHT@RATE. POSITION is auto or XxY (for example 0x0). "
            "SCALE is auto or a number such as 1 or 1.5. Check the part '{part}'",
            {"part": _bad_monitor_part(parts)},
        )
    return value


def _bad_monitor_part(parts: list[str]) -> str:
    patterns = [
        r"[A-Za-z0-9_-]*",
        r"preferred|highres|highrr|\d{3,5}x\d{3,5}(@\d{1,3}(\.\d+)?)?",
        r"auto(-left|-right|-up|-down)?|-?\d{1,5}x-?\d{1,5}",
        r"auto|\d(\.\d{1,2})?",
    ]
    for part, pattern in zip(parts, patterns, strict=True):
        if not re.fullmatch(pattern, part):
            return part
    return parts[0]


Hex = Annotated[str, AfterValidator(_hex)]
Monitor = Annotated[str, AfterValidator(_monitor)]
Gap = Annotated[int, Field(ge=0, le=200), Expect("a whole number from 0 to 200 (pixels)")]
Border = Annotated[int, Field(ge=0, le=20), Expect("a whole number from 0 to 20 (pixels)")]
BarThickness = Annotated[int, Field(ge=16, le=120), Expect("a whole number from 16 to 120 (pixels)")]
FontSize = Annotated[int, Field(ge=6, le=48), Expect("a whole number from 6 to 48 (points)")]
FontName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9 _-]{1,60}$"),
    Expect("a font name (letters, digits, spaces, '_' or '-'), for example monospace or JetBrains Mono"),
]
Mode = Annotated[Literal["dark", "light"], Expect("dark or light")]


class Colours(Part):
    """The two colours of a text area."""

    background: Hex = "1e1e2e"
    """The background colour: six hex digits, rrggbb, for example '1e1e2e'."""
    foreground: Hex = "cdd6f4"
    """The text colour: six hex digits, rrggbb, for example 'cdd6f4'."""

    if TYPE_CHECKING:

        def __init__(self, *, background: Hex = "1e1e2e", foreground: Hex = "cdd6f4") -> None:
            """The two colours of a text area.

            Args:
                background: The background colour: six hex digits, rrggbb, for example '1e1e2e'.
                foreground: The text colour: six hex digits, rrggbb, for example 'cdd6f4'.
            """
            ...


class Theme(Part):
    """One look for the whole desktop. Pass the same `Theme` to `Hyprland`, `Noctalia`, and `Foot`."""

    accent: Hex = "33ccff"
    """The accent colour (the border of the active window): six hex digits, rrggbb."""
    colours: Colours = Colours()
    """The colours of the terminal."""
    font: FontName = "monospace"
    """The font of the terminal."""
    font_size: FontSize = 11
    """The size of the terminal font, in points."""
    mode: Mode = "dark"
    """Dark or light, for the Noctalia bar."""

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            accent: Hex = "33ccff",
            colours: Colours = Colours(),  # noqa: B008
            font: FontName = "monospace",
            font_size: FontSize = 11,
            mode: Mode = "dark",
        ) -> None:
            """One look for the whole desktop. Pass the same `Theme` to `Hyprland`, `Noctalia`, and `Foot`.

            Args:
                accent: The accent colour (the border of the active window): six hex digits, rrggbb.
                colours: The colours of the terminal.
                font: The font of the terminal.
                font_size: The size of the terminal font, in points.
                mode: Dark or light, for the Noctalia bar.
            """
            ...


def _lua(text: str) -> str:
    """A Lua string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _default_look() -> str:
    """The Lua that loads the Basix default when the file exists (009-D30), else the base of minix."""
    indented = "".join(f"    {line}\n" if line else "\n" for line in BASE_LUA.splitlines())
    return (
        f"local basix_default = {_lua(BASIX_DEFAULT)}\n"
        'local default_file = io.open(basix_default, "r")\n'
        "if default_file then\n"
        "    default_file:close()\n"
        "    dofile(basix_default)\n"
        "else\n"
        f"{indented}"
        "end\n"
    )


type TextPart = str | Package | ConfigRef | Native | PathRef | Text
type FootValue = bool | int | float | str | Text | None | list[bool | int | float | str | Text | None]


def _join(parts: list[TextPart]) -> str | Text:
    """One string when all parts are text; a `Text` when a part is a reference."""
    merged: list[TextPart] = []
    for part in parts:
        last = merged[-1] if merged else None
        if isinstance(part, str) and isinstance(last, str):
            merged[-1] = last + part
        else:
            merged.append(part)
    first = merged[0]
    return first if len(merged) == 1 and isinstance(first, str) else Text(*merged)


def _command(bind: Bind) -> list[TextPart]:
    """The Lua call `hl.dsp.exec_cmd("...")`. Literal parts are escaped; a reference part stays a reference."""
    run = bind.command_text()  # the form of the spike builds the text (a string, or a `Text` with references)
    parts = [run] if isinstance(run, str) else list(run.parts)
    out: list[TextPart] = ['hl.dsp.exec_cmd("']
    for part in parts:
        out.append(_lua(part)[1:-1] if isinstance(part, str) else part)
    out.append('")')
    return out


class Hyprland(Component):
    """Settings for the Hyprland window manager. Hyprland must be on the machine. Your settings come after the default look."""

    theme: Theme | None = None
    """A `Theme`. Its accent colour sets the border of the active window, unless `active_border` is set."""
    gaps_in: Gap | None = None
    """The gap between windows, in pixels. The default is 5."""
    gaps_out: Gap | None = None
    """The gap between a window and the screen edge, in pixels. The default is 20."""
    border_size: Border | None = None
    """The width of a window border, in pixels. The default is 2."""
    active_border: Hex | None = None
    """The border colour of the active window: six hex digits, rrggbb, for example 'ff8800'."""
    monitors: list[Monitor] = []
    """Screens, as NAME,MODE,POSITION,SCALE. For example "DP-1,1920x1080@60,0x0,1". Run `hyprctl monitors` for the names."""
    binds: list[Bind] = []
    """More key bindings. The default bindings stay (for example SUPER+Q starts the terminal)."""
    extra_lua: str = ""
    """Lua code that runs last. Python does not check it: Hyprland reads it when you log in."""

    if TYPE_CHECKING:  # help while you type `Hyprland(...)`; the editor reads it, Python never runs it (S7)

        def __init__(
            self,
            *,
            theme: Theme | None = None,
            gaps_in: Gap | None = None,
            gaps_out: Gap | None = None,
            border_size: Border | None = None,
            active_border: Hex | None = None,
            monitors: list[Monitor] = [],
            binds: list[Bind] = [],
            extra_lua: str = "",
        ) -> None:
            """Settings for the Hyprland window manager. Hyprland must be on the machine. Your settings come after the default look.

            Args:
                theme: A `Theme`. Its accent colour sets the border of the active window, unless `active_border` is set.
                gaps_in: The gap between windows, in pixels. The default is 5.
                gaps_out: The gap between a window and the screen edge, in pixels. The default is 20.
                border_size: The width of a window border, in pixels. The default is 2.
                active_border: The border colour of the active window: six hex digits, rrggbb, for example 'ff8800'.
                monitors: Screens, as NAME,MODE,POSITION,SCALE. For example "DP-1,1920x1080@60,0x0,1". Run `hyprctl monitors` for the names.
                binds: More key bindings. The default bindings stay (for example SUPER+Q starts the terminal).
                extra_lua: Lua code that runs last. Python does not check it: Hyprland reads it when you log in.
            """
            ...

    def lua(self) -> str | Text:
        """The `extraConfig` text: the default look first, then the settings of the learner.

        The default look is the Basix file when it exists on the machine (checked when Hyprland reads the text), else
        the base of minix.
        """
        border = self.active_border or (self.theme.accent if self.theme else None)
        general = [
            f"{key} = {value}"
            for key, value in (
                ("gaps_in", self.gaps_in),
                ("gaps_out", self.gaps_out),
                ("border_size", self.border_size),
            )
            if value is not None
        ]
        if border is not None:
            general.append(f'col = {{ active_border = "rgb({border})" }}')
        parts: list[TextPart] = [_default_look()]
        if general:
            parts.append("hl.config({ general = { " + ", ".join(general) + " } })\n")
        for monitor in self.monitors:
            name, mode, position, scale = monitor.split(",")
            scale_lua = _lua(scale) if scale == "auto" else scale
            parts.append(
                f"hl.monitor({{ output = {_lua(name)}, mode = {_lua(mode)}, position = {_lua(position)}, scale = {scale_lua} }})\n"
            )
        for bind in self.binds:
            combo = " + ".join([*bind.mods.split("+"), bind.key]) if bind.mods else bind.key
            parts += [f'hl.bind("{combo}", ', *_command(bind), ")\n"]
        if self.extra_lua:
            parts.append(self.extra_lua if self.extra_lua.endswith("\n") else self.extra_lua + "\n")
        return _join(parts)

    def emit(self) -> Iterator[Model]:
        # Each value that a learner may set in another way is a `Default` (L42): a value in `options=[...]` wins.
        yield WindowManager(
            hyprland=WindowManager_Hyprland(
                enable=Default(True),
                package=Default(None),  # the machine has Hyprland (Basix: `Desktop()`)
                portalPackage=Default(None),  # the machine has the portal
                configType=Default("lua"),  # Hyprland reads a lua file before a conf file, in every folder
                systemd=WindowManager_Hyprland_Systemd(enable=Default(False)),  # UWSM (Basix) owns the session targets
                extraConfig=self.lua(),
            )
        )


def _merge(base: dict, extra: dict) -> dict:
    """A deep merge: `extra` wins."""
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class Noctalia(Component):
    """Settings for the Noctalia bar. Noctalia must be on the machine."""

    theme: Theme | None = None
    """A `Theme`. Its mode (dark or light) sets the look of the bar."""
    bar_thickness: BarThickness | None = None
    """The height of the bar, in pixels. The default is 44."""
    extra_settings: dict[str, JsonValue] = {}
    """Other Noctalia settings, as a dict of dicts: {"shell": {"font": "Inter"}}. Python does not check the names."""

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            theme: Theme | None = None,
            bar_thickness: BarThickness | None = None,
            extra_settings: dict[str, JsonValue] = {},
        ) -> None:
            """Settings for the Noctalia bar. Noctalia must be on the machine.

            Args:
                theme: A `Theme`. Its mode (dark or light) sets the look of the bar.
                bar_thickness: The height of the bar, in pixels. The default is 44.
                extra_settings: Other Noctalia settings, as a dict of dicts: {"shell": {"font": "Inter"}}. Python does not check the names.
            """
            ...

    def emit(self) -> Iterator[Model]:
        settings: dict = {}
        if self.bar_thickness is not None:
            settings["bar"] = {"main": {"thickness": self.bar_thickness}}
        if self.theme is not None:
            settings["theme"] = {"mode": self.theme.mode}
        yield HmNoctalia(enable=Default(True), package=Default(None), settings=_merge(settings, self.extra_settings))  # type: ignore[arg-type]


class Foot(Component):
    """Settings for the foot terminal. Home Manager writes `foot.ini`."""

    theme: Theme | None = None
    """A `Theme`. It gives the font, the size, and the colours that you do not set here."""
    font: FontName | None = None
    """The font, for example "monospace" or "JetBrains Mono"."""
    size: FontSize | None = None
    """The font size, in points."""
    colours: Colours | None = None
    """The background and text colours."""

    if TYPE_CHECKING:

        def __init__(
            self,
            *,
            theme: Theme | None = None,
            font: FontName | None = None,
            size: FontSize | None = None,
            colours: Colours | None = None,
        ) -> None:
            """Settings for the foot terminal. Home Manager writes `foot.ini`.

            Args:
                theme: A `Theme`. It gives the font, the size, and the colours that you do not set here.
                font: The font, for example "monospace" or "JetBrains Mono".
                size: The font size, in points.
                colours: The background and text colours.
            """
            ...

    def emit(self) -> Iterator[Model]:
        font = self.font or (self.theme.font if self.theme else None)
        size = self.size or (self.theme.font_size if self.theme else None)
        colours = self.colours or (self.theme.colours if self.theme else None)
        settings: dict[str, dict[str, FootValue]] = {}
        if font or size:
            settings["main"] = {"font": f"{font or 'monospace'}:size={size or 11}"}
        if colours:
            settings["colors-dark"] = {"background": colours.background, "foreground": colours.foreground}
        # HM installs foot (the option has no null). It is the same package as the foot of `Desktop()`.
        yield HmFoot(enable=Default(True), settings=settings)
