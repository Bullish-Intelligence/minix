"""The typed unit fields of `Service` and the `Step` object: the wire that each field writes, and the plain errors."""

from __future__ import annotations

from typing import Any

import pytest
from nixdantic import ExeOf, supplied
from pydantic import ValidationError
from pynix_native import InputProg, NixpkgsProg

from minix_config import Bind, Native, Service, Step, pkgs
from minix_config import _base as base

BASE = {"Description": "minix service web"}


def wire(service: Service) -> dict[str, Any]:
    """The one unit of a service, as the wire that Home Manager reads."""
    emitted = list(service.emit())
    assert len(emitted) == 1
    wire: Any = supplied(emitted[0])
    return wire["services"][service.name]


def make(**fields: Any) -> Service:
    return Service("web", program=pkgs.python3, **fields)


def test_defaults_write_no_new_key() -> None:
    web = wire(make())
    assert set(web) == {"Unit", "Service", "Install"}
    assert web["Unit"] == BASE
    assert set(web["Service"]) == {"ExecStart", "Restart"}
    assert web["Install"] == {"WantedBy": ["default.target"]}


def test_default_values_written_by_hand_write_no_key() -> None:
    explicit = make(
        type="simple",
        kill_mode="control-group",
        working_dir="",
        only_if_exists=[],
        only_if_virtualization="",
        timeout_start="",
        start_limit_interval="",
        start_limit_burst=None,
        pre_start=[],
    )
    assert wire(explicit) == wire(make())


@pytest.mark.parametrize(
    ("fields", "section", "key", "value"),
    [
        ({"type": "exec"}, "Service", "Type", "exec"),
        ({"type": "oneshot"}, "Service", "Type", "oneshot"),
        ({"working_dir": "/var/lib/app"}, "Service", "WorkingDirectory", "/var/lib/app"),
        ({"working_dir": "~"}, "Service", "WorkingDirectory", "%h"),
        ({"working_dir": "~/work"}, "Service", "WorkingDirectory", "%h/work"),
        ({"working_dir": "~/50%"}, "Service", "WorkingDirectory", "%h/50%%"),
        ({"timeout_start": "15min"}, "Service", "TimeoutStartSec", "15min"),
        ({"timeout_start": "infinity"}, "Service", "TimeoutStartSec", "infinity"),
        ({"kill_mode": "mixed"}, "Service", "KillMode", "mixed"),
        ({"kill_mode": "process"}, "Service", "KillMode", "process"),
        ({"no_restart_on_exit": [75, 76]}, "Service", "RestartPreventExitStatus", "75 76"),
        ({"no_restart_on_exit": [0, 255]}, "Service", "RestartPreventExitStatus", "0 255"),
        ({"only_if_exists": ["/a", "~/b", "!~/c d"]}, "Unit", "ConditionPathExists", ["/a", "%h/b", "!%h/c d"]),
        ({"only_if_exists": ["/100%"]}, "Unit", "ConditionPathExists", ["/100%%"]),
        ({"only_if_glob": ["/dev/ttyUSB*", "!~/x/*"]}, "Unit", "ConditionPathExistsGlob", ["/dev/ttyUSB*", "!%h/x/*"]),
        ({"only_if_virtualization": "no"}, "Unit", "ConditionVirtualization", "no"),
        ({"only_if_virtualization": "!container"}, "Unit", "ConditionVirtualization", "!container"),
        ({"after": ["db"]}, "Unit", "After", ["db.service"]),
        ({"wants": ["db", "x.timer"]}, "Unit", "Wants", ["db.service", "x.timer"]),
        ({"requires": ["db", "x.socket"]}, "Unit", "Requires", ["db.service", "x.socket"]),
        ({"description": "A web server"}, "Unit", "Description", "A web server"),
        ({"description": "100% sure"}, "Unit", "Description", "100%% sure"),
        ({"environment_file": "/etc/app/env"}, "Service", "EnvironmentFile", "/etc/app/env"),
        ({"environment_file": "~/.config/app/env"}, "Service", "EnvironmentFile", "%h/.config/app/env"),
        ({"environment_file": "-~/.config/app/env"}, "Service", "EnvironmentFile", "-%h/.config/app/env"),
        ({"environment_file": "-/a/50%"}, "Service", "EnvironmentFile", "-/a/50%%"),
        ({"start_limit_interval": "60s"}, "Unit", "StartLimitIntervalSec", "60s"),
        ({"start_limit_burst": 5}, "Unit", "StartLimitBurst", 5),
    ],
)
def test_each_field_writes_its_key_in_its_section(fields: dict[str, Any], section: str, key: str, value: Any) -> None:
    web = wire(make(**fields))
    other = "Service" if section == "Unit" else "Unit"
    assert web[section][key] == value
    assert key not in web.get(other, {})
    assert set(web[section]) - {key} <= {"Description", "ExecStart", "Restart"}  # one new key and nothing else


