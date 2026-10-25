"""The curated items of a home: programs, files, and the capabilities `Git`, `Neovim`, `Files`, and `TerminalWorkspace`.

Each item is a frozen, strict Pydantic model. The identity of an item is positional, its settings are keywords. A
capability emits values of the generated Home Manager classes (`pynix_hm`) and nothing else (L04, 009-D18).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated

from nixdantic import ConfigRef, Expect, Model, Package, Text
from pydantic import AfterValidator, StringConstraints
from pydantic_core import PydanticCustomError
from pynix_hm.home.__options__ import Home as HmHome
from pynix_hm.home.__options__ import Home_File_Value
from pynix_hm.programs.bat import Bat as HmBat
from pynix_hm.programs.fd import Fd as HmFd
from pynix_hm.programs.git import Git as HmGit
from pynix_hm.programs.htop import Htop as HmHtop
from pynix_hm.programs.neovim import Neovim as HmNeovim
from pynix_hm.programs.ripgrep import Ripgrep as HmRipgrep
from pynix_hm.programs.yazi import Yazi as HmYazi
from pynix_hm.programs.zellij import Zellij as HmZellij
from pynix_pkgs import Pkg

from ._base import Component

__all__ = ["File", "Files", "Git", "Neovim", "Program", "TerminalWorkspace"]


def _relative_path(value: str) -> str:
    """A path below the home folder (the keys of `home.file` are relative to `$HOME`)."""
    if value.startswith("/"):
        raise PydanticCustomError(
            "file_absolute",
            "must be relative to your home folder, not absolute: write '.config/NAME' instead of '/home/YOU/.config/NAME'",
        )
    if value.startswith("~"):
        raise PydanticCustomError(
            "file_tilde", "must not start with '~': the path is already relative to your home folder"
        )
    if ".." in value.split("/"):
        raise PydanticCustomError("file_dotdot", "must stay below your home folder: it must not hold '..'")
    if value == "" or value.endswith("/"):
        raise PydanticCustomError("file_dir", "must name a file, and not end with '/'")
    return value


Email = Annotated[
    str,
    StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    Expect("an email address, for example you@example.org"),
]
GitName = Annotated[str, StringConstraints(min_length=1), Expect("your name for commits, for example 'Ada Lovelace'")]
FilePath = Annotated[str, AfterValidator(_relative_path)]
Content = Annotated[str | Text, Expect('text, for example "greeting = hello\\n"')]


class Git(Component):
    """Install Git and set your name and email for commits."""

    name: GitName
    """Your name. Git writes it into each commit."""
    email: Email
    """Your email address. Git writes it into each commit."""

    if TYPE_CHECKING:

        def __init__(self, *, name: GitName, email: Email) -> None:
            """Install Git and set your name and email for commits.

            Args:
                name: Your name. Git writes it into each commit.
                email: Your email address. Git writes it into each commit.
            """
            ...

    def emit(self) -> Iterator[Model]:
        yield HmGit(enable=True, settings={"user": {"name": self.name, "email": self.email}})


# The Lua text of the editor client of ty (008-Q4, 009-D8). The language server `ty` reads `home.py` with the types of
# `minix_config` and `pynix`; the second part of the search path is the folder of the input packages of the workspace.
_TY_HEAD = """-- minix: the language server ty reads home.py with the types of minix_config.
vim.lsp.config("minix_ty", {
  cmd = function(dispatchers, config)
    local root = config.root_dir or vim.fn.getcwd()
    local path = \""""
_TY_TAIL = """" .. ":" .. root .. "/.basix/pynix"
    return vim.lsp.rpc.start({ "ty", "server" }, dispatchers, { env = { PYTHONPATH = path }, cwd = root })
  end,
  filetypes = { "python" },
  root_markers = { "home.py" },
})
vim.lsp.enable("minix_ty")
"""


class Neovim(Component):
    """Install Neovim, a text editor for the terminal. Your own `~/.config/nvim` stays yours."""

    default_editor: bool = False
    """Make Neovim the editor that other programs start (the EDITOR variable)."""
    plugins: list[Pkg] = []
    """Neovim plugins, for example `pkgs.vimPlugins.yazi_nvim`."""
    lua: str = ""
    """Lua text that Neovim runs at start. Your own `~/.config/nvim/init.lua` runs after it, so yours wins."""
    ty: bool = True
    """Check `home.py` while you type: Neovim starts the language server ty with the types of minix. Set False to turn it off."""

    if TYPE_CHECKING:

        def __init__(
            self, *, default_editor: bool = False, plugins: list[Pkg] = [], lua: str = "", ty: bool = True
        ) -> None:
            """Install Neovim, a text editor for the terminal. Your own `~/.config/nvim` stays yours.

            Args:
                default_editor: Make Neovim the editor that other programs start (the EDITOR variable).
                plugins: Neovim plugins, for example `pkgs.vimPlugins.yazi_nvim`.
                lua: Lua text that Neovim runs at start. Your own `~/.config/nvim/init.lua` runs after it, so yours wins.
                ty: Check `home.py` while you type: Neovim starts the language server ty with the types of minix.
            """
            ...

    def emit(self) -> Iterator[Model]:
        # `sideloadInitLua` loads the Lua text through the wrapper, so Home Manager writes no `~/.config/nvim/init.lua`.
        yield HmNeovim(
            enable=True,
            defaultEditor=self.default_editor,
            sideloadInitLua=True,
            plugins=list(self.plugins),
            **({"initLua": self.lua + "\n"} if self.lua else {}),
        )
        if self.ty:
            yield HmNeovim(
                extraPackages=[Package("ty")],
                initLua=Text(_TY_HEAD, ConfigRef(["minix", "pythonPath"]), _TY_TAIL),
            )


class Program(Component):
    """Install one program from the package list, for example `Program(pkgs.ripgrep)`."""

    _positional = ("package",)
    package: Pkg
    """The package: `pkgs.NAME`. Search for the name at https://search.nixos.org/packages."""

    if TYPE_CHECKING:

        def __init__(self, package: Pkg, /) -> None:
            """Install one program from the package list, for example `Program(pkgs.ripgrep)`.

            Args:
                package: The package: `pkgs.NAME`. Search for the name at https://search.nixos.org/packages.
            """
            ...

    def emit(self) -> Iterator[Model]:
        yield HmHome(packages=[self.package])


