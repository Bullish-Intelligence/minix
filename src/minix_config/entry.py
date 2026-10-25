"""The written entry: the first `home.py` that `minix init` puts in a new workspace (009-D2, 009-D23, 009-D34).

The file holds one import line with every learner name, the account name, the state version, and the common fields
empty, each with an example in a comment. It leaves out the advanced fields `options`, `native_modules`, `inputs`, and
`values`: they stay in `Home`, and the overview names them as hatches (009-D34).
"""

from __future__ import annotations

import re

__all__ = ["STATE_VERSION", "written_entry"]

STATE_VERSION = "26.11"
"""The release of Home Manager that the pinned minix first set up. `minix init` writes it into `home.py` (009-D39)."""

_USER = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

_TEMPLATE = """from minix_config import *

home = Home(
    user="{user}",
    state_version="{state_version}",
    programs=[],  # Program(pkgs.ripgrep), Git(name="Ada Lovelace", email="ada@example.org"), Neovim(default_editor=True)
    files=[],  # File(".config/demo/demo.conf", "greeting = hello\\n")
    services=[],  # Service("web", program=pkgs.python3, args="-m http.server 8080")
)
"""


def written_entry(user: str, state_version: str = STATE_VERSION) -> str:
    """The text of the first `home.py`, with `user` (the running account) filled in."""
    if not _USER.fullmatch(user):
        raise ValueError(
            f"the account name {user!r} is not a valid Home user: use a lower case letter, then up to 31 lower case letters, digits, or '-'"
        )
    if not re.fullmatch(r"[0-9]{2}\.(05|11)", state_version):
        raise ValueError(f'the state version {state_version!r} is not a release name such as "26.11"')
    return _TEMPLATE.format(user=user, state_version=state_version)
