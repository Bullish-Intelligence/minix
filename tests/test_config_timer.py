"""The `Timer` object and the `timer` field of `Service`: the wire that they write, and the plain errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from nixdantic import supplied
from pydantic import ValidationError

from minix_config import Service, Timer, pkgs
from minix_config import compile as compiler


def emitted(service: Service) -> dict[str, Any]:
    """The one Home Manager value of a service, as the wire that Home Manager reads."""
    values = list(service.emit())
    assert len(values) == 1
    wire: Any = supplied(values[0])
    return wire


def make(timer: Timer | None = None, **fields: Any) -> Service:
    return Service("backup", program=pkgs.python3, type="oneshot", timer=timer, **fields)


def test_a_timer_writes_the_timer_unit() -> None:
    wire = emitted(make(Timer("daily", randomized_delay="30min")))
    assert set(wire) == {"services", "timers"}
    assert wire["timers"] == {
        "backup": {
            "Unit": {"Description": "minix timer backup"},
            "Timer": {"OnCalendar": "daily", "Persistent": True, "RandomizedDelaySec": "30min"},
            "Install": {"WantedBy": ["timers.target"]},
        }
    }


def test_the_timer_starts_the_service_so_the_service_has_no_wanted_by() -> None:
    web = emitted(make(Timer("daily")))["services"]["backup"]
    assert "Install" not in web
    assert set(web) == {"Unit", "Service"}
    assert web["Service"]["Type"] == "oneshot"


def test_persistent_false_is_written_as_false() -> None:
    timer = emitted(make(Timer("hourly", persistent=False)))["timers"]["backup"]["Timer"]
    assert timer == {"OnCalendar": "hourly", "Persistent": False}


def test_a_randomized_delay_is_written_only_when_set() -> None:
    assert "RandomizedDelaySec" not in emitted(make(Timer("daily")))["timers"]["backup"]["Timer"]
    assert emitted(make(Timer("daily", randomized_delay="5min")))["timers"]["backup"]["Timer"][
        "RandomizedDelaySec"
    ] == ("5min")


def test_a_service_without_a_timer_writes_the_same_wire_as_before() -> None:
    wire = emitted(Service("web", program=pkgs.python3))
    assert set(wire) == {"services"}
    assert set(wire["services"]) == {"web"}
    web = wire["services"]["web"]
    assert web["Unit"] == {"Description": "minix service web"}
    assert set(web["Service"]) == {"ExecStart", "Restart"}
    assert web["Install"] == {"WantedBy": ["default.target"]}
    assert wire == emitted(Service("web", program=pkgs.python3, timer=None))


def test_a_timer_and_the_desktop_are_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        make(Timer("daily"), starts_with="desktop")
    assert caught.value.error_count() == 1
    assert 'cannot have a timer with starts_with="desktop": the timer starts the service.' in str(caught.value)


def test_a_timer_takes_the_calendar_as_a_positional_argument_only() -> None:
    assert Timer("daily").on_calendar == "daily"
    with pytest.raises(TypeError):
        Timer(on_calendar="daily")  # ty: ignore[positional-only-parameter-as-kwarg]
    with pytest.raises(TypeError):
        Timer("daily", True)  # ty: ignore[too-many-positional-arguments]


@pytest.mark.parametrize(
    ("args", "fields", "message"),
    [
        (("",), {}, 'must be a calendar time such as "daily", and this value is empty'),
        (("  ",), {}, "and this value is empty"),
        (("daily\nhourly",), {}, "must be on one line, and this value holds a line break"),
        (("daily\r",), {}, "must be on one line, and this value holds a line break"),
        (("dai\x00ly",), {}, "holds a control character"),
        (("dai\tly",), {}, "holds a control character"),
        (("daily",), {"randomized_delay": "soon"}, 'must be a time such as "10s", "500ms", or "1min"'),
        (("daily",), {"randomized_delay": "infinity"}, "randomized_delay\n  must be a time such as"),
        (("daily",), {"persistent": "yes"}, "persistent"),
    ],
)
def test_a_bad_timer_gives_one_plain_line(args: tuple[str, ...], fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Timer(*args, **fields)
    assert caught.value.error_count() == 1
    assert message in str(caught.value)


def test_a_timer_object_is_needed() -> None:
    with pytest.raises(ValidationError):
        make("daily")  # ty: ignore[invalid-argument-type]


def test_a_home_with_a_timer_compiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "home.py").write_text(
        "from minix_config import *\n"
        'home = Home(user="a", services=[Service("backup", program=pkgs.python3, type="oneshot", timer=Timer("daily"))])\n'
    )
    code = compiler.main(["minix-compile", str(tmp_path / "home.py"), str(tmp_path / "home.json")])
    assert (code, capsys.readouterr().err) == (0, "")
    (fragment,) = json.loads((tmp_path / "home.json").read_text())["fragments"]
    value = fragment["value"]
    assert value["timers"]["backup"]["Timer"] == {"OnCalendar": "daily", "Persistent": True}
    assert value["timers"]["backup"]["Install"] == {"WantedBy": ["timers.target"]}
    assert "Install" not in value["services"]["backup"]
