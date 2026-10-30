#!/usr/bin/env python3
"""The VM check `opt:minix-update`: `minix update` from one minix release to the next (G8, 009-D10, 010-D31).

Run from the repository root, through the devenv: `devenv shell -- uv run --no-sync python scripts/update_vm.py`.
The check needs `/dev/kvm`. It never counts for green. It reuses the helpers of `scripts/apply_vm.py`.

The host makes a scratch channel. It never writes to a real repository and never makes a real tag:

- a bare copy of `pynix`, with one more scratch tag `pynix-v0.5.0-rc.93`. The tag has the tree of `pynix-v0.5.0`,
  and a changelog section with one line that names `home.sessionVariables`;
- a bare `minix` repository with two scratch releases made by `scripts/release.sh --skip-status`: `v<version>-rc.91` (the
  files of this checkout, so the check tests the commit under check) and `v<version>-rc.92` (one more commit: it pins
  `pynix-v0.5.0-rc.93` and adds the file `~/.config/minix/release` to a home that holds the `minix` package). The branch
  `release` is at `v<version>-rc.92`;
- a git config that maps the two public URLs to the scratch repositories (`insteadOf`). Only the host commands of this
  script read it, through `GIT_CONFIG_GLOBAL`. The `~/.gitconfig` of the owner is not read.

Every host `nix` and `git` command of the check uses its own cache folder, `$MINIX_VM_WORK/cache` (`XDG_CACHE_HOME`), so
the Nix cache of the owner never maps a public URL to a scratch ref. The log lists the scratch cache. It shows that the
cache of the owner got no new or changed entry for a scratch tag, and that the scratch cache holds no download.

The VM has no network and no substituter. The host builds what the VM needs: the `minix` package of each release, the
compiler and the home of each release, and the sources of the flake inputs. It copies them in, with the two scratch
repositories, and writes the `~/.gitconfig` of the learner with the same `insteadOf` rules.

Cases (each prints `CHECK ok` or `CHECK FAIL` lines):

- `init`: the `minix` command of `v<version>-rc.91` is on `PATH`; `minix init` offline writes `~/minix`; its lock holds the
  revision of `v<version>-rc.91` with `ref=release`.
- `apply1`: `minix apply` builds the home of `v<version>-rc.91`; the file of `v<version>-rc.92` is not there.
- `update`: `minix update` prints the two release names, the age of nixpkgs, the changelog line of `pynix-v0.5.0-rc.93`,
  and the next step. The lock holds the revision of `v<version>-rc.92` and the nested `pynix` at `-rc.93`.
- `apply2`: `minix apply` builds and switches; the file of `v<version>-rc.92` is there; a second apply changes nothing.
- `again`: a second `minix update` says that minix is up to date and prints no `pynix` line.

Environment (all optional):

- `MINIX_VM_WORK`: the folder of the scratch repositories and the VM files (default `$XDG_CACHE_HOME/minix/minix-update`, else `~/.cache/minix/minix-update`).
- `MINIX_VM_LOGS`: the raw logs and `results.json` (default `~/.local/state/basix/minix-update`).
- `MINIX_VM_PORT`: the host port of the test channel (default 22541).
- `MINIX_VM_PYNIX`: the repository to clone for the scratch `pynix`: a folder or an URL (default: the public pynix URL, which the
  Git configuration of the user may map to a local copy). Clones only read it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import sqlite3
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# apply_vm reads these variables when it loads, so the defaults of this check go in first.
os.environ.setdefault(
    "MINIX_VM_WORK", str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "minix" / "minix-update")
)
os.environ.setdefault("MINIX_VM_LOGS", str(Path.home() / ".local/state/basix/minix-update"))
os.environ.setdefault("MINIX_VM_PORT", "22541")
os.environ["MINIX_VM_HOST"] = "plain"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_vm as av  # noqa: E402

ROOT = av.ROOT
WORK, LOGS, PORT = av.WORK, av.LOGS, av.PORT

# The cache of the owner (read only, for the proof) and the cache of this check. `uv` keeps its own cache: the release
# job runs `uv sync --locked`, which needs the wheels that the owner's cache holds.
_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
OWNER_NIX_CACHE = _CACHE_HOME / "nix"
os.environ.setdefault("UV_CACHE_DIR", str(_CACHE_HOME / "uv"))
os.environ["XDG_CACHE_HOME"] = str(WORK / "cache")
SCRATCH_NIX_CACHE = WORK / "cache" / "nix"
GROUPS = ["init", "apply1", "update", "apply2", "again"]

MINIX_URL = "https://github.com/Bullish-Intelligence/minix"
PYNIX_URL = "https://github.com/Bullish-Intelligence/pynix"
# `release.sh` checks that the tag names the version of `pyproject.toml`, so the scratch tags follow that version.
VERSION = re.search(r'^version = "(.*)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE)[1]
R1, R2 = f"v{VERSION}-rc.91", f"v{VERSION}-rc.92"
PYNIX_OLD, PYNIX_NEW = "pynix-v0.5.0", "pynix-v0.5.0-rc.93"
STAMP = "2026-10-21T12:00:00+00:00"  # a fixed date: the same inputs make the same commits, so a second run finds a warm store
MARKER = ".config/minix/release"  # the visible change of R2, in the learner's home
# What the scratch copy of this checkout leaves out: state of tools, caches, and links of the workspace.
SKIP_TOP = {
    ".git", ".jj", ".gitman", ".devenv", ".devenv.flake.nix", ".direnv", ".venv", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".testee", ".agents", ".claude", ".copyroom-local.json", "devenv.local.nix", "build", "dist", "result",
}  # fmt: skip
SOURCE_PYNIX = os.environ.get("MINIX_VM_PYNIX", PYNIX_URL)

# The line that the changelog of pynix-v0.5.0-rc.93 names. Its form is the form of `pynix.changelog` (a type change).
CHANGE_LINE = "home.sessionVariables: attribute set of (string or signed integer) -> attribute set of string"
SECTION = f"""## {PYNIX_NEW}

