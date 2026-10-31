"""Build a Python input from its uv.lock for a Home Manager configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class UvLockBuild(BaseModel):
    """Build the default dependency group of a flake input with uv2nix.

    The home exposes ``minix.builds.NAME.environment`` and
    ``minix.builds.NAME.command``. The command output contains one executable.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["uv-lock"] = "uv-lock"
    name: str
    input: str
    command: str
    python: str = "python313"
    hatchling: list[str] = []

    @field_validator("name", "input", "command")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
            raise ValueError("use letters, digits, underscores, or hyphens")
        return value


class TextFileBuild(BaseModel):
    """Make one text file under a package output."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["text-file"] = "text-file"
    name: str
    destination: str
    text: str

    @field_validator("destination")
    @classmethod
    def _destination(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value.split("/"):
            raise ValueError("use an absolute path below the package output")
        return value


class FetchUrlBuild(BaseModel):
    """Fetch one file by URL and fixed hash."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["fetch-url"] = "fetch-url"
    name: str
    url: str
    hash: str


class WriteTextBuild(BaseModel):
    """Write text as one file in the Nix store."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["write-text"] = "write-text"
    name: str
    text: str


class ReplaceTextBuild(BaseModel):
    """Write text after replacing one marker with a build path."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["replace-text"] = "replace-text"
    name: str
    text: str
    marker: str
    replacement: str


class ActivationAfter(BaseModel):
    """Run a home activation script after named Home Manager steps."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str
    after: list[str]
    script: str
    package_paths: dict[str, str] = {}
    build_paths: dict[str, str] = {}


class FlakePackageBuild(BaseModel):
    """Refer to an existing package output of a flake input."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["flake-package"] = "flake-package"
    name: str
    input: str
    attr: str


class InputSourceBuild(BaseModel):
    """Refer to the source path of a flake input."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["input-source"] = "input-source"
    name: str
    input: str


class ShellApplicationBuild(BaseModel):
    """Write one shell command with Nixpkgs programs on its PATH."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["shell-application"] = "shell-application"
    name: str
    text: str
    runtime_inputs: list[str] = []
    package_paths: dict[str, str] = {}
    build_paths: dict[str, str] = {}
