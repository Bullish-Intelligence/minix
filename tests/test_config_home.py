"""The items of a home: the values that each component emits, as Home Manager fragments (no Nix runs here)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from nixdantic import supplied
from pydantic import ValidationError

from minix_config import (
    File,
    Files,
    Git,
    Home,
    Neovim,
    Program,
    Service,
    TerminalWorkspace,
    pkgs,
)
from minix_config import __all__ as learner_names


def values(home: Home) -> list[dict[str, Any]]:
    compiled: dict[str, Any] = dict(home.compile())
    return compiled["fragments"]


def paths(home: Home) -> list[str]:
    return [".".join(f["path"]) for f in values(home)]


def test_learner_names_are_the_documented_set() -> None:
    """009-D23: `from minix_config import *` brings the learner names and nothing else."""
    assert sorted(learner_names) == sorted(
        [
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
            "OWN",
            "Package",
            "Program",
            "Service",
            "Step",
            "TerminalWorkspace",
            "Text",
            "Theme",
            "Timer",
            "Zsh",
            "hm",
            "pkgs",
        ]
    )


def test_home_fields() -> None:
    assert list(Home.model_fields) == [
        "user",
        "state_version",
        "programs",
        "files",
        "services",
        "options",
        "native_modules",
        "inputs",
        "values",
    ]


def test_compile_keeps_the_order_and_the_learner_lines() -> None:
    home = Home(user="a", programs=[Program(pkgs.ripgrep)], files=[File(".config/x", "t")])
    sources = [f["source"] for f in values(home)]
    assert [s.rsplit(" ", 1)[1] for s in sources] == ["(Program)", "(File)"]
    assert all(s.startswith("test_config_home.py:") for s in sources)


def test_state_version_is_a_fragment() -> None:
    home = Home(user="a", state_version="26.05")
    assert [f["value"] for f in values(home)] == [{"stateVersion": "26.05"}]


def test_state_version_refuses_a_bad_release() -> None:
    with pytest.raises(ValidationError, match="a release name such as"):
        Home(user="a", state_version="26.7")


def test_native_modules_values_and_inputs_reach_the_json() -> None:
    from minix_config import Input, NativeModule

    home = Home(
        user="a",
        native_modules=[NativeModule("shell.nix")],
        values={"fans": [1, 2]},
        inputs=[Input("example-tool", "github:o/example-tool", modules=["example-tool"])],
    )
    out: dict[str, Any] = dict(home.compile())
    assert out["modules"][0]["file"] == "shell.nix"
    assert out["modules"][0]["source"].startswith("test_config_home.py:")
    assert out["modules"][0]["root"] == "workspace"
    assert out["values"] == {"fans": [1, 2]}
    assert out["inputs"][0]["modules"] == ["example-tool"]


def test_file_executable() -> None:
    plain = values(Home(user="a", files=[File(".config/x", "t")]))[0]["value"]["file"][".config/x"]
    script = values(Home(user="a", files=[File(".local/bin/hi", "#!/bin/sh\n", executable=True)]))[0]["value"]["file"][
        ".local/bin/hi"
    ]
    assert plain == {"text": "t"}
    assert script == {"text": "#!/bin/sh\n", "executable": True}


def test_service_environment_restart_delay_and_desktop() -> None:
    plain = Service("web", program=pkgs.python3, args="-m http.server 8080")
    tuned = Service(
        "web",
        program=pkgs.python3,
        environment={"PORT": "8080", "NAME": "a b"},
        restart_delay="3s",
        starts_with="desktop",
    )
    first, second = (list(s.emit()) for s in (plain, tuned))
    assert len(first) == len(second) == 1
    unit = json.dumps([v.model_dump(exclude_unset=True) for v in second], default=str)
    assert "Service_Environment" in unit and "PORT=8080" in unit and '\\"NAME=a b\\"' in unit.replace("\\\\", "\\")
    assert "graphical-session.target" in unit and "RestartSec" in unit


def test_service_emits_one_value_with_every_key() -> None:
    """pynix 0.3.0 joins the typed fields and the extra keys of one head, so one value holds the whole unit."""
    service = Service(
        "web",
        program=pkgs.python3,
        environment={"PORT": "8080"},
        restart_delay="3s",
        starts_with="desktop",
    )
    emitted = list(service.emit())
    assert len(emitted) == 1
    wire: Any = supplied(emitted[0])
    web = wire["services"]["web"]
    assert set(web) == {"Unit", "Service", "Install"}
    assert web["Unit"] == {
        "Description": "minix service web",
        "PartOf": ["graphical-session.target"],
        "After": ["graphical-session.target"],
    }
    assert set(web["Service"]) == {"ExecStart", "Environment", "Restart", "RestartSec"}
    assert web["Service"]["Environment"] == ["PORT=8080"]
    assert web["Service"]["Restart"] == "on-failure"
    assert web["Service"]["RestartSec"] == "3s"
    assert web["Install"] == {"WantedBy": ["graphical-session.target"]}


@pytest.mark.parametrize("delay", ["3", "3s", "500ms", "1min", "2h"])
def test_restart_delay_accepts_time(delay: str) -> None:
    Service("web", program=pkgs.python3, restart_delay=delay)


def test_restart_delay_refuses_text() -> None:
    with pytest.raises(ValidationError, match='a time such as "3s"'):
        Service("web", program=pkgs.python3, restart_delay="soon")


def test_neovim_default_editor_and_sideload() -> None:
    fragments = values(Home(user="a", programs=[Neovim(default_editor=True)]))
    first = fragments[0]["value"]
    assert fragments[0]["path"] == ["programs", "neovim"]
    assert first["enable"] is True and first["defaultEditor"] is True
    assert first["sideloadInitLua"] is True  # the own ~/.config/nvim stays the learner's


def test_neovim_ty_client() -> None:
    """008-Q4, 009-D8: the editor starts ty with the types of minix."""
    fragments = values(Home(user="a", programs=[Neovim()]))
    second = fragments[1]["value"]
    assert second["extraPackages"][0]["__nixdantic_ref__"] == {"kind": "package", "attr": "ty"}
    text = second["initLua"]["__nixdantic_ref__"]["parts"]
    assert any("minix_ty" in part for part in text if isinstance(part, str))
    assert {"__nixdantic_ref__": {"kind": "config", "path": ["minix", "pythonPath"]}} in text


def test_neovim_without_ty_and_with_plugins_and_lua() -> None:
    home = Home(user="a", programs=[Neovim(ty=False, plugins=[pkgs.hello], lua="vim.g.x = 1")])
    fragments = values(home)
    assert len(fragments) == 1
    assert fragments[0]["value"]["initLua"] == "vim.g.x = 1\n"
    assert fragments[0]["value"]["plugins"][0]["__nixdantic_ref__"]["attr"] == "hello"


def test_files_component() -> None:
    fragments = values(Home(user="a", programs=[Files()]))
    assert [".".join(f["path"]) for f in fragments] == ["programs.yazi", "home", "programs.neovim"]
    assert fragments[1]["value"] == {"shellAliases": {"f": "yazi", "fh": "yazi ~"}}
    # No `settings`: Home Manager writes no file below ~/.config/yazi.
    assert "settings" not in fragments[0]["value"]
    assert fragments[2]["value"]["plugins"][0]["__nixdantic_ref__"]["attr"] == "vimPlugins.yazi-nvim"


def test_terminal_workspace_component() -> None:
    fragments = values(Home(user="a", programs=[TerminalWorkspace()]))
    assert [".".join(f["path"]) for f in fragments] == [
        "programs.zellij",
        "programs.ripgrep",
        "programs.fd",
        "programs.bat",
        "programs.htop",
    ]
    # No auto-start in bash or zsh. The package is the option `minix.zellijPackage`, which `lib/home.nix` fills from the input pyllij.
    assert fragments[0]["value"] == {
        "enable": True,
        "enableBashIntegration": False,
        "enableZshIntegration": False,
        "package": {"__nixdantic_ref__": {"kind": "config", "path": ["minix", "zellijPackage"]}},
    }
    assert all("settings" not in f["value"] for f in fragments)  # no settings file


def test_git_component() -> None:
    fragments = values(Home(user="a", programs=[Git(name="Ada Lovelace", email="ada@example.org")]))
    assert fragments[0]["value"]["settings"] == {"user": {"name": "Ada Lovelace", "email": "ada@example.org"}}


def test_program_takes_one_package_by_position() -> None:
    assert (
        values(Home(user="a", programs=[Program(pkgs.ripgrep)]))[0]["value"]["packages"][0]["__nixdantic_ref__"]["attr"]
        == "ripgrep"
    )
    with pytest.raises(TypeError, match="takes 2 positional arguments"):
        Program(pkgs.ripgrep, pkgs.fd)  # ty: ignore[too-many-positional-arguments]
