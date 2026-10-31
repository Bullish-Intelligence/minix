"""A home records typed uv.lock builds for Nix evaluation."""

from typing import Any

import pytest
from pydantic import ValidationError

from minix_config import (
    ActivationAfter,
    FetchUrlBuild,
    FlakePackageBuild,
    Home,
    InputSourceBuild,
    ReplaceTextBuild,
    ShellApplicationBuild,
    TextFileBuild,
    UvLockBuild,
)


def test_uv_build_reaches_compiled_home() -> None:
    build = UvLockBuild(name="agentman", input="agentman", command="agentman", hatchling=["inferference"])
    home = Home(user="andrew", builds=[build])
    assert home.compile()["builds"] == [
        {
            "name": "agentman",
            "kind": "uv-lock",
            "input": "agentman",
            "command": "agentman",
            "python": "python313",
            "hatchling": ["inferference"],
        }
    ]


def test_build_rejects_invalid_input() -> None:
    with pytest.raises(ValidationError):
        UvLockBuild(name="agentman", input="../other", command="agentman")


def test_text_file_build_rejects_parent_path() -> None:
    with pytest.raises(ValidationError):
        TextFileBuild(name="completion", destination="/share/../etc/example", text="example")


def test_asset_and_activation_reach_compiled_home() -> None:
    home = Home(
        user="andrew",
        builds=[
            FetchUrlBuild(name="plugin", url="https://example.org/plugin.wasm", hash="sha256-example"),
            ReplaceTextBuild(name="layout", text="plugin=@PLUGIN@", marker="@PLUGIN@", replacement="plugin"),
        ],
        activations=[
            ActivationAfter(
                name="grantPlugin",
                after=["writeBoundary"],
                script="python3 @WRITER@ @PLUGIN@",
                build_paths={"@PLUGIN@": "plugin", "@WRITER@": "layout"},
            )
        ],
    )
    compiled: dict[str, Any] = dict(home.compile())
    assert [build["kind"] for build in compiled["builds"]] == ["fetch-url", "replace-text"]
    assert compiled["activations"][0]["after"] == ["writeBoundary"]


def test_input_packages_and_shell_application_reach_compiled_home() -> None:
    home = Home(
        user="andrew",
        builds=[
            FlakePackageBuild(name="paseo", input="nix-paseo", attr="paseo"),
            InputSourceBuild(name="source", input="nix-paseo"),
            ShellApplicationBuild(name="launcher", text="exec @PASEO@/bin/paseo", build_paths={"@PASEO@": "paseo"}),
        ],
    )
    compiled: dict[str, Any] = dict(home.compile())
    assert [build["kind"] for build in compiled["builds"]] == [
        "flake-package",
        "input-source",
        "shell-application",
    ]