```text
pynix 0.3.0-rc.93 (nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e; flake-parts 024633c)
Previous: 0.3.0 (nixpkgs e7439b6, 2026-10-08). Gap: 0 d 0 h.
NixOS options: 21699 -> 21699. Added 0, removed 0, type changed 0, renamed 0.
Home Manager options: 5229 -> 5229. Added 0, removed 0, type changed 1, renamed 0.
flake-parts options: 26 -> 26. Added 0, removed 0, type changed 0, renamed 0.
Package names: 25285 -> 25285. Added 0, removed 0.
Home Manager:
 Type changed:
  {CHANGE_LINE}
```
"""
# The change of R2 in lib/home.nix. It is off when the package is null, so the reference homes of `check_nix.py` stay as they are.
HOME_NIX_ANCHOR = "      programs.bash.enable = lib.mkDefault true;\n"
HOME_NIX_ADD = f"""      # The scratch change of the check opt:minix-update: a file that only a home with the `minix` package gets.
      home.file."{MARKER}" = lib.mkIf (config.minix.package != null) {{ text = "{R2}\\n"; }};
"""

FACTS: dict = {}  # revisions and store paths of this run, for the log and for results.json


def home_py() -> str:
    """The `home.py` of the check: it sets `home.sessionVariables.EDITOR`, the option that the changelog line names."""
    return av.home_text(options=av.EDITOR_OPTION)


# --- the host: scratch repositories ----------------------------------------------------------------------------
def ancestors() -> set[int]:
    """The process ids of this process and its parents. A parent shell may hold a pattern in its command line."""
    found, pid = set(), os.getpid()
    while pid > 1:
        found.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return found


def wait_quiet(limit: int = 7200) -> None:
    """Wait while another session runs a VM, a verify run, a gate run, or a release job (AGENT-COMMON)."""
    end = time.time() + limit
    while True:
        seen = []
        ps = subprocess.run(["ps", "-eo", "comm"], capture_output=True, text=True).stdout
        if any("qemu" in name for name in ps.split()):
            seen.append("qemu")
        for pattern in ("testee verify", "run_gates.sh", "release.sh"):
            found = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
            if {int(pid) for pid in found} - ancestors():
                seen.append(pattern)
        if not seen:
            return
        if time.time() > end:
            raise SystemExit(f"another session still runs: {seen}")
        av.log(f"   waiting: {seen}")
        time.sleep(30)


def g(cwd: Path, *args: str) -> str:
    """Run git in a scratch repository (a fixed identity and date, no global config)."""
    return av.host(
        ["git", *args], cwd=cwd, env={**av.git_env(), "GIT_AUTHOR_DATE": STAMP, "GIT_COMMITTER_DATE": STAMP}
    ).stdout


def channel_env(config: Path) -> dict[str, str]:
    """The environment of the host commands that fetch through the scratch channel."""
    return {**av.git_env(), "GIT_AUTHOR_DATE": STAMP, "GIT_COMMITTER_DATE": STAMP, "GIT_CONFIG_GLOBAL": str(config)}


def gitconfig(minix_repo: Path | str, pynix_repo: Path | str) -> str:
    return (
        f'[url "{minix_repo}"]\n\tinsteadOf = {MINIX_URL}\n'
        f'[url "{pynix_repo}"]\n\tinsteadOf = {PYNIX_URL}\n'
        "[safe]\n\tdirectory = *\n"
    )


def copy_checkout(target: Path) -> None:
    """Copy the files of this checkout into `target`. `.gitignore` then keeps the same files out of the scratch commit."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    target.mkdir(parents=True)
    for entry in sorted(ROOT.iterdir()):
        if entry.name in SKIP_TOP:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target / entry.name, symlinks=True, ignore=ignore)
        else:
            shutil.copy2(entry, target / entry.name, follow_symlinks=False)


