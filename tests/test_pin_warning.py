"""The pin-mismatch warning (021-D3): a Python input that the home and the activated Basix system pin at different revisions.

`apply`, `undo`, and `update` warn once per input, in one plain line. The warning never changes an exit code. A missing or
damaged file of the system gives no line. The activated system is a scratch folder (`Context.active_system`).
"""

import json
from pathlib import Path

import pytest
from test_cmd_support import Setup, initialized, lock_json

from minix import commands, layer

REV = "b" * 40  # the revision that the fake lock gives every input
OTHER = "c" * 40
INPUT: dict[str, object] = {"name": "comps", "url": "github:o/comps", "flake": True, "python": "src"}
HOME_INPUT = 'inputs = [Input("comps", "github:o/comps", python="src")]\n'
WARNING = (
    "warning: the input `comps` is at bbbbbbb in your home, but the system has ccccccc. "
    "The Python code of the two pins can differ. To make them the same, update the side that is behind."
)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    made = initialized(tmp_path)
    made.world.compiled_inputs = [INPUT]
    with (made.ws / "home.py").open("a") as handle:
        handle.write(HOME_INPUT)
    return made


def publish(setup: Setup, rev: str | None = OTHER, url: str = "github:o/comps", name: str = "comps") -> None:
    """Write the file that an activated Basix system holds."""
    pins = [{"name": name, "url": url, "flake": True, "python": "src", "rev": rev, "narHash": "sha256-x"}]
    write(setup, json.dumps({"version": 1, "pins": pins}))


def write(setup: Setup, text: str) -> None:
    path = setup.ctx.active_system / "etc" / "basix" / "python-pins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_a_different_revision_warns_once(setup: Setup) -> None:
    publish(setup)
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if line.startswith("warning: the input")] == [WARNING]
    assert setup.lines.index(WARNING) == setup.lines.index("built the home") + 1  # after the build, before the switch


def test_the_warning_has_no_store_path_and_no_git_text(setup: Setup) -> None:
    publish(setup)
    assert commands.apply(setup.ctx) == 0
    assert "/nix/store" not in WARNING
    assert "git" not in WARNING.lower()


def test_the_same_revision_is_silent(setup: Setup) -> None:
    publish(setup, rev=REV)
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []


def test_the_same_input_under_another_url_form_still_warns(setup: Setup) -> None:
    publish(setup, url="git+https://github.com/O/Comps.git?ref=main", name="other-name")
    assert commands.apply(setup.ctx) == 0, setup.lines
    [line] = [line for line in setup.lines if line.startswith("warning: the input")]
    assert "`comps` (`other-name` in the system)" in line


def test_an_input_that_only_the_system_pins_is_silent(setup: Setup) -> None:
    publish(setup, url="github:o/elsewhere", name="elsewhere")
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []


def test_a_missing_file_is_silent(setup: Setup) -> None:
    assert not setup.ctx.active_system.exists()
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []


@pytest.mark.parametrize("text", ["", "{", "[]", '{"version": 2, "pins": []}', '{"version": 1, "pins": "x"}', "\xff"])
def test_a_damaged_file_is_silent(setup: Setup, text: str) -> None:
    write(setup, text)
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []


def test_undo_warns_for_the_restored_home(setup: Setup) -> None:
    publish(setup)
    assert commands.apply(setup.ctx) == 0
    with (setup.ws / "home.py").open("a") as handle:
        handle.write("# an edit\n")
    setup.lines.clear()
    assert commands.undo(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if line.startswith("warning: the input")] == [WARNING]


def test_a_failing_check_does_not_change_the_exit_code_of_apply(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    publish(setup)

    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(layer, "pin_lines", broken)
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []
    assert "the pin check failed and was skipped" in setup.log()


def test_the_compare_never_raises(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    publish(setup)

    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(layer.pins, "lines", broken)
    assert commands.apply(setup.ctx) == 0, setup.lines
    assert [line for line in setup.lines if "warning" in line] == []


# --- update: no build, so the pins come from home.json and the new lock -----------------------------
@pytest.fixture
def moving(tmp_path: Path) -> Setup:
    made = initialized(tmp_path, update_to=lock_json(inputs=["comps"]))
    (made.ws / "home.json").write_text(json.dumps({"user": "ada", "fragments": [], "inputs": [INPUT]}))
    return made


def test_update_warns_after_the_lock_moves(moving: Setup) -> None:
    publish(moving)
    assert commands.update(moving.ctx) == 0, moving.lines
    assert [line for line in moving.lines if line.startswith("warning: the input")] == [WARNING]
    assert moving.lines.index(WARNING) < moving.lines.index("next: run minix apply to use it")


def test_update_is_silent_for_the_same_revision(moving: Setup) -> None:
    publish(moving, rev=REV)
    assert commands.update(moving.ctx) == 0, moving.lines
    assert [line for line in moving.lines if "warning" in line] == []


def test_update_is_silent_without_the_file(moving: Setup) -> None:
    assert commands.update(moving.ctx) == 0, moving.lines
    assert [line for line in moving.lines if "warning" in line] == []


def test_update_is_silent_without_home_json(moving: Setup) -> None:
    (moving.ws / "home.json").unlink()
    publish(moving)
    assert commands.update(moving.ctx) == 0, moving.lines
    assert [line for line in moving.lines if "warning" in line] == []


def test_a_failing_check_does_not_change_the_exit_code_of_update(
    moving: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish(moving)

    def broken(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(commands, "pin_lines", broken)
    assert commands.update(moving.ctx) == 0, moving.lines
    assert moving.lines[-1] == "next: run minix apply to use it"
    assert [line for line in moving.lines if "warning" in line] == []
