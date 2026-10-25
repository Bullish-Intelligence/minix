"""The desktop components: `Hyprland`, `Noctalia`, `Foot`, and `Theme` (009-D25). No Nix runs here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_config_compile import IMPORT, clean_imports, fail, run  # noqa: F401

from minix_config import Bind, Colours, Foot, Home, Hyprland, Noctalia, Theme, pkgs
from minix_config._desktop import BASE_LUA, BASIX_DEFAULT

DEFAULT = {"__nixdantic_ref__": {"kind": "priority", "level": 1000}}


def fragments(home: Home) -> list[dict[str, Any]]:
    compiled: dict[str, Any] = dict(home.compile())
    return compiled["fragments"]


def hyprland_value(home: Home) -> dict[str, Any]:
    return fragments(home)[0]["value"]["hyprland"]


def lua_of(home: Home) -> str:
    """The `extraConfig` text. With no store reference it is one plain string."""
    ref = hyprland_value(home)["extraConfig"]
    if isinstance(ref, str):
        return ref
    parts = ref["__nixdantic_ref__"]["parts"]
    return "".join(p if isinstance(p, str) else "<ref>" for p in parts)


def marked(value: Any) -> Any:
    """The value inside a `Default(...)` marker; fail when the marker is not a priority of 1000."""
    mark = value["__nixdantic_ref__"]
    assert (mark["kind"], mark["level"]) == ("priority", 1000), mark
    return mark["value"]


# --- Hyprland ---------------------------------------------------------------------------------------------------
def test_hyprland_loads_the_basix_default_only_when_the_file_exists() -> None:
    lua = lua_of(Home(user="a", programs=[Hyprland()]))
    head, _, rest = lua.partition("else\n")
    assert f'local basix_default = "{BASIX_DEFAULT}"' in head
    assert 'io.open(basix_default, "r")' in head
    assert "if default_file then" in head and "dofile(basix_default)" in head
    assert rest.endswith("end\n")  # the base of minix sits in the else branch, and nothing follows with no settings


def test_hyprland_ships_the_base_of_minix() -> None:
    lua = lua_of(Home(user="a", programs=[Hyprland()]))
    for line in BASE_LUA.splitlines():
        assert f"    {line}".rstrip() in lua
    assert "nix/store" not in BASE_LUA
    assert "firefox" not in BASE_LUA and "noctalia" not in BASE_LUA  # the base names no program of the machine but foot


def test_hyprland_with_no_settings_adds_nothing_after_the_default_look() -> None:
    lua = lua_of(Home(user="a", programs=[Hyprland()]))
    assert lua.endswith("end\n")
    assert 'hl.monitor({ output = "DP' not in lua


def test_hyprland_settings_come_after_the_default_look() -> None:
    home = Home(
        user="a",
        programs=[
            Hyprland(
                gaps_in=1,
                gaps_out=40,
                border_size=4,
                active_border="ff8800",
                monitors=["DP-1,1920x1080@60,0x0,1", ",preferred,auto,auto"],
                extra_lua="hl.config({ misc = { vfr = true } })",
            )
        ],
    )
    lua = lua_of(home)
    tail = lua.split("end\nhl.config", 1)[1]
    assert tail.splitlines() == [
        '({ general = { gaps_in = 1, gaps_out = 40, border_size = 4, col = { active_border = "rgb(ff8800)" } } })',
        'hl.monitor({ output = "DP-1", mode = "1920x1080@60", position = "0x0", scale = 1 })',
        'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })',
        "hl.config({ misc = { vfr = true } })",
    ]


def test_the_theme_sets_the_border_and_an_explicit_colour_wins() -> None:
    theme = Theme(accent="33ccff")
    assert "rgb(33ccff)" in lua_of(Home(user="a", programs=[Hyprland(theme=theme)]))
    both = lua_of(Home(user="a", programs=[Hyprland(theme=theme, active_border="ff5555")]))
    assert "rgb(ff5555)" in both and "33ccff" not in both


def test_a_bind_runs_a_store_program_through_a_reference() -> None:
    bind = Bind("SUPER", "T", program=pkgs.foot, args="-T minix-terminal")
    ref = hyprland_value(Home(user="a", programs=[Hyprland(binds=[bind])]))["extraConfig"]["__nixdantic_ref__"]
    parts = ref["parts"]
    assert any(isinstance(p, dict) and p["__nixdantic_ref__"].get("attr") for p in parts)
    text = "".join(p for p in parts if isinstance(p, str))
    assert 'hl.bind("SUPER + T", hl.dsp.exec_cmd("' in text
    assert '-T minix-terminal")' in text


def test_hyprland_values_are_defaults_so_a_learner_option_wins() -> None:
    value = hyprland_value(Home(user="a", programs=[Hyprland()]))
    assert marked(value["enable"]) is True
    assert marked(value["package"]) is None
    assert marked(value["portalPackage"]) is None
    assert marked(value["configType"]) == "lua"
    assert marked(value["systemd"]["enable"]) is False


# --- Noctalia and Foot ------------------------------------------------------------------------------------------
def test_noctalia_writes_settings_and_installs_nothing() -> None:
    home = Home(user="a", programs=[Noctalia(theme=Theme(mode="light"), bar_thickness=28)])
    value = fragments(home)[0]["value"]
    assert marked(value["enable"]) is True
    assert marked(value["package"]) is None
    assert value["settings"] == {"bar": {"main": {"thickness": 28}}, "theme": {"mode": "light"}}


def test_noctalia_extra_settings_merge_and_win() -> None:
    home = Home(
        user="a",
        programs=[
            Noctalia(
                theme=Theme(),
                bar_thickness=28,
                extra_settings={"bar": {"main": {"thickness": 30, "position": "top"}}, "shell": {"font": "Inter"}},
            )
        ],
    )
    assert fragments(home)[0]["value"]["settings"] == {
        "bar": {"main": {"thickness": 30, "position": "top"}},
        "theme": {"mode": "dark"},
        "shell": {"font": "Inter"},
    }


def test_noctalia_with_no_settings_writes_none() -> None:
    assert fragments(Home(user="a", programs=[Noctalia()]))[0]["value"]["settings"] == {}


def test_foot_takes_font_size_and_colours_from_the_theme() -> None:
    theme = Theme(colours=Colours(background="3b1e2e", foreground="ffffff"), font="JetBrains Mono", font_size=13)
    value = fragments(Home(user="a", programs=[Foot(theme=theme)]))[0]["value"]
    assert marked(value["enable"]) is True
    assert value["settings"] == {
        "main": {"font": "JetBrains Mono:size=13"},
        "colors-dark": {"background": "3b1e2e", "foreground": "ffffff"},
    }


def test_foot_explicit_fields_beat_the_theme() -> None:
    theme = Theme(font="monospace", font_size=13)
    foot = Foot(theme=theme, font="Inter", size=9, colours=Colours(background="000000", foreground="ffffff"))
    settings = fragments(Home(user="a", programs=[foot]))[0]["value"]["settings"]
    assert settings["main"] == {"font": "Inter:size=9"}
    assert settings["colors-dark"] == {"background": "000000", "foreground": "ffffff"}


def test_foot_with_no_settings_writes_none() -> None:
    assert fragments(Home(user="a", programs=[Foot()]))[0]["value"]["settings"] == {}


def test_one_theme_serves_all_three() -> None:
    theme = Theme(accent="ff5555", colours=Colours(background="3b1e2e"), mode="light", font_size=14)
    home = Home(user="a", programs=[Hyprland(theme=theme), Noctalia(theme=theme), Foot(theme=theme)])
    assert [f["source"].rsplit(" ", 1)[1] for f in fragments(home)] == ["(Hyprland)", "(Noctalia)", "(Foot)"]
    assert "rgb(ff5555)" in lua_of(home)
    assert fragments(home)[1]["value"]["settings"]["theme"] == {"mode": "light"}
    assert fragments(home)[2]["value"]["settings"]["main"] == {"font": "monospace:size=14"}


# --- the error lines (S18 cases c01 to c08) ---------------------------------------------------------------------
def err(tmp_path: Path, capsys: pytest.CaptureFixture[str], programs: str) -> str:
    return fail(tmp_path, IMPORT + f'home = Home(user="learner", programs=[{programs}])\n', capsys)


def test_a_colour_with_a_hash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert err(tmp_path, capsys, 'Hyprland(active_border="#ff8800")') == (
        "home.py:2:62: Hyprland.active_border must be written without '#': write 'ff8800', not '#ff8800', "
        'found "#ff8800".'
    )


def test_a_colour_of_three_digits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert err(tmp_path, capsys, 'Theme(accent="f80")') == (
        "home.py:2:52: Theme.accent must have six hex digits, rrggbb. "
        "Three digits are not enough: write 'ff8800', not 'f80', found \"f80\"."
    )


def test_a_bind_key_of_two_letters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = err(tmp_path, capsys, 'Hyprland(binds=[Bind("SUPER", "Tx", program=pkgs.foot)])')
    assert text.startswith("home.py:2:")
    assert "Bind.key must be one key: a letter or digit (T, 5), F1 to F24" in text
    assert text.endswith('A bind has one key; write the modifiers in `mods`, found "Tx".')


def test_a_monitor_of_three_parts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = err(tmp_path, capsys, 'Hyprland(monitors=["DP-1,1920x1080@60,0x0"])')
    assert "Hyprland.monitors[0] must have four parts, separated by commas: NAME,MODE,POSITION,SCALE." in text
    assert 'Example: DP-1,1920x1080@60,0x0,1. Found 3 part(s), found "DP-1,1920x1080@60,0x0".' in text


def test_a_monitor_with_a_bad_scale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = err(tmp_path, capsys, 'Hyprland(monitors=["DP-1,1920x1080@60,0x0,big"])')
    assert "Hyprland.monitors[0] must read NAME,MODE,POSITION,SCALE." in text
    assert "Check the part 'big', found" in text


def test_a_misspelled_setting(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = err(tmp_path, capsys, "Hyprland(gap_out=40)")
    assert "Hyprland.gap_out is not a setting. Did you mean `gaps_out`?" in text
    assert "The settings are: theme, gaps_in, gaps_out, border_size, active_border, monitors, binds, extra_lua." in text


def test_a_value_out_of_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert err(tmp_path, capsys, "Hyprland(gaps_out=400)").endswith(
        "Hyprland.gaps_out must be a whole number from 0 to 200 (pixels), found 400."
    )
    assert err(tmp_path, capsys, "Noctalia(bar_thickness=5)").endswith(
        "Noctalia.bar_thickness must be a whole number from 16 to 120 (pixels), found 5."
    )


def test_a_bad_modifier(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = err(tmp_path, capsys, 'Hyprland(binds=[Bind("WIN", "T", program=pkgs.foot)])')
    assert "Bind.mods must hold only SUPER, SHIFT, CTRL, or ALT, joined by '+' (for example SUPER+SHIFT)" in text


def test_a_theme_that_is_not_a_theme(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert err(tmp_path, capsys, 'Foot(theme="dark")').endswith('Foot.theme must be a Theme, found "dark".')


def test_extra_lua_is_not_checked_by_python(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """S18 case c09: a wrong call in `extra_lua` compiles. `minix apply` finds it with `Hyprland --verify-config`."""
    code, text, data = run(
        tmp_path, IMPORT + 'home = Home(user="a", programs=[Hyprland(extra_lua="hl.bnd(1)")])\n', capsys
    )
    assert (code, text) == (0, "")
    assert data is not None