def prepare_repos() -> tuple[Path, Path, Path, Path]:
    """Make the scratch `pynix` and `minix` repositories and the config of the channel."""
    repos = WORK / "repos"
    shutil.rmtree(repos, ignore_errors=True)
    repos.mkdir(parents=True)
    # pynix: a bare copy (clone only reads the source), then one scratch tag on a commit with one more changelog section.
    # The clone uses the Git configuration of the user, so an `insteadOf` rule can map the public URL to a local copy.
    av.host(
        ["git", "clone", "-q", "--bare", "--no-hardlinks", SOURCE_PYNIX, "pynix.git"],
        cwd=repos,
        env={"GIT_CONFIG_GLOBAL": str(Path.home() / ".gitconfig")},
    )
    pynix = repos / "pynix.git"
    g(pynix, "remote", "remove", "origin")
    g(repos, "clone", "-q", "--no-hardlinks", str(pynix), "pynix-work")
    work = repos / "pynix-work"
    g(work, "remote", "remove", "origin")
    g(work, "checkout", "-q", "--detach", PYNIX_OLD)
    text = (work / "CHANGELOG.md").read_text()
    head = "# Changelog\n\n"
    assert text.startswith(head), "the changelog of pynix starts with a title"
    (work / "CHANGELOG.md").write_text(head + SECTION + "\n" + text[len(head) :])
    g(work, "add", "CHANGELOG.md")
    g(work, "commit", "-q", "-m", f"Scratch release {PYNIX_NEW}: one changelog section")
    g(work, "tag", "-a", PYNIX_NEW, "-m", f"{PYNIX_NEW} (scratch)")
    g(work, "push", "-q", str(pynix), PYNIX_NEW)
    FACTS["pynix_old"] = g(pynix, "rev-parse", f"{PYNIX_OLD}^{{commit}}").strip()
    FACTS["pynix_new"] = g(pynix, "rev-parse", f"{PYNIX_NEW}^{{commit}}").strip()
    tree_old = g(pynix, "rev-parse", f"{PYNIX_OLD}^{{commit}}:src").strip()
    tree_new = g(pynix, "rev-parse", f"{PYNIX_NEW}^{{commit}}:src").strip()
    av.check(
        "repos",
        "the src tree of the scratch tag equals the src tree of pynix-v0.5.0 (the pair check holds)",
        tree_old,
        tree_new,
    )
    # minix: a new repository with the files of this checkout (the commit under check), and a bare repository for the channel.
    work_minix = repos / "minix-work"
    copy_checkout(work_minix)
    g(work_minix, "init", "-q", "-b", "main")
    g(work_minix, "add", "-A")
    g(work_minix, "commit", "-q", "-m", "Scratch copy of the checkout under check (opt:minix-update)")
    FACTS["base"] = g(work_minix, "rev-parse", "HEAD").strip()
    g(repos, "init", "-q", "--bare", "minix.git")
    minix = repos / "minix.git"
    g(minix, "symbolic-ref", "HEAD", "refs/heads/release")
    config = WORK / "channel.gitconfig"
    config.write_text(gitconfig(minix, pynix))
    return pynix, minix, work_minix, config


def release(clone: Path, tag: str, config: Path) -> str:
    """Run the release job in the scratch clone. Return the revision of the tag."""
    env = {
        **channel_env(config),
        "MINIX_RELEASE_LOG": str(LOGS / f"release-{tag}"),
        "MINIX_RELEASE_WORK": str(WORK / f"release-work-{tag}"),
    }
    started = time.time()
    done = av.host(["scripts/release.sh", "--skip-status", tag], cwd=clone, env=env, check_rc=False)
    av.log(f"   release.sh --skip-status {tag}: exit {done.returncode}, {time.time() - started:.0f} s")
    for line in done.stdout.splitlines():
        if line.startswith("[release"):
            av.log("      " + line)
    if done.returncode != 0:
        raise SystemExit(f"release.sh {tag} failed ({done.returncode}):\n{(done.stdout + done.stderr)[-2000:]}")
    shutil.rmtree(WORK / f"release-work-{tag}", ignore_errors=True)
    return g(clone, "rev-parse", f"{tag}^{{commit}}").strip()


def make_releases(clone: Path, minix: Path, config: Path) -> None:
    """R1 is `main` as it is. R2 is one scratch commit on top of R1: the new pin of pynix and the marker file."""
    FACTS["r1"] = release(clone, R1, config)
    flake = (clone / "flake.nix").read_text()
    assert flake.count(PYNIX_OLD) == 1, "flake.nix names the pynix tag once"
    (clone / "flake.nix").write_text(flake.replace(PYNIX_OLD, PYNIX_NEW))
    home = (clone / "lib/home.nix").read_text()
    assert home.count(HOME_NIX_ANCHOR) == 1, "lib/home.nix has the anchor line once"
    (clone / "lib/home.nix").write_text(home.replace(HOME_NIX_ANCHOR, HOME_NIX_ANCHOR + HOME_NIX_ADD))
    # The lock names only the public URL: the channel maps it to the scratch repository while the lock is made.
    av.host(["nix", *av.FEATURES, "flake", "update", "pynix"], cwd=clone, env=channel_env(config))
    g(clone, "add", "-A")
    g(
        clone,
        "commit",
        "-q",
        "-m",
        "Scratch change for opt:minix-update: pin pynix-v0.5.0-rc.93, add the release marker file",
    )
    FACTS["r2"] = release(clone, R2, config)
    g(clone, "push", "-q", str(minix), R1, R2, f"{R2}^{{commit}}:refs/heads/release")
    FACTS["release_branch"] = g(minix, "rev-parse", "refs/heads/release").strip()
    av.check("repos", "the branch release of the scratch minix is at R2", FACTS["r2"], FACTS["release_branch"])


