"""The workspace of minix: where it is, what the host is, and the facts that minix reads from the host."""

import platform
import re
from collections.abc import Mapping
from pathlib import Path

from pynix_engine import Fail, Session

from minix.layer import DEFAULT_FOLDER, WORDS, WORKSPACE_VARIABLE

SYSTEMS = {"x86_64": "x86_64-linux", "amd64": "x86_64-linux", "aarch64": "aarch64-linux", "arm64": "aarch64-linux"}

# The URL line of the managed stub. A later apply keeps the URL that the stub already holds.
STUB_URL = re.compile(r'inputs\.minix\.url\s*=\s*"([^"]+)"')


def host_system(kernel: str | None = None, machine: str | None = None) -> str:
    """The Nix system of the host (009-D29): `minix init` records it, so the stub holds no fixed system."""
    kernel = kernel or platform.system()
    machine = (machine or platform.machine()).lower()
    if kernel != "Linux" or machine not in SYSTEMS:
        raise Fail(f"minix runs on x86_64 and aarch64 Linux only, not on {machine} {kernel}.")
    return SYSTEMS[machine]


def tilde(path: Path, home: Path) -> str:
    """A path for the learner: `~/minix` instead of the full path, when it lies under the home folder."""
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    return "~" if str(relative) == "." else f"~/{relative}"


def stub_url(workspace: Path) -> str | None:
    """The URL of the `minix` input in the stub of the workspace, or None when there is no stub or no such line."""
    try:
        found = STUB_URL.search((workspace / "flake.nix").read_text())
    except OSError:
        return None
    return found.group(1) if found else None


def expand_home(text: str, home: Path) -> Path:
    """`~` and `~/x` mean the home folder of this run. Any other text is a path as written."""
    if text == "~":
        return home
    return home / text[2:] if text.startswith("~/") else Path(text)


def environment_workspace(env: Mapping[str, str], cwd: Path, home: Path) -> Path | None:
    """The folder that `MINIX_WORKSPACE` names, or None when the variable is unset or empty.

    A folder with no `home.py` is an error: the variable was set on purpose, so minix does not fall back.
    """
    value = env.get(WORKSPACE_VARIABLE, "")
    if not value:
        return None
    folder = cwd / expand_home(value, home)
    if not (folder / "home.py").is_file():
        raise Fail(WORDS.bad_workspace_variable.format(variable=WORKSPACE_VARIABLE, value=value, entry="home.py"))
    return folder.resolve()


def init_workspace(explicit: Path | None, cwd: Path, home: Path, env: Mapping[str, str] | None = None) -> Path:
    """The folder that `minix init` makes: `--dir`, else the folder that `MINIX_WORKSPACE` names, else `~/minix`.

    The variable may name a folder that does not exist yet. `minix init` makes it and its parents.
    """
    if explicit is not None:
        return explicit.resolve()
    value = (env or {}).get(WORKSPACE_VARIABLE, "")
    return (cwd / expand_home(value, home)).resolve() if value else (home / "minix").resolve()


def find_workspace(explicit: Path | None, cwd: Path, home: Path, env: Mapping[str, str] | None = None) -> Path:
    """The minix workspace for this run.

    `--dir` wins. Else the repository that holds `cwd` counts when it has a `home.py`. A Basix workspace gets
    its own line (009-D15). Else the folder that `MINIX_WORKSPACE` names. Else `~/minix`.
    """
    if explicit is not None:
        return explicit.resolve()
    for folder in (cwd, *cwd.parents):
        if not (folder / ".git").exists():
            continue
        if (folder / "home.py").is_file():
            return folder
        if (folder / "system.py").is_file():
            raise Fail(WORDS.basix_workspace.format(path=tilde(folder, home), name="minix", folder=DEFAULT_FOLDER))
        break
    if (named := environment_workspace(env or {}, cwd, home)) is not None:
        return named
    default = home / "minix"
    if (default / ".git").exists():
        return default
    raise Fail(
        WORDS.no_workspace.format(
            path=tilde(cwd, home), name="minix", folder=DEFAULT_FOLDER, variable=WORKSPACE_VARIABLE
        )
    )


def lingering(session: Session, user: str) -> bool | None:
    """Whether the account lingers (the user manager runs with no login). None when the host cannot tell."""
    result = session.run(["loginctl", "show-user", user, "--property=Linger", "--value"], quiet=True)
    if result.code != 0:
        return None
    answer = result.out.strip().lower()
    return {"yes": True, "no": False}.get(answer)


def warn_no_linger(session: Session, user: str) -> None:
    """009-D30, BSX-393: say once per run that user services stop at logout when the account does not linger."""
    if lingering(session, user) is False:
        session.say(session.layer.text("no_linger", user=user))


def environment_home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()