class File(Component):
    """A file in your home folder. Home Manager links it to a read-only copy of the text."""

    _positional = ("path", "text")
    path: FilePath
    """Where the file goes, relative to your home folder, for example ".config/demo/demo.conf"."""
    text: Content
    """The content of the file."""
    executable: bool = False
    """Make the file a program that you can run, for example a script in ".local/bin". Start the text with "#!/bin/sh"."""

    if TYPE_CHECKING:

        def __init__(self, path: FilePath, text: Content, /, *, executable: bool = False) -> None:
            """A file in your home folder. Home Manager links it to a read-only copy of the text.

            Args:
                path: Where the file goes, relative to your home folder, for example ".config/demo/demo.conf".
                text: The content of the file.
                executable: Make the file a program that you can run. Start the text with "#!/bin/sh".
            """
            ...

    def emit(self) -> Iterator[Model]:
        extra = {"executable": True} if self.executable else {}
        yield HmHome(file={self.path: Home_File_Value(text=self.text, **extra)})


class Files(Component):
    """Browse files in the terminal with yazi. The aliases `f` (here) and `fh` (your home folder) start it."""

    if TYPE_CHECKING:

        def __init__(self) -> None:
            """Browse files in the terminal with yazi. The aliases `f` (here) and `fh` (your home folder) start it."""
            ...

    def emit(self) -> Iterator[Model]:
        # yazi reads the folder `~/.config/yazi` of the learner: set no `settings`, so Home Manager writes no file there.
        yield HmYazi(enable=True)
        yield HmHome(shellAliases={"f": "yazi", "fh": "yazi ~"})
        # The editor part works with `Neovim()`: Home Manager ignores the plugin list when Neovim is off.
        yield HmNeovim(plugins=[Package("vimPlugins.yazi-nvim")], initLua=_YAZI_LUA)


# Source: the "without a package manager" section of the README of yazi.nvim. The start text runs before Neovim loads
# its plugins, so the setup waits for `VimEnter`. The key map is set at once, so the own `init.lua` of the learner can
# replace it. The `pcall` keeps a plugin error from stopping the start.
_YAZI_LUA = """-- minix Files: open yazi inside Neovim (yazi.nvim)
vim.keymap.set({ "n", "v" }, "<leader>-", "<cmd>Yazi<cr>", { desc = "Open yazi at the current file" })
vim.api.nvim_create_autocmd("VimEnter", {
  once = true,
  callback = function()
    local ok, yazi = pcall(require, "yazi")
    if ok then
      yazi.setup({})
    else
      vim.notify("minix Files: the yazi plugin did not load", vim.log.levels.WARN)
    end
  end,
})
"""


class TerminalWorkspace(Component):
    """Panes and tabs in the terminal (zellij), and four search and view tools: rg, fd, bat, htop."""

    if TYPE_CHECKING:

        def __init__(self) -> None:
            """Panes and tabs in the terminal (zellij), and four search and view tools: rg, fd, bat, htop."""
            ...

    def emit(self) -> Iterator[Model]:
        # zellij does not start by itself: the login stays a plain shell. No component sets `settings`, so Home Manager
        # writes no settings file and the folders `~/.config/zellij`, `htop`, and `bat` stay the learner's.
        yield HmZellij(enable=True, enableBashIntegration=False)
        yield HmRipgrep(enable=True)
        yield HmFd(enable=True)
        yield HmBat(enable=True)
        yield HmHtop(enable=True)
