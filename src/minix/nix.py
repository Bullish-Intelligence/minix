"""The Nix calls of minix (009-D29): every `nix` command gets the two experimental features that minix needs."""

from collections.abc import Callable, Sequence
from pathlib import Path

from pynix_engine import Result, Runner

from minix.layer import NIX_FEATURES


def with_features(argv: Sequence[str]) -> list[str]:
    """Add the features after the program name of a `nix` command. Leave every other command as it is."""
    words = list(argv)
    if words[:1] == ["nix"]:
        return [words[0], *NIX_FEATURES, *words[1:]]
    return words


class NixRunner:
    """A runner that wraps another one. The engine, the compile step, and `update` all run `nix` through it."""

    def __init__(self, inner: Runner) -> None:
        self.inner = inner

    def run(
        self, argv: Sequence[str], *, cwd: Path | None = None, on_line: Callable[[str], None] | None = None
    ) -> Result:
        return self.inner.run(with_features(argv), cwd=cwd, on_line=on_line)