@pytest.mark.parametrize("suffix", [".service", ".target", ".socket", ".timer", ".path", ".mount", ".slice", ".scope"])
def test_a_unit_name_with_a_suffix_stays(suffix: str) -> None:
    assert wire(make(after=[f"x{suffix}"], wants=[f"y{suffix}"]))["Unit"]["After"] == [f"x{suffix}"]


def test_a_repeated_unit_joins_once() -> None:
    web = wire(make(after=["db", "db.service"], wants=["db", "db"]))
    assert web["Unit"]["After"] == ["db.service"]
    assert web["Unit"]["Wants"] == ["db.service"]


def test_desktop_and_after_join_without_a_duplicate() -> None:
    web = wire(make(starts_with="desktop", after=["db", "graphical-session.target"]))
    assert web["Unit"]["After"] == ["graphical-session.target", "db.service"]
    assert web["Unit"]["PartOf"] == ["graphical-session.target"]
    assert web["Install"] == {"WantedBy": ["graphical-session.target"]}


def test_after_alone_leaves_the_default_target_out_of_the_unit() -> None:
    web = wire(make(after=["db"]))
    assert web["Unit"]["After"] == ["db.service"]
    assert "PartOf" not in web["Unit"]


def parts(text: Any) -> list[Any]:
    """The parts of a `Text` value on the wire."""
    return text["__nixdantic_ref__"]["parts"]


def test_steps_become_exec_start_pre_lines_in_order() -> None:
    web = wire(
        make(
            pre_start=[
                Step(program=pkgs.curl, args="-sf 'http://a b' --retry 3"),
                Step(program=pkgs.coreutils, file="sleep", args="2"),
                Step(program=pkgs.curl),
            ]
        )
    )
    first, second, third = web["Service"]["ExecStartPre"]
    assert parts(first) == [
        {"__nixdantic_ref__": {"kind": "exe", "attr": "curl"}},
        " -sf",
        ' "http://a b"',
        " --retry",
        " 3",
    ]
    assert parts(second) == [{"__nixdantic_ref__": {"kind": "package", "attr": "coreutils"}}, "/bin/sleep", " 2"]
    assert parts(third) == [{"__nixdantic_ref__": {"kind": "exe", "attr": "curl"}}]


def test_a_step_quotes_the_percent_sign_and_the_dollar_sign() -> None:
    web = wire(make(pre_start=[Step(program=pkgs.curl, args="50% $HOME")]))
    assert parts(web["Service"]["ExecStartPre"][0])[1:] == [' "50%%"', ' "$$HOME"']


HOME_WORDS = [
    # (args, the words after the program in ExecStart, the words after the program in a shell)
    ("~", [" %h"], [' "$HOME"']),
    ("~/x", [" %h/x"], [' "$HOME"/x']),
    ('"~/a b"', [' "%h/a b"'], [" \"$HOME\"'/a b'"]),
    ("~/100%", [' "%h/100%%"'], [' "$HOME"/100%']),
    ("'~/$X'", [' "%h/$$X"'], [" \"$HOME\"'/$X'"]),
    ("~/", [" %h/"], [' "$HOME"/']),
    ("--out=~/x", [' "--out=~/x"'], [" '--out=~/x'"]),
    ("a~/b", [' "a~/b"'], [" 'a~/b'"]),
    ("~user", [' "~user"'], [" '~user'"]),
    ("~user/x", [' "~user/x"'], [" '~user/x'"]),
    ("-o ~/x ~/y", [" -o", " %h/x", " %h/y"], [" -o", ' "$HOME"/x', ' "$HOME"/y']),
]


