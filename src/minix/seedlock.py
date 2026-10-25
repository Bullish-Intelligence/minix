"""The lock of a new workspace, made offline from the files that the `minix` package carries (009-D2, G9).

The package holds two files in `share/minix/` (the folder that `MINIX_SHARE` names):

- `source.json`: the source of this build of minix: `rev`, `narHash`, `lastModified`, and `revCount`.
- `flake.lock`: the lock of the flake of minix itself, so every input of minix has a locked version.

`workspace_lock` joins them into the `flake.lock` of a workspace whose stub has one input, `minix`. The `minix` node
comes from `source.json`. Every other node comes from the lock of minix. Then `nix flake lock --offline` accepts the
file, and the first `minix apply` needs no network for the locks.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

SOURCE = "source.json"
LOCK = "flake.lock"
LOCK_VERSION = 7
_REQUIRED = ("rev", "narHash")


def git_original(url: str) -> dict[str, str] | None:
    """The `original` attributes of a `git+https://HOST/PATH?ref=REF` flake URL, or None for another form."""
    if not re.match(r"git\+(https?|file|ssh)://", url):
        return None
    parts = urlsplit(url.removeprefix("git+"))
    query = parse_qs(parts.query)
    if set(query) - {"ref"} or any(len(values) != 1 for values in query.values()):
        return None  # a pinned `rev` or a `dir` does not match the source of this build
    original = {"type": "git", "url": f"{parts.scheme}://{parts.netloc}{parts.path}"}
    if "ref" in query:
        original["ref"] = query["ref"][0]
    return original


def read_source(share: Path) -> dict[str, Any] | None:
    """The `source.json` of the package, or None when it is missing, unreadable, or without a revision."""
    try:
        source = json.loads((share / SOURCE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(source, dict) or any(not source.get(key) for key in _REQUIRED):
        return None
    return source


def _remap(value: Any) -> Any:
    """Move one `inputs` value of the lock of minix below the node `minix`: a follows path starts at the root."""
    return ["minix", *value] if isinstance(value, list) else value


def workspace_lock(share: Path, url: str) -> str | None:
    """The text of a `flake.lock` for a workspace whose `minix` input has `url`, or None when `share` cannot give one."""
    original = git_original(url)
    source = read_source(share)
    if original is None or source is None:
        return None
    try:
        own = json.loads((share / LOCK).read_text())
        root = own["nodes"][own["root"]]
        nodes: dict[str, Any] = {name: node for name, node in own["nodes"].items() if name != own["root"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if "minix" in nodes or "root" in nodes:
        return None  # a node name would collide with the new `minix` node or the new root
    locked = {**original, "rev": source["rev"], "narHash": source["narHash"]}
    for key in ("lastModified", "revCount"):
        if source.get(key) is not None:
            locked[key] = source[key]
    for node in nodes.values():
        if isinstance(node.get("inputs"), Mapping):
            node["inputs"] = {key: _remap(value) for key, value in node["inputs"].items()}
    nodes["minix"] = {
        "inputs": {key: _remap(value) for key, value in root.get("inputs", {}).items()},
        "locked": locked,
        "original": original,
    }
    nodes["root"] = {"inputs": {"minix": "minix"}}
    return json.dumps({"nodes": nodes, "root": "root", "version": own.get("version", LOCK_VERSION)}, indent=2) + "\n"
