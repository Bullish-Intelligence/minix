"""The minix command-line interface: `init`, `apply`, `undo`, and `update`."""

from pathlib import Path
from typing import Annotated

import typer

from minix import __version__, commands

app = typer.Typer(
    help="minix applies one typed home.py as a standalone Home Manager configuration.",
    no_args_is_help=True,
    add_completion=False,
)

Folder = Annotated[
    Path | None, typer.Option("--dir", help="The workspace folder (default: the current one, else ~/minix).")
]
Verbose = Annotated[bool, typer.Option("--verbose", help="Show every command and its output.")]


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"minix {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_show_version, is_eager=True, help="Print the version and exit.")
    ] = False,
) -> None:
    """minix applies one typed home.py as a standalone Home Manager configuration."""


def _exit(code: int) -> None:
    raise typer.Exit(code)


@app.command("init")
def init_command(
    directory: Annotated[Path | None, typer.Option("--dir", help="The new folder (default: ~/minix).")] = None,
    url: Annotated[
        str | None, typer.Option("--url", help="The source of minix in the stub (default: the release).")
    ] = None,
) -> None:
    """Make the workspace ~/minix: the stub, the lock, home.py, and the first save. It needs no network."""
    _exit(commands.init(commands.real_context(), directory, url))


@app.command("apply")
def apply_command(directory: Folder = None, verbose: Verbose = False) -> None:
    """Compile home.py, build the home, switch to it, check its services, then save the files."""
    _exit(commands.apply(commands.real_context(), directory, verbose))


@app.command("undo")
def undo_command(directory: Folder = None, verbose: Verbose = False) -> None:
    """Drop an edit that minix did not apply; else go back to the save before the last one. Then apply."""
    _exit(commands.undo(commands.real_context(), directory, verbose))


@app.command("update")
def update_command(
    name: Annotated[str, typer.Argument(help="The input to move (default: minix).")] = "minix",
    directory: Folder = None,
    verbose: Verbose = False,
) -> None:
    """Move the lock of minix, or of one input of home.py. Run apply to use the new release."""
    _exit(commands.update(commands.real_context(), name, directory, verbose))