@pytest.mark.parametrize(("args", "systemd", "shell"), HOME_WORDS)
def test_a_word_that_starts_with_tilde_slash_names_the_home_in_exec_start(
    args: str, systemd: list[str], shell: list[str]
) -> None:
    exe = {"__nixdantic_ref__": {"kind": "exe", "attr": "curl"}}
    web = wire(make(args=args, pre_start=[Step(program=pkgs.curl, args=args)]))
    assert parts(web["Service"]["ExecStart"])[1:] == systemd
    assert parts(web["Service"]["ExecStartPre"][0]) == [exe, *systemd]


@pytest.mark.parametrize(("args", "systemd", "shell"), HOME_WORDS)
def test_a_word_that_starts_with_tilde_slash_names_the_home_in_a_bind(
    args: str, systemd: list[str], shell: list[str]
) -> None:
    text = Bind("SUPER", "T", program=pkgs.foot, args=args).command_text()
    assert list(text.parts)[1:] == shell


def test_a_step_needs_a_main_program_or_a_file() -> None:
    with pytest.raises(ValidationError, match=r'`coreutils` has none. Name the file: file="NAME"'):
        Step(program=pkgs.coreutils)  # ty: ignore[invalid-argument-type]
    assert Step(program=pkgs.coreutils, file="sleep").file == "sleep"


def test_a_step_warns_about_shell_characters() -> None:
    base.WARNINGS.clear()
    Step(program=pkgs.curl, args="a | b")
    Step(program=pkgs.curl, args="a b")
    assert [(field, chars) for _, field, chars in base.WARNINGS] == [("Step.args", "|")]
    base.WARNINGS.clear()


def test_a_step_takes_keywords_only() -> None:
    with pytest.raises(TypeError):
        Step(pkgs.curl)  # ty: ignore[no-matching-overload]


