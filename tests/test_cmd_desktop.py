"""`minix apply` with the desktop files: the missing-program line and `Hyprland --verify-config` (009-D25, L28)."""

from pathlib import Path

import pytest
from pynix_engine import Result
from test_cmd_support import Setup, initialized

from minix import commands

FILES = {
    "Hyprland": "home-files/.config/hypr/hyprland.lua",
    "Noctalia": "home-files/.config/noctalia/config.toml",
}
CONFIG_OK = "======== Config parsing result:\n\nconfig ok\n"
CONFIG_BAD = (
    "\x1b[1;32mDEBUG \x1b[0m]: [cfg] Config is lua, loading lua mgr\n\n\n"
    "======== Config parsing result:\n\n"
    "{file}:2: attempt to call a nil value (field 'bnd')\nstack traceback:\n\t{file}:2: in main chunk\n"
)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return initialized(tmp_path)


def build_with(setup: Setup, *programs: str) -> Path:
    """The home generation that the next build gives: it holds the config files of the named programs."""
    built = setup.world.store(setup.world.next_home)
    for name in programs:
        path = built / FILES[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- config\n")
    return built


def put_on_path(setup: Setup, *commands_: str) -> Path:
    """Put the named programs on the PATH of the run: an empty executable file for each."""
    folder = setup.world.tmp / "bin"
    folder.mkdir(exist_ok=True)
    for name in commands_:
        (folder / name).write_text("#!/bin/sh\n")
        (folder / name).chmod(0o755)
    setup.ctx.env = {**setup.ctx.env, "PATH": str(folder)}
    return folder


def verify_calls(setup: Setup) -> list[tuple[str, ...]]:
    return [call for call in setup.fake.calls if call[:1] == ("env",)]


def run_apply(setup: Setup) -> int:
    return commands.apply(setup.ctx)


# --- the missing-program line ------------------------------------------------------------------------------------
def test_a_machine_without_hyprland_gets_one_plain_line(setup: Setup) -> None:
    build_with(setup, "Hyprland")
    assert run_apply(setup) == 0, setup.lines
    assert setup.lines == [
        "checked: home.py",
        "building the home",
        "built the home",
        "Hyprland is not on this machine",
        "checking the units for 8 s",
        "applied: the home runs, and its units are healthy",
        "saved: home.json",
    ]
    assert not verify_calls(setup)  # no program, no check


def test_two_missing_programs_share_the_line(setup: Setup) -> None:
    build_with(setup, "Hyprland", "Noctalia")
    assert run_apply(setup) == 0
    assert "Hyprland and Noctalia are not on this machine" in setup.lines
    assert sum("not on this machine" in line for line in setup.lines) == 1


def test_noctalia_alone_is_named(setup: Setup) -> None:
    build_with(setup, "Noctalia")
    assert run_apply(setup) == 0
    assert "Noctalia is not on this machine" in setup.lines


def test_another_host_names_nothing_of_basix(setup: Setup) -> None:
    build_with(setup, "Hyprland", "Noctalia")
    put_on_path(setup, "git")
    assert run_apply(setup) == 0
    text = "\n".join(setup.lines).lower()
    assert "basix" not in text and "desktop()" not in text and "system.py" not in text


def test_a_basix_machine_gets_the_way_to_add_the_program(setup: Setup) -> None:
    build_with(setup, "Hyprland")
    put_on_path(setup, "basix")
    assert run_apply(setup) == 0
    assert "Hyprland is not on this machine; add Desktop() to system.py" in setup.lines


def test_a_basix_machine_with_two_missing_programs(setup: Setup) -> None:
    build_with(setup, "Hyprland", "Noctalia")
    put_on_path(setup, "basix")
    assert run_apply(setup) == 0
    assert "Hyprland and Noctalia are not on this machine; add Desktop() to system.py" in setup.lines


def test_only_the_missing_program_is_named(setup: Setup) -> None:
    build_with(setup, "Hyprland", "Noctalia")
    put_on_path(setup, "Hyprland")
    assert run_apply(setup) == 0
    assert "Noctalia is not on this machine" in setup.lines
    assert not any(line.startswith("Hyprland") for line in setup.lines)


def test_a_home_with_no_desktop_file_says_nothing(setup: Setup) -> None:
    put_on_path(setup, "Hyprland")
    assert run_apply(setup) == 0
    assert not any("machine" in line for line in setup.lines)
    assert not verify_calls(setup)


# --- Hyprland --verify-config -------------------------------------------------------------------------------------
def test_apply_verifies_the_built_file_before_the_switch(setup: Setup) -> None:
    built = build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    setup.fake.on(("env",), Result(0, CONFIG_OK))
    assert run_apply(setup) == 0, setup.lines
    (call,) = verify_calls(setup)
    assert call[0] == "env" and call[1].startswith("XDG_RUNTIME_DIR=")
    assert call[2:] == ("Hyprland", "--verify-config", "-c", str(built / FILES["Hyprland"]))
    checked, switched = setup.fake.index(*call), setup.fake.index(str(built / "activate"))
    assert checked is not None and switched is not None
    assert checked < switched
    assert not any("machine" in line for line in setup.lines)


def test_the_check_runs_in_a_private_folder_that_it_removes(setup: Setup) -> None:
    build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    setup.fake.on(("env",), Result(0, CONFIG_OK))
    assert run_apply(setup) == 0
    runtime = Path(verify_calls(setup)[0][1].removeprefix("XDG_RUNTIME_DIR="))
    assert runtime.name.startswith("minix-hyprland-")
    assert not runtime.exists()


def test_a_file_that_hyprland_refuses_stops_with_one_line(setup: Setup) -> None:
    built = build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    file = str(built / FILES["Hyprland"])
    setup.fake.on(("env",), Result(1, CONFIG_BAD.format(file=file)))
    assert run_apply(setup) == 2
    assert setup.lines[-1] == (
        "Hyprland does not accept the config: hyprland.lua:2: attempt to call a nil value (field 'bnd'). "
        "Nothing was switched or saved. The home did not change."
    )
    assert "\n" not in setup.lines[-1]
    assert not any(label.startswith("activate") for label in setup.trace())
    assert setup.commits() == ["minix init"]


def test_the_refusal_shows_no_store_path_and_no_color_code(setup: Setup) -> None:
    built = build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    setup.fake.on(("env",), Result(1, CONFIG_BAD.format(file=str(built / FILES["Hyprland"]))))
    assert run_apply(setup) == 2
    text = "\n".join(setup.lines)
    assert "/nix/store" not in text and str(setup.world.tmp) not in text and "\x1b" not in text


def test_an_unknown_key_is_named(setup: Setup) -> None:
    built = build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    out = f"======== Config parsing result:\n\n{built / FILES['Hyprland']}:1: unknown config key 'general.gap_out'\n"
    setup.fake.on(("env",), Result(1, out))
    assert run_apply(setup) == 2
    assert "hyprland.lua:1: unknown config key 'general.gap_out'." in setup.lines[-1]


def test_a_run_that_gives_no_verdict_does_not_stop_the_apply(setup: Setup) -> None:
    build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    setup.fake.on(("env",), Result(134, "terminate called after throwing an instance\n"))
    assert run_apply(setup) == 0, setup.lines
    assert "warning: Hyprland could not check the config (exit 134), so minix skipped the check" in setup.lines
    assert setup.commits()[0] == "apply: home.json"


def test_noctalia_alone_runs_no_hyprland_check(setup: Setup) -> None:
    build_with(setup, "Noctalia")
    put_on_path(setup, "Hyprland", "noctalia")
    assert run_apply(setup) == 0
    assert not verify_calls(setup)
    assert not any("machine" in line for line in setup.lines)


def test_a_file_name_that_lua_cut_short_is_named_hyprland_lua(setup: Setup) -> None:
    """Lua writes a long file name as `...TAIL` in its messages. The line names the file, not the store."""
    build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    out = "======== Config parsing result:\n\n...e/home-files/.config/hypr/hyprland.lua:1: '}' expected near <eof>\n"
    setup.fake.on(("env",), Result(1, out))
    assert run_apply(setup) == 2
    assert setup.lines[-1].startswith("Hyprland does not accept the config: hyprland.lua:1: '}' expected near <eof>. ")


def test_a_path_inside_the_message_stays(setup: Setup) -> None:
    build_with(setup, "Hyprland")
    put_on_path(setup, "Hyprland")
    out = "======== Config parsing result:\n\nhyprland.lua:3: cannot open /etc/xdg/hypr/hyprland.lua\n"
    setup.fake.on(("env",), Result(1, out))
    assert run_apply(setup) == 2
    assert "cannot open /etc/xdg/hypr/hyprland.lua." in setup.lines[-1]