def prove_channel(config: Path, pynix: Path) -> None:
    """Show that the public URLs lead to the scratch repositories, not to the source `pynix`."""
    out = av.host(["git", "ls-remote", "--tags", PYNIX_URL], cwd=pynix, env=channel_env(config)).stdout
    av.check(
        "channel",
        "the public pynix URL lists the scratch tag rc.93 through the channel",
        True,
        f"refs/tags/{PYNIX_NEW}" in out,
    )
    av.log("   git ls-remote --tags " + PYNIX_URL + " (GIT_CONFIG_GLOBAL = the scratch config):")
    for line in out.splitlines():
        av.log("      " + line)
    source = av.host(
        ["git", "ls-remote", "--tags", SOURCE_PYNIX], env={"GIT_CONFIG_GLOBAL": str(Path.home() / ".gitconfig")}
    ).stdout
    av.check("channel", "the tag rc.93 is not in the source pynix", False, "rc.93" in source)


# --- the Nix cache of the host ---------------------------------------------------------------------------------
def owner_cache_state() -> dict[str, str]:
    """The names and times of the entries of the owner's Nix cache (top level and the git cache folders)."""
    state: dict[str, str] = {}
    if not OWNER_NIX_CACHE.is_dir():
        return state
    for entry in sorted(OWNER_NIX_CACHE.iterdir()):
        state[entry.name] = str(entry.lstat().st_mtime_ns)
    git = OWNER_NIX_CACHE / "gitv3"
    for entry in sorted(git.iterdir()) if git.is_dir() else []:
        state[f"gitv3/{entry.name}"] = str(entry.lstat().st_mtime_ns)
    return state


def scratch_tags(folder: Path) -> list[str]:
    """The tag and branch names of a Nix git cache folder that look like a scratch release."""
    names = [p.name for p in (folder / "refs").rglob("*") if p.is_file()] if (folder / "refs").is_dir() else []
    packed = folder / "packed-refs"
    if packed.is_file():
        names += [line.split()[-1] for line in packed.read_text().splitlines() if line and line[0] not in "#^"]
    return sorted(n for n in names if re.search(r"rc\.9[0-9]$", n))


def prove_cache(before: dict[str, str]) -> None:
    """Show what the scratch cache holds, and that the owner's cache got no entry for a scratch ref."""
    av.log(f"   scratch Nix cache: {SCRATCH_NIX_CACHE}")
    git = SCRATCH_NIX_CACHE / "gitv3"
    for entry in sorted(git.iterdir()) if git.is_dir() else []:
        if not entry.is_dir() or entry.name.endswith(".lock"):
            continue
        fetch = entry / "FETCH_HEAD"
        source = fetch.read_text().split(" of ")[-1].strip() if fetch.is_file() and " of " in fetch.read_text() else "-"
        av.log(f"      gitv3/{entry.name}: from {source}; scratch refs {scratch_tags(entry)}")
    av.check(
        "cache", "the scratch cache holds git folders (the run used it)", True, git.is_dir() and any(git.iterdir())
    )
    fetcher = SCRATCH_NIX_CACHE / "fetcher-cache-v4.sqlite"
    downloads = -1
    if fetcher.is_file():
        db = sqlite3.connect(f"file:{fetcher}?mode=ro", uri=True)
        downloads = db.execute("select count(*) from Cache where domain in ('file', 'tarball')").fetchone()[0]
        db.close()
    av.check("cache", "the scratch cache holds no download (no file or tarball entry)", 0, downloads)
    after = owner_cache_state()
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    av.log(
        f"   owner Nix cache {OWNER_NIX_CACHE}: {len(before)} entries before, {len(after)} after; changed during the run: {changed}"
    )
    for name in changed:
        av.log(f"      {name}: mtime_ns {before.get(name)} -> {after.get(name)}")
    touched = [n for n in changed if n.startswith("gitv3/") and scratch_tags(OWNER_NIX_CACHE / n)]
    av.check("cache", "no git folder of the owner's cache changed during the run and holds a scratch ref", [], touched)
    gitv3 = (before.get("gitv3"), after.get("gitv3"))
    av.info("cache", "mtime_ns of the owner's gitv3 folder, before and after", gitv3)
    av.check("cache", "the owner's gitv3 folder has the same mtime before and after", gitv3[0], gitv3[1])