def test_pre_start_refuses_a_plain_string() -> None:
    with pytest.raises(ValidationError, match="a Step object"):
        make(pre_start=["curl -sf x"])


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"working_dir": "work"}, 'working_dir\n  must be an absolute path or start with ~/, found "work"'),
        ({"working_dir": "/a\nb"}, "must be on one line, and this value holds a line break"),
        (
            {"only_if_exists": ["/a", "b/c"]},
            'only_if_exists.1\n  must be an absolute path or start with ~/, found "b/c"',
        ),
        ({"only_if_exists": ["/a\r/b"]}, "must be on one line, and this value holds a line break"),
        ({"only_if_exists": ["!"]}, 'must be an absolute path or start with ~/, found ""'),
        ({"only_if_glob": ["tty*"]}, 'only_if_glob.0\n  must be an absolute path or start with ~/, found "tty*"'),
        ({"only_if_virtualization": "vm\nkvm"}, "must be a kind of virtualization such as"),
        ({"only_if_virtualization": "!"}, "must be a kind of virtualization such as"),
        ({"after": ["a b"]}, 'after.0\n  must be a unit name such as "db" or "network.target", found "a b"'),
        ({"after": [".service"]}, "must be a unit name such as"),
        ({"wants": ["x\ny"]}, "wants.0\n  must be a unit name such as"),
        ({"requires": ["a b"]}, 'requires.0\n  must be a unit name such as "db" or "network.target", found "a b"'),
        ({"requires": "db"}, "requires\n  must be a list"),
        ({"description": "a\nb"}, "description\n  must be on one line, and this value holds a line break"),
        ({"description": 5}, "description\n  must be one line of text"),
        ({"environment_file": "env"}, 'environment_file\n  must be an absolute path or start with ~/, found "env"'),
        ({"environment_file": "-env"}, 'must be an absolute path or start with ~/, found "env"'),
        ({"environment_file": "-"}, 'must be an absolute path or start with ~/, found ""'),
        ({"environment_file": "/a\nb"}, "must be on one line, and this value holds a line break"),
        ({"environment_file": 5}, 'environment_file\n  must be an absolute path or a path that starts with "~/"'),
        ({"timeout_start": "soon"}, 'must be a time such as "90s" or "15min", or "infinity"'),
        ({"start_limit_interval": "infinity"}, 'must be a time such as "10s", "500ms", or "1min"'),
        ({"start_limit_burst": 0}, "must be a whole number of 1 or more"),
        ({"start_limit_burst": "5"}, "must be a whole number of 1 or more"),
        ({"no_restart_on_exit": [256]}, "no_restart_on_exit.0\n  must be an exit status, a whole number from 0 to 255"),
        ({"no_restart_on_exit": [-1]}, "must be an exit status, a whole number from 0 to 255"),
        ({"type": "service"}, "type\n  must be one of"),
        ({"kill_mode": "none"}, "kill_mode\n  must be one of"),
    ],
)
def test_a_bad_value_gives_one_plain_line(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        make(**fields)
    assert caught.value.error_count() == 1
    assert message in str(caught.value)


def test_requires_does_not_set_the_order() -> None:
    web = wire(make(requires=["db"]))
    assert "After" not in web["Unit"]


def test_a_repeated_required_unit_joins_once() -> None:
    assert wire(make(requires=["db", "db.service"]))["Unit"]["Requires"] == ["db.service"]


def test_the_default_description_names_the_service() -> None:
    assert wire(make())["Unit"]["Description"] == "minix service web"
    assert wire(make(description=""))["Unit"]["Description"] == "minix service web"


NATIVE = {"__nixdantic_ref__": {"kind": "native", "file": "pkg.nix", "attr": ["daemon"]}}


def test_a_service_runs_a_native_package_by_its_file() -> None:
    service = Service("d", program=Native("pkg.nix", ["daemon"]), file="d", args="--a ~/x")
    assert parts(wire(service)["Service"]["ExecStart"]) == [NATIVE, "/bin/d", " --a", " %h/x"]


def test_a_step_runs_a_native_package_by_its_file() -> None:
    step = Step(program=Native("pkg.nix", ["daemon"]), file="d", args="-q")
    assert list(step.command_text().parts) == [Native("pkg.nix", ["daemon"]), "/bin/d", " -q"]
    web = wire(make(pre_start=[step]))
    assert parts(web["Service"]["ExecStartPre"][0]) == [NATIVE, "/bin/d", " -q"]


@pytest.mark.parametrize("make_one", [Service, Step])
def test_a_native_package_without_a_file_gives_one_plain_line(make_one: Any) -> None:
    name = ["d"] if make_one is Service else []
    with pytest.raises(ValidationError) as caught:
        make_one(*name, program=Native("pkg.nix", ["daemon"]))
    assert caught.value.error_count() == 1
    assert 'must name the file: file="NAME". The Native package from `pkg.nix` has no known main program' in str(
        caught.value
    )


@pytest.mark.parametrize("make_one", [Service, Step])
def test_a_wrong_program_names_both_forms(make_one: Any) -> None:
    name = ["d"] if make_one is Service else []
    with pytest.raises(ValidationError) as caught:
        make_one(*name, program="curl", file="x")
    assert caught.value.error_count() == 1
    assert 'must be a package such as pkgs.NAME, or a Native package such as Native("pkg.nix", ["attr"])' in str(
        caught.value
    )


def test_a_bind_still_takes_a_package_only() -> None:
    with pytest.raises(ValidationError, match="must be a Pkg"):
        Bind("SUPER", "T", program=Native("pkg.nix", ["daemon"]))  # ty: ignore[invalid-argument-type]


def test_the_shell_warning_does_not_fail_on_a_native_package() -> None:
    base.WARNINGS.clear()
    Service("d", program=Native("pkg.nix", ["daemon"]), file="d", args="a | b")
    Step(program=Native("pkg.nix", ["daemon"]), file="d", args="a | b")
    Service("d", program=Native("pkg.nix", ["daemon"]), file="bash", args="-c 'a | b'")
    assert [(field, chars) for _, field, chars in base.WARNINGS] == [("Service.args", "|"), ("Step.args", "|")]
    base.WARNINGS.clear()


def test_a_router_service_writes_the_whole_unit() -> None:
    """A unit like a real router service: conditions, start limits, a type, two steps, and exit codes."""
    service = Service(
        "router",
        program=pkgs.python3,
        args="-m http.server 8080",
        type="exec",
        working_dir="~/router",
        only_if_exists=["~/.config/router/ready", "!/etc/router/off"],
        only_if_glob=["/dev/ttyUSB*"],
        only_if_virtualization="no",
        after=["db"],
        wants=["db"],
        pre_start=[
            Step(program=pkgs.curl, args="-sf http://localhost:8080/ready --max-time 5"),
            Step(program=pkgs.coreutils, file="sleep", args="2"),
        ],
        timeout_start="15min",
        kill_mode="mixed",
        start_limit_interval="60s",
        start_limit_burst=5,
        no_restart_on_exit=[75, 76],
        restart="always",
        restart_delay="3s",
        environment={"PORT": "8080"},
    )
    web = wire(service)
    exe = {"__nixdantic_ref__": {"kind": "exe", "attr": "python3"}}
    curl = {"__nixdantic_ref__": {"kind": "exe", "attr": "curl"}}
    coreutils = {"__nixdantic_ref__": {"kind": "package", "attr": "coreutils"}}

    def text(*items: Any) -> dict[str, Any]:
        return {"__nixdantic_ref__": {"kind": "text", "parts": list(items)}}

    assert web == {
        "Unit": {
            "Description": "minix service router",
            "After": ["db.service"],
            "Wants": ["db.service"],
            "ConditionPathExists": ["%h/.config/router/ready", "!/etc/router/off"],
            "ConditionPathExistsGlob": ["/dev/ttyUSB*"],
            "ConditionVirtualization": "no",
            "StartLimitIntervalSec": "60s",
            "StartLimitBurst": 5,
        },
        "Service": {
            "ExecStart": text(exe, " -m", " http.server", " 8080"),
            "Environment": ["PORT=8080"],
            "Restart": "always",
            "RestartSec": "3s",
            "Type": "exec",
            "WorkingDirectory": "%h/router",
            "ExecStartPre": [
                text(curl, " -sf", " http://localhost:8080/ready", " --max-time", " 5"),
                text(coreutils, "/bin/sleep", " 2"),
            ],
            "TimeoutStartSec": "15min",
            "KillMode": "mixed",
            "RestartPreventExitStatus": "75 76",
        },
        "Install": {"WantedBy": ["default.target"]},
    }


# --- the package of an input or of a pin keeps its identity (no file=) ----------------------------------
def exe_of(package: dict[str, str]) -> dict[str, Any]:
    return {"__nixdantic_ref__": {"kind": "exe_of", "of": package}}


INPUT = InputProg("nix-demo", "daemon")
INPUT_WIRE = {"kind": "input_package", "attr": "daemon", "input": "nix-demo"}
PIN = NixpkgsProg("old", "daemon")
PIN_WIRE = {"kind": "nixpkgs_package", "attr": "daemon", "nixpkgs": "old"}


@pytest.mark.parametrize(("program", "package"), [(INPUT, INPUT_WIRE), (PIN, PIN_WIRE)])
def test_a_service_runs_the_package_of_an_input_or_a_pin(program: Any, package: dict[str, str]) -> None:
    web = wire(Service("web", program=program, args="-q"))
    assert parts(web["Service"]["ExecStart"]) == [exe_of(package), " -q"]


@pytest.mark.parametrize(("program", "package"), [(INPUT, INPUT_WIRE), (PIN, PIN_WIRE)])
def test_a_step_runs_the_package_of_an_input_or_a_pin(program: Any, package: dict[str, str]) -> None:
    assert list(Step(program=program).command_text().parts) == [ExeOf(program)]
    web = wire(make(pre_start=[Step(program=program, args="-q")]))
    assert parts(web["Service"]["ExecStartPre"][0]) == [exe_of(package), " -q"]


@pytest.mark.parametrize(("program", "package"), [(INPUT, INPUT_WIRE), (PIN, PIN_WIRE)])
def test_a_bind_runs_the_package_of_an_input_or_a_pin(program: Any, package: dict[str, str]) -> None:
    text = supplied(Bind("SUPER", "T", program=program, args="-q").command_text())
    assert text["__nixdantic_ref__"]["parts"][0] == exe_of(package)


def test_a_file_names_the_package_of_an_input_or_a_pin_with_a_path() -> None:
    web = wire(Service("web", program=INPUT, file="daemon-bin"))
    assert parts(web["Service"]["ExecStart"]) == [{"__nixdantic_ref__": INPUT_WIRE}, "/bin/daemon-bin"]


def test_a_plain_package_keeps_the_exe_wire() -> None:
    exe = {"__nixdantic_ref__": {"kind": "exe", "attr": "curl"}}
    assert parts(wire(Service("web", program=pkgs.curl))["Service"]["ExecStart"]) == [exe]
    assert parts(wire(make(pre_start=[Step(program=pkgs.curl)]))["Service"]["ExecStartPre"][0]) == [exe]
    assert supplied(Bind("SUPER", "T", program=pkgs.curl).command_text())["__nixdantic_ref__"]["parts"] == [exe]
