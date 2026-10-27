"""The Zellij of `TerminalWorkspace` comes from pyllij and from nothing else (Z2).

These tests read files and run no Nix. The Nix check `zellij` of `scripts/check_nix.py` evaluates the home and compares
the package with the one that the locked input `pyllij` defines.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYLLIJ_URL = re.compile(r'pyllij\s*=\s*\{\s*url\s*=\s*"([^"]+)"', re.S)


def pinned_url() -> str:
    match = PYLLIJ_URL.search((ROOT / "flake.nix").read_text())
    assert match, "flake.nix has no input pyllij"
    return match[1]


URL = "git+https://github.com/Bullish-Intelligence/pyllij"
PIN = re.compile(re.escape(URL) + r"\?(rev=(?P<rev>[0-9a-f]{40})|ref=refs/tags/(?P<tag>v[0-9]+\.[0-9]+\.[0-9]+))")


def test_flake_pins_pyllij_by_revision_or_tag() -> None:
    # The public URL of the repository with a full revision or a release tag. The owner's Git config maps the URL to a cache.
    assert PIN.fullmatch(pinned_url()), pinned_url()


def test_lock_holds_the_pin() -> None:
    match = PIN.fullmatch(pinned_url())
    assert match
    lock = json.loads((ROOT / "flake.lock").read_text())
    node = lock["nodes"][lock["nodes"]["root"]["inputs"]["pyllij"]]
    assert node["locked"]["type"] == "git" and node["locked"]["url"] == URL.removeprefix("git+")
    if match["rev"]:
        assert node["locked"]["rev"] == match["rev"] and node["original"]["rev"] == match["rev"]
    else:
        # The lock holds the tag and the commit that the tag named when it was locked.
        assert node["original"]["ref"] == node["locked"]["ref"] == f"refs/tags/{match['tag']}"
        assert re.fullmatch(r"[0-9a-f]{40}", node["locked"]["rev"])


def test_pyllij_inputs_follow_the_pins_of_minix() -> None:
    """The lock holds one nixpkgs and one copy of each uv2nix input: every input of pyllij follows a pin of minix."""
    nodes = json.loads((ROOT / "flake.lock").read_text())["nodes"]
    for name, follows in nodes["pyllij"].get("inputs", {}).items():
        assert follows == [name], f"the input {name} of pyllij does not follow {name} of minix"
    assert not [n for n in nodes if re.fullmatch(r"(nixpkgs|uv2nix|pyproject-nix|pyproject-build-systems)_\d+", n)]


def test_home_option_reads_the_input() -> None:
    text = (ROOT / "lib/home.nix").read_text()
    assert "default = inputs.pyllij.packages.${system}.zellij;" in text


def test_the_component_names_the_option() -> None:
    text = (ROOT / "src/minix_config/_components.py").read_text()
    assert 'package=ConfigRef(["minix", "zellijPackage"])' in text