# --- the host: packages, homes, sources -------------------------------------------------------------------------
def build_package(minix: Path, tag: str, config: Path) -> Path:
    url = f"git+file://{minix}?ref=refs/tags/{tag}#minix"
    done = av.host(
        ["nix", *av.FEATURES, "build", "--print-out-paths", "--out-link", str(WORK / f"minix-{tag}"), url],
        env=channel_env(config),
    )
    return Path(done.stdout.split()[-1])


def build_home(ws: Path, name: str, config: Path) -> tuple[Path, Path]:
    """Compile and build the home of a prepared workspace. Return the compiler and the activation package."""
    env = channel_env(config)
    av.host(["git", "add", "-A"], cwd=ws, env=env)
    compiler = Path(
        av.host(
            [
                "nix",
                *av.FEATURES,
                "build",
                "--print-out-paths",
                "--out-link",
                str(WORK / f"prep/links/{name}-compiler"),
                ".#compiler",
            ],
            cwd=ws,
            env=env,
        ).stdout.split()[-1]
    )
    av.host([str(compiler / "bin/minix-compile"), "home.py", "home.json"], cwd=ws)
    av.host(["git", "add", "-A"], cwd=ws, env=env)
    out = av.host(
        [
            "nix", *av.FEATURES, "build", "--print-out-paths", "--out-link", str(WORK / f"prep/links/{name}-home"),
            f".#homeConfigurations.{av.USER}.activationPackage",
        ],
        cwd=ws,
        env=env,
    ).stdout.split()[-1]  # fmt: skip
    return compiler, Path(out)


def lock_of(ws: Path) -> dict:
    return json.loads((ws / "flake.lock").read_text())


def nested_pynix(lock: dict) -> dict:
    """The locked node of the `pynix` input of the `minix` node."""
    nodes = lock["nodes"]
    return nodes[nodes[nodes["root"]["inputs"]["minix"]]["inputs"]["pynix"]]


def prepare_homes(r1_pkg: Path, config: Path) -> tuple[dict[str, Path], set[str], list[Path], str]:
    """Build the home of each release on the host, in the way the VM will. Return the homes, the store paths, the workspaces."""
    shutil.rmtree(WORK / "prep", ignore_errors=True)
    (WORK / "prep/links").mkdir(parents=True)
    env = channel_env(config)
    ws1 = WORK / "prep/r1"
    av.host([str(r1_pkg / "bin/minix"), "init", "--dir", str(ws1)], env=env)
    (ws1 / "home.py").write_text(home_py())
    av.host(["git", "add", "-A"], cwd=ws1, env=env)
    init_lock = (ws1 / "flake.lock").read_text()
    av.host(["nix", *av.FEATURES, "flake", "lock", "--offline"], cwd=ws1, env=env)
    av.check(
        "prepare",
        "the lock of minix init holds R1 with ref=release",
        (FACTS["r1"], "release"),
        (lock_of(ws1)["nodes"]["minix"]["locked"]["rev"], lock_of(ws1)["nodes"]["minix"]["original"]["ref"]),
    )
    av.check(
        "prepare",
        "nix flake lock --offline leaves the lock of init as it is",
        init_lock,
        (ws1 / "flake.lock").read_text(),
    )
    paths: set[str] = set()
    outputs: dict[str, Path] = {}
    compiler1, home1 = build_home(ws1, "r1", config)
    outputs["r1"] = home1
    paths |= av.closure(compiler1) | av.closure(home1)
    # R2: the same workspace after `minix update` (the host runs the command that the VM will run).
    ws2 = WORK / "prep/r2"
    shutil.copytree(ws1, ws2, symlinks=True)
    update = av.host([str(r1_pkg / "bin/minix"), "update"], cwd=ws2, env=env, check_rc=False)
    FACTS["host_update_output"] = update.stdout + update.stderr
    av.log(f"   host `minix update` (exit {update.returncode}):")
    for line in FACTS["host_update_output"].splitlines():
        av.log("      " + line)
    if update.returncode != 0:
        raise SystemExit("the host `minix update` failed")
    compiler2, home2 = build_home(ws2, "r2", config)
    outputs["r2"] = home2
    paths |= av.closure(compiler2) | av.closure(home2)
    lock2 = lock_of(ws2)
    FACTS["lock2_minix"] = lock2["nodes"]["minix"]["locked"]["rev"]
    FACTS["lock2_pynix"] = nested_pynix(lock2)
    av.check("prepare", "the lock after the host update holds R2", FACTS["r2"], FACTS["lock2_minix"])
    av.check(
        "prepare",
        "the nested pynix is the scratch tag rc.93",
        (PYNIX_NEW, FACTS["pynix_new"]),
        (FACTS["lock2_pynix"]["locked"]["ref"].removeprefix("refs/tags/"), FACTS["lock2_pynix"]["locked"]["rev"]),
    )
    nixpkgs = json.loads(
        av.nix("flake", "archive", "--json", "--dry-run", "--no-write-lock-file", ".", cwd=ws1).stdout
    )["inputs"]["minix"]["inputs"]["nixpkgs"]["path"]
    return outputs, paths, [ws1, ws2], nixpkgs


# --- the VM -----------------------------------------------------------------------------------------------------
def lines_of(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def read_lock_vm(where: str = "~/minix") -> dict:
    return json.loads(av.sh(f"cat {where}/flake.lock", show=False).out)


def resolved(path: Path) -> str:
    return str(path.resolve())


def case_init(ctx: dict) -> None:
    found = av.sh("readlink -f $(command -v minix)", show=False).out.strip()
    check = av.check
    check("init", "the minix command on PATH is the package of R1", resolved(ctx["r1_pkg"]) + "/bin/minix", found)
    source = json.loads(
        av.sh("cat $(dirname $(readlink -f $(command -v minix)))/../share/minix/source.json", show=False).out
    )
    check("init", "share/minix/source.json names the revision of R1", FACTS["r1"], source.get("rev"))
    r = av.minix("init", "init", where="~")
    check("init", "init exits 0", 0, r.rc)
    lock = read_lock_vm()
    locked, original = lock["nodes"]["minix"]["locked"], lock["nodes"]["minix"]["original"]
    check("init", "the lock holds the revision of R1", FACTS["r1"], locked.get("rev"))
    check("init", "the lock follows the branch release", "release", original.get("ref"))
    check("init", "the lock names the public URL", MINIX_URL, original.get("url"))
    check(
        "init",
        "the lock holds pynix at pynix-v0.5.0",
        PYNIX_OLD,
        nested_pynix(lock)["locked"]["ref"].removeprefix("refs/tags/"),
    )
    av.put("minix/home.py", home_py())


def generation() -> str:
    return av.sh("readlink -f ~/.local/state/nix/profiles/home-manager", show=False).out.strip()


def case_apply1(ctx: dict) -> None:
    av.sh(f"loginctl enable-linger {av.USER}", user="root", title="loginctl enable-linger")
    r = av.minix("apply", "apply R1", then="true")
    fields = r.fields()
    av.check("apply1", "the apply exits 0", "0", fields.get("RC"))
    av.check(
        "apply1", "the apply built and switched", True, "applied: the home runs, and its units are healthy" in r.lines
    )
    av.check(
        "apply1", "the generation is the home that the host built for R1", resolved(ctx["homes"]["r1"]), generation()
    )
    marker = av.sh(f"cat ~/{MARKER} 2>&1; echo rc=$?", show=False).out
    av.check("apply1", "the visible change of R2 is absent", True, "rc=1" in marker.splitlines()[-1:][0])
    editor = av.sh("bash -lc 'echo EDITOR=$EDITOR'", show=False).out.strip()
    av.check("apply1", "the home sets the option that the changelog names", "EDITOR=ed", editor)
    ctx["lock_before_update"] = av.sh("cat ~/minix/flake.lock", show=False).out


def case_update(ctx: dict) -> None:
    r = av.minix("update", "update")
    av.RESULTS["notes"]["update: full output"] = r.out
    av.check("update", "update exits 0", 0, r.rc)
    lines = r.lines
    av.check("update", "the output has five lines", 5, len(lines))
    first = lines[0] if lines else ""
    av.check("update", "line 1 names the two releases", f"minix {R1} -> {R2}", first)
    age = lines[1] if len(lines) > 1 else ""
    av.check(
        "update", "line 2 is the nixpkgs age line", True, bool(re.fullmatch(r"nixpkgs [0-9a-f]{8}, \d+ days? old", age))
    )
    av.check(
        "update",
        "line 3 names the changelog and the option",
        f"{PYNIX_NEW} changes options that home.py sets:",
        lines[2] if len(lines) > 2 else "",
    )
    av.check(
        "update",
        "line 4 is the changelog line, with the name that sets it",
        f"  {CHANGE_LINE}  [Home]",
        lines[3] if len(lines) > 3 else "",
    )
    av.check("update", "line 5 names the next step", "next: run minix apply to use it", lines[-1] if lines else "")
    lock = read_lock_vm()
    av.check("update", "the lock holds the revision of R2", FACTS["r2"], lock["nodes"]["minix"]["locked"]["rev"])
    pynix = nested_pynix(lock)["locked"]
    av.check(
        "update",
        "the nested pynix is at rc.93",
        (PYNIX_NEW, FACTS["pynix_new"]),
        (pynix["ref"].removeprefix("refs/tags/"), pynix["rev"]),
    )
    av.check("update", "the VM and the host made the same lock", lock_of(ctx["ws"][1]), lock)
    av.check("update", "the VM output equals the output of the host", lines_of(FACTS["host_update_output"]), lines)
    ctx["lock_after_update"] = av.sh("cat ~/minix/flake.lock", show=False).out
    state = av.sh("cd ~/minix && git status --porcelain", show=False).out.strip()
    av.check("update", "update changes only flake.lock in the tree", "M flake.lock", state)


def case_apply2(ctx: dict) -> None:
    r = av.minix("apply", "apply R2", then="true")
    av.check("apply2", "the apply exits 0", "0", r.fields().get("RC"))
    av.check(
        "apply2", "the apply built and switched", True, "applied: the home runs, and its units are healthy" in r.lines
    )
    av.check(
        "apply2", "the generation is the home that the host built for R2", resolved(ctx["homes"]["r2"]), generation()
    )
    marker = av.sh(f"cat ~/{MARKER}", show=False).out
    av.check("apply2", "the visible change of R2 is present", f"{R2}\n", marker)
    found = av.sh("readlink -f $(command -v minix)", show=False).out.strip()
    av.check("apply2", "the minix command is now the package of R2", resolved(ctx["r2_pkg"]) + "/bin/minix", found)
    again = av.minix("apply", "apply R2, second")
    av.check("apply2", "the second apply exits 0", 0, again.rc)
    av.check("apply2", "the second apply says nothing to change", True, "nothing to change" in again.lines)
    saved = av.sh("cd ~/minix && git log -1 --format=%s; git status --porcelain", show=False).lines
    av.check(
        "apply2", "the apply saved flake.lock and left a clean tree", True, len(saved) == 1 and "flake.lock" in saved[0]
    )


def case_again(ctx: dict) -> None:
    before = av.sh("cat ~/minix/flake.lock", show=False).out
    r = av.minix("update", "update again")
    av.RESULTS["notes"]["again: full output"] = r.out
    av.check("again", "update exits 0", 0, r.rc)
    lines = r.lines
    av.check("again", "line 1 says that minix is up to date", f"minix is up to date ({R2})", lines[0] if lines else "")
    av.check("again", "the output has no pynix line", [], [line for line in lines if "pynix" in line])
    av.check("again", "the lock did not change", before, av.sh("cat ~/minix/flake.lock", show=False).out)
    av.check(
        "again", "the last line names the next step", "next: run minix apply to use it", lines[-1] if lines else ""
    )


def stage(paths: set[str], workspaces: list[Path], repos: list[Path], config: Path) -> list[str]:
    """Copy what the VM needs: closures, flake sources, and the two scratch repositories (as store paths)."""
    ssh_env = {**os.environ, "NIX_SSHOPTS": " ".join(av.SSHOPT)}
    t = time.time()
    for ws in workspaces:
        done = subprocess.run(
            ["nix", *av.FEATURES, "flake", "archive", "--no-check-sigs", "--to", "ssh-ng://root@127.0.0.1", "--no-write-lock-file", str(ws)],
            env=ssh_env, capture_output=True, text=True,
        )  # fmt: skip
        av.log(f"== nix flake archive {ws.name}: rc={done.returncode}, {time.time() - t:.0f} s\n{done.stderr[-300:]}")
        if done.returncode != 0:
            raise SystemExit(
                "nix flake archive failed: the VM has no sources of the lock, so every case would fail offline"
            )
    store_repos = []
    for repo in repos:
        out = av.host(["nix", *av.FEATURES, "store", "add", "--name", repo.name, str(repo)]).stdout.strip()
        store_repos.append(out)
    everything = sorted(paths | set(store_repos))
    t = time.time()
    done = subprocess.run(
        ["nix", *av.FEATURES, "copy", "--no-check-sigs", "--to", "ssh-ng://root@127.0.0.1", *everything],
        env=ssh_env, capture_output=True, text=True,
    )  # fmt: skip
    av.log(
        f"== nix copy of {len(everything)} paths: rc={done.returncode}, {time.time() - t:.0f} s\n{done.stderr[-300:]}"
    )
    if done.returncode != 0:
        raise SystemExit("nix copy failed")
    return store_repos


def main() -> int:
    parser = argparse.ArgumentParser(description="The VM check opt:minix-update.")
    parser.add_argument("--cases", default=",".join(GROUPS), help="comma list of: " + ", ".join(GROUPS))
    parser.add_argument("--keep-vm", action="store_true", help="debug: leave the VM running at the end")
    parser.add_argument(
        "--prepare-only", action="store_true", help="debug: make the repositories, packages, homes, and VM only"
    )
    args = parser.parse_args()
    groups = args.cases.split(",")
    WORK.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    av.RAW = open(LOGS / "update_vm.log", "w")
    # A new cache for each run: Nix keeps the revision of a ref for an hour, and the scratch tags move from run to run.
    shutil.rmtree(WORK / "cache", ignore_errors=True)
    if not (WORK / "key").exists():
        av.host(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(WORK / "key"), "-C", "minix-update"])
    av.SSHOPT = av.ssh_options()
    started = time.time()
    if subprocess.run(["ssh", *av.SSHOPT, "root@127.0.0.1", "true"], capture_output=True).returncode == 0:
        raise SystemExit(f"port {PORT} already answers: a VM from an earlier run is still alive")
    av.log(f"== groups {groups}; work {WORK}; logs {LOGS}")
    wait_quiet()
    owner_before = owner_cache_state()
    pynix, minix, clone, config = prepare_repos()
    make_releases(clone, minix, config)
    prove_channel(config, pynix)
    FACTS["config"] = config.read_text()
    av.log(f"== scratch base commit {FACTS['base']} (the files of {ROOT.name})")
    r1_pkg, r2_pkg = build_package(minix, R1, config), build_package(minix, R2, config)
    source = json.loads((r1_pkg / "share/minix/source.json").read_text())
    av.log(f"== R1 package {r1_pkg}; source.json {source}")
    av.check("prepare", "source.json of the R1 package names the R1 revision", FACTS["r1"], source.get("rev"))
    av.check(
        "prepare",
        "source.json of the R2 package names the R2 revision",
        FACTS["r2"],
        json.loads((r2_pkg / "share/minix/source.json").read_text()).get("rev"),
    )
    homes, paths, workspaces, nixpkgs = prepare_homes(r1_pkg, config)
    av.OUTPUTS.update(homes)
    paths |= av.closure(r2_pkg)
    vm_dir = av.build_vm(r1_pkg, nixpkgs)
    av.log(f"== prepared in {time.time() - started:.0f} s; {len(paths)} store paths to copy")
    av.RESULTS["notes"]["facts"] = {k: v for k, v in FACTS.items() if k != "lock2_pynix"}
    runner = next((vm_dir / "bin").glob("run-*-vm"))
    if args.prepare_only or any(not c["ok"] for c in av.RESULTS["checks"]):
        prove_cache(owner_before)
        return finish(started, 0 if args.prepare_only else 1)
    wait_quiet()
    env = dict(os.environ)
    run_dir = WORK / "run"
    shutil.rmtree(run_dir, ignore_errors=True)
    (run_dir / "tmp").mkdir(parents=True)
    env.update(
        NIX_DISK_IMAGE=str(run_dir / "vm.qcow2"),
        TMPDIR=str(run_dir / "tmp"),
        QEMU_NET_OPTS=f"hostfwd=tcp:127.0.0.1:{PORT}-:2222,restrict=on",
        NO_COLOR="1",
    )
    vm = None
    try:
        console = open(LOGS / "qemu-console.log", "ab")
        vm = subprocess.Popen(
            [str(runner)],
            env=env,
            cwd=run_dir,
            stdin=subprocess.DEVNULL,
            stdout=console,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_MEMLOCK, (65536, 65536)),
        )
        time.sleep(3)
        if vm.poll() is not None:
            raise SystemExit(f"the VM runner exited with {vm.returncode}; see {LOGS}/qemu-console.log")
        av.wait_ssh()
        av.log(f"== VM up after {time.time() - started:.0f} s")
        store_repos = stage(paths, workspaces, [minix, pynix], config)
        av.put(".gitconfig", gitconfig(store_repos[0], store_repos[1]))
        av.RESULTS["times"]["prepare and stage"] = round(time.time() - started, 1)
        ctx = {"r1_pkg": r1_pkg, "r2_pkg": r2_pkg, "homes": homes, "ws": workspaces}
        cases = {
            "init": case_init,
            "apply1": case_apply1,
            "update": case_update,
            "apply2": case_apply2,
            "again": case_again,
        }
        for group in GROUPS:
            if group not in groups:
                continue
            av.log(f"\n== case {group}")
            try:
                cases[group](ctx)
            except Exception as error:  # a crashed case must not stop the session
                import traceback

                av.check(group, f"the case crashed: {type(error).__name__}", "no crash", str(error)[:300], ok=False)
                av.log(traceback.format_exc())
    finally:
        if vm is not None and not args.keep_vm:
            subprocess.run(["ssh", *av.SSHOPT, "root@127.0.0.1", "poweroff"], capture_output=True)
            try:
                vm.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(vm.pid, signal.SIGTERM)
    prove_cache(owner_before)
    return finish(started)


def finish(started: float, forced: int | None = None) -> int:
    failed = [c for c in av.RESULTS["checks"] if not c["ok"]]
    counts: dict[str, dict[str, int]] = {}
    for item in av.RESULTS["checks"]:
        row = counts.setdefault(item["case"], {"ok": 0, "fail": 0})
        row["ok" if item["ok"] else "fail"] += 1
    av.RESULTS["counts"] = counts
    av.RESULTS["total_s"] = round(time.time() - started, 1)
    (LOGS / "results.json").write_text(json.dumps(av.RESULTS, indent=2, default=str) + "\n")
    av.log(
        f"\n{len(av.RESULTS['checks']) - len(failed)} of {len(av.RESULTS['checks'])} checks ok, {len(failed)} failed, {av.RESULTS['total_s']} s"
    )
    for case, row in counts.items():
        av.log(f"   {case}: {row['ok']} ok, {row['fail']} failed")
    for item in failed:
        av.log(
            f"FAIL [{item['case']}] {item['check']}: expected {item['expected']!r}, actual {str(item['actual'])[:200]!r}"
        )
    av.log("PASS" if not failed else "FAIL")
    return forced if forced is not None else (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
