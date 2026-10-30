#!/usr/bin/env python3
"""The VM check `opt:minix-apply`: the real `minix` command on a NixOS machine (GAPS G26, G9, G15, BSX-374 to BSX-393).

Run from the repository root, through the devenv: `devenv shell -- uv run --no-sync python scripts/apply_vm.py`.
The check needs `/dev/kvm`. It never counts for green. It builds the `minix` package from a scratch copy of this tree,
boots a NixOS VM with one learner account, and runs the cases below as that account over a test SSH channel.

The VM has no network and no substituter. The host builds each home first and copies the store paths in, so the
commands in the VM find every build output and every flake input in the store. `minix init` writes the lock from the
files of the package, and `nix flake lock --offline` accepts it.

Cases (each prints `CHECK ok` or `CHECK FAIL` lines):

- `env`: the VM facts: no network. On the plain host: Nix with flakes off by default, no lingering. On the Basix host:
  Nix with flakes on and lingering on (the core sets both, 010-D16, `BSX-393`).
- `init`: `minix init` offline writes `~/minix`: stub, lock, `home.py` with the account, `.gitignore`, first commit.
- `apply`: a `Service` (form C). Plain host, no lingering: one warning, the service stops at logout; then the check turns
  lingering on. Basix host, lingering on: no warning, the service runs with no login. Then it answers, and a second apply
  prints "nothing to change".
  After the first apply, `command -v minix` in a login shell names the profile of the home, not the system
  (the home holds the `minix` of its own lock, L113).
- `editor`: a login shell has `EDITOR` set (010-D14, G15).
- `account`: a wrong `Home.user` stops with the account line; nothing changes.
- `fail`: a failing service: the activation rolls back, the edit stays, nothing is committed; `MINIX_ON_FAIL=keep` keeps
  the new activation.
- `undo`: a dirty tree, a clean tree, the toggle, and the one-commit refusal.
- `rollback`: a failing service, and the activate script of the earlier home fails too (a bind mount in the VM). The
  failed-rollback line prints, the mark `home-manager.unsettled` stays, and the next apply switches again instead of
  saying "only files changed" or "nothing to change". A good home then clears the mark.
- `basix`: a folder with `system.py` names `basix apply`.
- `input`: one `Input` on Home Manager (the demo input, from a local path): stub, lock, input package, compile, build.
- `gitinput`: the same input from a `git+file` folder. After one apply, the folder moves away. The next apply reads the
  names of the input from the store (the VM has no network): the stub keeps its follows lines, and `flake.lock` stays.

Environment (all optional):

- `MINIX_VM_HOST`: `plain` (default) or `basix`. `basix` needs `BASIX_FLAKE`, a flake reference of a Basix v2 workspace
  whose `nixosConfigurations.basix` has `disk=None` and a root file system (see `nix/vm.nix`).
- `MINIX_VM_WORK`: the folder of the VM files, the scratch copy, and the prepared homes (default
  `$XDG_CACHE_HOME/minix/minix-apply`, else `~/.cache/minix/minix-apply`).
- `MINIX_VM_LOGS`: the raw logs and `results.json` (default `~/.local/state/basix/minix-apply`).
- `MINIX_VM_PORT`: the host port of the test channel (default 22540).
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from minix_config.entry import written_entry  # noqa: E402

SPEC_FILES = ["flake.nix", "flake.lock", "pyproject.toml", "uv.lock", "README.md", "lib", "nix", "src"]
FEATURES = ["--extra-experimental-features", "nix-command flakes"]
USER = "learner"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
WORK = Path(os.environ.get("MINIX_VM_WORK", CACHE / "minix" / "minix-apply"))
LOGS = Path(os.environ.get("MINIX_VM_LOGS", Path.home() / ".local/state/basix/minix-apply"))
PORT = os.environ.get("MINIX_VM_PORT", "22540")
HOST = os.environ.get("MINIX_VM_HOST", "plain")
BASIX = HOST == "basix"  # the Basix core turns flakes and lingering on (010-D16), so some expectations differ
KEY = WORK / "key"
GROUPS = ["env", "init", "apply", "editor", "account", "fail", "undo", "rollback", "basix", "input", "gitinput"]

SERVICE = 'Service("web", program=pkgs.python3, args="-m http.server 8080")'
BAD_SERVICE = 'Service("api", program=pkgs.python3, args="-c exit(1)")'
HOME_TEMPLATE = """from minix_config import *
{imports}
home = Home(
    user="{user}",
    state_version="26.11",
    programs=[{programs}],
    services=[{services}],
    options=[{options}],
)
"""
EDITOR_OPTION = 'hm.Home(sessionVariables={"EDITOR": "ed"})'
DEMO_OPTION = 'demo.Demo(enable=True, greeting="hi", count=2)'


def home_text(services: str = "", programs: str = "", options: str = "", imports: str = "", user: str = USER) -> str:
    return HOME_TEMPLATE.format(
        user=user, services=services, programs=programs, options=options, imports=imports + "\n" if imports else ""
    )


# The homes of the cases. The host builds each one first (`prepare_homes`).
STATES = {
    "init": written_entry(USER),
    "svc": home_text(services=SERVICE, options=EDITOR_OPTION),
    "bad": home_text(services=f"{SERVICE}, {BAD_SERVICE}", options=EDITOR_OPTION),
}
DEMO_HOME = home_text(
    programs="Program(pkgs.ripgrep)",
    options=DEMO_OPTION,
    imports="import pynix_demo as demo\n",
).replace("home = Home(", 'home = Home(\n    inputs=[Input("demo", "path:@URL@", modules=["demo"])],')
GIT_DEMO_HOME = DEMO_HOME.replace('"path:@URL@"', '"git+file://@URL@"')
FAIL_ACTIVATE = "#!/bin/sh\necho 'activate: scratch failure of the earlier home' >&2\nexit 7\n"
UNSETTLED = "$HOME/.local/state/nix/profiles/home-manager.unsettled"
INPUT_HOMES = {"input", "gitinput"}  # the homes with a flake input: the host also builds their input packages

OUTPUTS: dict[str, Path] = {}
RESULTS: dict = {"checks": [], "times": {}, "notes": {}}
RAW = None
SSHOPT: list[str] = []


# --- logging ---------------------------------------------------------------------------------------------------
def log(text: str = "") -> None:
    print(text, flush=True)
    if RAW:
        RAW.write(text + "\n")
        RAW.flush()


def check(case: str, what: str, expected: object, actual: object, ok: bool | None = None) -> bool:
    ok = (expected == actual) if ok is None else ok
    RESULTS["checks"].append({"case": case, "check": what, "expected": expected, "actual": actual, "ok": bool(ok)})
    log(f"   CHECK {'ok  ' if ok else 'FAIL'} [{case}] {what}: expected {expected!r}, actual {str(actual)[:400]!r}")
    return bool(ok)


def info(case: str, what: str, value: object) -> None:
    RESULTS["notes"][f"{case}: {what}"] = value
    log(f"   INFO [{case}] {what}: {str(value)[:400]!r}")


# --- the host --------------------------------------------------------------------------------------------------
def host(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, check_rc: bool = True):
    """Run a command on the host. The output goes to the raw log."""
    full = {**os.environ, **(env or {})}
    done = subprocess.run(command, cwd=cwd, env=full, capture_output=True, text=True)
    if RAW:
        RAW.write(f"$ {' '.join(command)}  (exit {done.returncode})\n{done.stdout}{done.stderr}\n")
        RAW.flush()
    if check_rc and done.returncode != 0:
        raise SystemExit(f"{' '.join(command)} failed ({done.returncode}):\n{(done.stdout + done.stderr)[-2000:]}")
    return done


def nix(*args: str, cwd: Path | None = None, check_rc: bool = True):
    return host(["nix", *FEATURES, *args], cwd=cwd, check_rc=check_rc)


def git_env() -> dict[str, str]:
    """A fixed identity and date: the scratch commit of the same tree is the same commit."""
    date = "2026-01-01T00:00:00+00:00"
    return {
        "GIT_AUTHOR_NAME": "scratch",
        "GIT_AUTHOR_EMAIL": "scratch@example.org",
        "GIT_COMMITTER_NAME": "scratch",
        "GIT_COMMITTER_EMAIL": "scratch@example.org",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def scratch_git(path: Path, *args: str) -> str:
    return host(["git", *args], cwd=path, env=git_env()).stdout


def prepare_source() -> Path:
    """A scratch git repository of the files that the flake reads. Its `self.rev` feeds `share/minix/source.json`."""
    target = WORK / "src"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name in SPEC_FILES:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target / name)
    scratch_git(target, "init", "-q", "-b", "main")
    scratch_git(target, "add", "-A")
    scratch_git(target, "commit", "-q", "-m", "scratch copy for apply_vm.py")
    return target


def build_minix(source: Path) -> Path:
    out = nix("build", "--print-out-paths", "--out-link", str(WORK / "minix"), f"git+file://{source}#minix").stdout
    return Path(out.split()[-1])


def prepare_homes(minix: Path) -> tuple[dict[str, Path], set[str], str]:
    """Build each home on the host in a scratch workspace. Return the home outputs, the store paths, and nixpkgs."""
    shutil.rmtree(WORK / "prep", ignore_errors=True)
    (WORK / "prep" / "links").mkdir(parents=True)
    fake_home = WORK / "prep" / "home"
    fake_home.mkdir()
    demo = WORK / "prep" / "demo-input"
    shutil.copytree(ROOT / "tests/fixtures/inputs/demo", demo)
    env = {"HOME": str(fake_home), **git_env()}
    demo_git = WORK / "prep" / "demo-git"
    shutil.copytree(ROOT / "tests/fixtures/inputs/demo", demo_git)
    for git_args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "demo input"]):
        host(["git", *git_args], cwd=demo_git, env=git_env())  # a fixed commit: the VM copy has the same revision
    texts = {
        **STATES,
        "input": DEMO_HOME.replace("@URL@", str(demo)),
        "gitinput": GIT_DEMO_HOME.replace("@URL@", str(demo_git)),
    }
    outputs: dict[str, Path] = {}
    paths: set[str] = set()
    nixpkgs = ""
    for name, text in texts.items():
        ws = WORK / "prep" / name
        host([str(minix / "bin/minix"), "init", "--dir", str(ws)], env=env)
        (ws / "home.py").write_text(text)
        stub = host([sys.executable, "-m", "minix_config.stub", str(ws)], env=env)
        host(["git", "add", "-A"], cwd=ws, env=env)
        nix("flake", "lock", cwd=ws)
        host(["git", "add", "-A"], cwd=ws, env=env)
        if name in INPUT_HOMES:
            (ws / ".basix").mkdir()
            nix("build", "--out-link", str(ws / ".basix" / "pynix"), ".#input-packages", cwd=ws)
            paths |= closure(ws / ".basix" / "pynix")
        compiler = Path(
            nix(
                "build",
                "--print-out-paths",
                "--out-link",
                str(WORK / "prep/links" / f"{name}-compiler"),
                ".#compiler",
                cwd=ws,
            ).stdout.split()[-1]
        )
        paths |= closure(compiler)
        host([str(compiler / "bin/minix-compile"), "home.py", "home.json"], cwd=ws)
        host(["git", "add", "-A"], cwd=ws, env=env)
        out = nix(
            "build",
            "--print-out-paths",
            "--out-link",
            str(WORK / "prep/links" / f"{name}-home"),
            f".#homeConfigurations.{USER}.activationPackage",
            cwd=ws,
        ).stdout.split()[-1]
        outputs[name] = Path(out)
        paths |= closure(Path(out))
        log(f"   prepared {name}: {out} {stub.stdout.strip()}")
        if not nixpkgs:
            tree = json.loads(
                nix("flake", "archive", "--json", "--dry-run", "--no-write-lock-file", ".", cwd=ws).stdout
            )
            nixpkgs = tree["inputs"]["minix"]["inputs"]["nixpkgs"]["path"]
    return outputs, paths, nixpkgs


def closure(path: Path) -> set[str]:
    return set(host(["nix-store", "-qR", str(path.resolve())]).stdout.split())


def build_vm(minix: Path, nixpkgs: str) -> Path:
    pub = (WORK / "key.pub").read_text().strip()
    args = [
        "build",
        "--file",
        str(ROOT / "nix/vm.nix"),
        "--out-link",
        str(WORK / f"vm-{HOST}"),
        "--argstr",
        "nixpkgs",
        nixpkgs,
        "--argstr",
        "minix",
        str(minix.resolve()),
        "--argstr",
        "testKey",
        pub,
        "--argstr",
        "host",
        HOST,
    ]
    if HOST == "basix":
        flake = os.environ.get("BASIX_FLAKE")
        if not flake:
            raise SystemExit("MINIX_VM_HOST=basix needs BASIX_FLAKE (a flake reference of the Basix v2 flake)")
        args += ["--argstr", "basixFlake", flake, "--impure"]
    nix(*args)
    return WORK / f"vm-{HOST}"


# --- the VM ----------------------------------------------------------------------------------------------------
class Remote:
    """One remote command: its exit code, its output, and its wall time."""

    def __init__(self, rc: int, out: str, wall: float) -> None:
        self.rc, self.out, self.wall = rc, out, wall

    @property
    def lines(self) -> list[str]:
        return [line for line in self.out.splitlines() if line.strip()]

    def fields(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.out.splitlines() if "=" in line)


def sh(command: str, user: str = USER, timeout: int = 1500, title: str | None = None, show: bool = True) -> Remote:
    """Run a command in the VM over the test channel, in a login-like session of `user`."""
    started = time.time()
    wrapped = "bash -l -c " + shlex.quote(command)  # a login shell, as a learner has: the PATH and the home hook
    done = subprocess.run(
        ["ssh", *SSHOPT, f"{user}@127.0.0.1", wrapped], capture_output=True, text=True, timeout=timeout, input=""
    )
    result = Remote(done.returncode, done.stdout + done.stderr, time.time() - started)
    if show:
        log(f"$ [{user}] {title or command}   (rc={result.rc}, {result.wall:.1f} s)")
        for line in result.out.rstrip().splitlines()[-40:]:
            log("    " + line)
    return result


def put(path: str, text: str) -> None:
    done = subprocess.run(
        ["ssh", *SSHOPT, f"{USER}@127.0.0.1", f'mkdir -p "$(dirname ~/{path})" && cat > ~/{path}'],
        input=text,
        text=True,
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr


def wait_ssh(timeout: int = 300) -> None:
    end = time.time() + timeout
    while time.time() < end:
        done = subprocess.run(["ssh", *SSHOPT, "root@127.0.0.1", "true"], capture_output=True, text=True)
        if done.returncode == 0:
            return
        time.sleep(3)
    raise SystemExit("ssh never came up")


def minix(args: str = "apply", label: str = "", env: str = "", where: str = "~/minix", then: str = "") -> Remote:
    """Run `minix` as the learner in `where`. `setpriv --no-new-privs` makes a setuid program (sudo) useless for it.

    `then` is a shell text that runs in the same session after the command, with the exit code in `$?`.
    """
    command = f"cd {where} && {env}setpriv --no-new-privs minix {args}"
    result = sh(command + (f"; echo RC=$?; {then}" if then else ""), title=f"minix {args}  [{label}] in {where}")
    RESULTS["times"][label or args] = round(result.wall, 1)
    return result


PROBE = r"""
cd {where} 2>/dev/null
P=$HOME/.local/state/nix/profiles
echo commits=$(git rev-list --count HEAD 2>/dev/null || echo 0)
echo head=$(git log -1 --format=%s 2>/dev/null)
echo dirty=$(git status --short | tr '\n' ';')
echo gen=$(readlink -f $P/home-manager 2>/dev/null)
echo ngens=$(ls -d $P/home-manager-*-link 2>/dev/null | wc -l)
echo links=$(for l in $P/home-manager-*-link; do readlink -f $l; done 2>/dev/null | sort -u | tr '\n' ' ')
echo web=$(systemctl --user is-active web.service 2>&1)
echo api=$(systemctl --user is-active api.service 2>&1)
echo pid=$(systemctl --user show web.service -p MainPID --value 2>/dev/null)
echo http=$(curl -s -o /dev/null -w %{{http_code}} -m 3 http://127.0.0.1:8080/ 2>/dev/null || echo none)
echo failed=$(systemctl --user --failed --no-legend --plain 2>/dev/null | wc -l)
echo homejson=$(sha256sum home.json 2>/dev/null | cut -c1-16)
"""


def state(where: str = "~/minix", show: bool = True) -> dict[str, str]:
    fields = sh(PROBE.format(where=where), show=False).fields()
    if show:
        log(
            "   STATE: "
            + "; ".join(f"{k}={v}" for k, v in fields.items() if k not in {"gen"})
            + f"; gen={fields.get('gen', '')[-30:]}"
        )
    return fields


# --- the cases -------------------------------------------------------------------------------------------------
def case_env() -> None:
    r = sh("id -un; nix --version; nproc; free -m | sed -n 2p", title="facts")
    check("env", "the account is the learner", USER, r.lines[0])
    flags = sh("grep experimental-features /etc/nix/nix.conf; true", show=False).out
    what = "Nix flakes are on by default" if BASIX else "no Nix setting turns flakes on"
    check("env", what, BASIX, "flakes" in flags)
    net = sh("curl -sS -m 5 https://cache.nixos.org/nix-cache-info", show=False)
    check("env", "the VM has no network", True, net.rc != 0)
    linger = sh(f"loginctl show-user {USER} -p Linger --value 2>&1", user="root", show=False).out.strip()
    if BASIX:
        check("env", "the learner lingers (the core sets it)", "yes", linger)
    else:
        check("env", "the learner does not linger", True, linger in {"no", ""} or "not logged in" in linger.lower())
    flake = sh("nix flake metadata path:/tmp 2>&1 | tail -3", show=False)
    refused = "experimental" in flake.out.lower()
    check("env", "a flake command without the features " + ("works" if BASIX else "fails"), not BASIX, refused)
    session = sh(
        "echo XDG=$XDG_RUNTIME_DIR; systemctl --user is-system-running 2>&1", title="user manager in a session"
    )
    info("env", "the user manager in a session", session.out.strip())


def case_init() -> None:
    before = sh("minix --version", show=False).out.strip()
    check("init", "the command runs and names its version", True, before.startswith("minix "))
    r = minix("init", "init", where="~")
    check("init", "init exits 0", 0, r.rc)
    check(
        "init",
        "init says what it made and what comes next",
        ["created ~/minix", "next: edit ~/minix/home.py, then run: minix apply"],
        r.lines,
    )
    files = sh("cd ~/minix && LC_ALL=C ls -A | grep -v '^.git$' | LC_ALL=C sort | tr '\\n' ' '", show=False).out.strip()
    check(
        "init",
        "the folder holds the stub, the lock, home.py, and .gitignore",
        ".gitignore flake.lock flake.nix home.py",
        files,
    )
    entry = sh("cat ~/minix/home.py", show=False).out
    check("init", "home.py names the running account", True, f'user="{USER}"' in entry)
    check("init", "home.py is the written entry", STATES["init"], entry)
    check(
        "init", ".gitignore holds .basix/ and result", ".basix/\nresult\n", sh("cat ~/minix/.gitignore", show=False).out
    )
    first = sh("head -1 ~/minix/flake.nix", show=False).out.strip()
    check("init", "the stub is managed", "# Managed by minix. Do not edit.", first)
    git = sh("cd ~/minix && git log --format=%s; git status --porcelain", show=False)
    check("init", "one commit, a clean tree", ["minix init"], git.lines)
    # G9: the lock came from the package. Nix accepts it offline and changes nothing.
    sh("cp ~/minix/flake.lock /tmp/flake.lock.init", show=False)
    lock = sh(
        "cd ~/minix && nix --extra-experimental-features 'nix-command flakes' flake lock --offline 2>&1; echo rc=$?",
        title="nix flake lock --offline",
    )
    check("init", "nix flake lock --offline accepts the lock", "0", lock.fields().get("rc"))
    same = sh("cmp ~/minix/flake.lock /tmp/flake.lock.init && echo same", show=False).out.strip()
    check("init", "the offline lock leaves flake.lock as init wrote it", "same", same)
    dirty = sh("cd ~/minix && git status --porcelain", show=False).out.strip()
    check("init", "the tree stays clean", "", dirty)
    editor = sh("bash -lc 'echo EDITOR=$EDITOR'", show=False).out.strip()
    info("init", "EDITOR in a login shell before any apply", editor)
    refused = minix("init", "init again", where="~")
    check("init", "init refuses a folder that is not empty", 1, refused.rc)
    check(
        "init",
        "the refusal line",
        ["~/minix exists and is not empty. minix init makes a new folder; give another with --dir."],
        refused.lines,
    )


def path_after_first_apply(generation: str) -> None:
    """After the first apply, a login shell finds `minix` in the profile of the home, not in the system (L113, 010-D21).

    The home holds the `minix` of its own lock (`home.packages`). The user profile comes before `/run/current-system/sw`
    on `PATH`, so this command takes over from the one that the machine carries. The check compares the file that
    `command -v` names with the `home-path` of the generation, because both commands may be one store path.
    """
    found = sh("command -v minix", show=False).out.strip()
    info("apply", "command -v minix in a login shell after the first apply", found)
    check("apply", "command -v minix is not the system command", False, found.startswith("/run/current-system/"))
    check("apply", "command -v minix names the profile of the home", True, found.startswith(f"/home/{USER}/"))
    resolved = sh("readlink -f $(command -v minix)", show=False).out.strip()
    home_path = sh(f"readlink -f {shlex.quote(generation)}/home-path/bin/minix", show=False).out.strip()
    check("apply", "command -v minix leads to the minix of the home-path of the generation", home_path, resolved)
    check("apply", "that file is a minix command", True, resolved.endswith("/bin/minix"))


def case_apply() -> None:
    put("minix/home.py", STATES["svc"])
    r = minix(
        "apply",
        "first apply, no lingering",
        then="curl -s -o /dev/null -w HTTP=%{http_code} -m 5 http://127.0.0.1:8080/",
    )
    fields = r.fields()
    check("apply", "the first apply exits 0", "0", fields.get("RC"))
    warnings = [line for line in r.lines if "lingering is off" in line]
    if BASIX:
        lingering = [x for x in r.lines if "linger" in x]
        check("apply", "it prints no lingering warning (the core sets lingering)", [], lingering)
    else:
        check(
            "apply",
            "it warns once that services stop at logout",
            [
                f"warning: lingering is off for {USER}, so your user services stop when you log out. "
                f"To keep them running, ask an administrator to run: loginctl enable-linger {USER}"
            ],
            warnings,
        )
        check("apply", "the warning comes first", True, r.lines[:1] == warnings)
    check("apply", "the units are healthy", True, "applied: the home runs, and its units are healthy" in r.lines)
    check(
        "apply",
        "the lines end with the save",
        "saved: home.json, home.py",
        r.lines[r.lines.index("RC=0") - 1] if "RC=0" in r.lines else "",
    )
    minix_lines = r.lines[: r.lines.index("RC=0")] if "RC=0" in r.lines else r.lines
    check(
        "apply",
        "the lines name no store path and no git command",
        False,
        any("/nix/store" in x or "git " in x for x in minix_lines),
    )
    check("apply", "the service answers in the session of the apply", "200", fields.get("HTTP"))
    s = state()
    check("apply", "one more commit", "2", s["commits"])
    check("apply", "the commit is the apply", "apply: home.json, home.py", s["head"])
    check("apply", "the tree is clean", "", s["dirty"])
    path_after_first_apply(s["gen"])
    time.sleep(20)
    gone = sh(f"pgrep -u {USER} -f 'http.server 8080' | wc -l", user="root", show=False).out.strip()
    if BASIX:
        check("apply", "after logout the service still runs (lingering is on)", True, int(gone) >= 1)
    else:
        check("apply", "after logout the service is gone (the warning is true)", "0", gone)
        # Lingering on, as the warning says.
        sh(f"loginctl enable-linger {USER}", user="root", title="loginctl enable-linger")
    answer = "none"
    for _ in range(30):
        answer = sh("curl -s -o /dev/null -w %{http_code} -m 3 http://127.0.0.1:8080/ || true", show=False).out.strip()
        if answer == "200":
            break
        time.sleep(2)
    check("apply", "with lingering the service answers with no login", "200", answer)
    before = state()
    r = minix("apply", "second apply")
    check("apply", "the second apply exits 0", 0, r.rc)
    check("apply", "it prints nothing to change", True, "nothing to change" in r.lines)
    check("apply", "it prints no lingering warning now", [], [line for line in r.lines if "lingering" in line])
    s = state()
    check("apply", "no new commit", before["commits"], s["commits"])
    check("apply", "the same generation", before["gen"], s["gen"])
    check("apply", "the same service process", before["pid"], s["pid"])
    check("apply", "the service answers", "200", s["http"])


def case_editor() -> None:
    r = sh("bash -lc 'echo EDITOR=$EDITOR'", show=False)
    check("editor", "a login shell has EDITOR set after the apply", "EDITOR=ed", r.out.strip())


def case_account() -> None:
    before = state()
    put("minix/home.py", STATES["svc"].replace(f'user="{USER}"', 'user="mallory"'))
    r = minix("apply", "wrong account")
    check("account", "the apply stops with exit 2", 2, r.rc)
    check(
        "account",
        "one line names the account",
        [
            'home.py names the account "mallory", but you are "learner". '
            'minix changes only your own home. Set user="learner" in home.py.'
        ],
        r.lines,
    )
    s = state()
    for key in ("gen", "commits", "pid", "homejson"):
        check("account", f"{key} did not change", before[key], s[key])
    check("account", "only the edit of home.py is in the tree", "M home.py;", s["dirty"])
    sh("cd ~/minix && git checkout -q home.py", show=False)


def case_fail() -> None:
    before = state()
    put("minix/home.py", STATES["bad"])
    r = minix("apply", "failing service")
    check("fail", "the apply exits 3", 3, r.rc)
    text = "\n".join(r.lines)
    check("fail", "the lines name the failed unit", True, "api.service" in text)
    check("fail", "the earlier home is back", True, any("the earlier home is back" in line for line in r.lines))
    check("fail", "nothing was saved", True, "Nothing was saved." in text)
    s = state()
    check("fail", "the generation is the earlier one", before["gen"], s["gen"])
    check("fail", "no link of the profile points to the failed home", False, str(OUTPUTS["bad"]) in s["links"].split())
    check("fail", "no commit", before["commits"], s["commits"])
    check("fail", "the edit stays in the folder", True, "home.py" in s["dirty"])
    check("fail", "the earlier service runs and answers", "200", s["http"])
    check("fail", "no failed unit is left", "0", s["failed"])
    check("fail", "the failing service is not active", True, s["api"] != "active")
    # MINIX_ON_FAIL=keep: the new activation stays.
    r = minix("apply", "failing service, keep", env="MINIX_ON_FAIL=keep ")
    check("fail", "keep: the apply exits 3", 3, r.rc)
    check(
        "fail",
        "keep: the lines say that the new home runs",
        True,
        any("the new home is running" in line for line in r.lines),
    )
    k = state()
    check("fail", "keep: the generation is a new one", True, k["gen"] != before["gen"])
    check("fail", "keep: no commit", before["commits"], k["commits"])
    unit = sh("systemctl --user cat api.service > /dev/null; echo rc=$?", show=False).fields().get("rc")
    check("fail", "keep: the new home keeps the failing unit (it restarts in a loop)", "0", unit)


def case_undo() -> None:
    # The tree is dirty: the bad edit, with the bad home running (keep). Undo drops the edit and applies the last save.
    start = state()
    r = minix("undo", "undo, dirty tree")
    check("undo", "the undo of a dirty tree exits 0", 0, r.rc)
    check(
        "undo",
        "it says that it dropped the edit",
        True,
        any(line.startswith("undo: dropped the edit") for line in r.lines),
    )
    s = state()
    check("undo", "the tree is clean", "", s["dirty"])
    check("undo", "no new commit", start["commits"], s["commits"])
    check("undo", "the home of the last save runs again", "200", s["http"])
    check("undo", "the failing service is gone", True, s["api"] != "active" and s["failed"] == "0")
    dropped = sh("cd ~/minix && git for-each-ref refs/minix | wc -l", show=False).out.strip()
    check("undo", "the dropped edit stays in the workspace history", True, int(dropped) >= 1)
    saved = sh("cat ~/minix/home.py", show=False).out
    check("undo", "home.py is the file of the last save", STATES["svc"], saved)
    # Clean tree: the files of the save before the last one.
    r = minix("undo", "undo, clean tree")
    check("undo", "the undo of a clean tree exits 0", 0, r.rc)
    check(
        "undo", "it says what it restores", True, "undo: restoring the files of the save before the last one" in r.lines
    )
    s2 = state()
    check("undo", "one new commit", str(int(s["commits"]) + 1), s2["commits"])
    check("undo", "the commit is an undo", True, s2["head"].startswith("undo to "))
    check("undo", "home.py is the first entry again", STATES["init"], sh("cat ~/minix/home.py", show=False).out)
    check("undo", "the service is gone", True, s2["http"] != "200")
    # The toggle: undo again brings the service back.
    r = minix("undo", "undo, the toggle")
    check("undo", "the toggle exits 0", 0, r.rc)
    s3 = state()
    check("undo", "the toggle makes one more commit", str(int(s2["commits"]) + 1), s3["commits"])
    check("undo", "home.py is the service entry again", STATES["svc"], sh("cat ~/minix/home.py", show=False).out)
    check("undo", "the service answers again", "200", s3["http"])
    # One save only.
    made = minix("init --dir ~/solo", "init solo", where="~")
    check("undo", "init --dir makes another workspace", 0, made.rc)
    r = minix("undo", "undo, one save", where="~/solo")
    check("undo", "one save: exit 1", 1, r.rc)
    check("undo", "one save: the refusal line", ["There is only one save. There is nothing to undo."], r.lines)


def case_rollback() -> None:
    """011 P3, finding 1: the rollback itself fails. The profile stays at the new home, so a mark must make the next apply switch."""
    start = state()
    earlier = start["gen"]
    check("rollback", "the saved home runs before the case", "200", start["http"])
    put("minix/home.py", STATES["bad"])
    script = "/var/tmp/fail-activate"
    sh(
        f"printf %s {shlex.quote(FAIL_ACTIVATE)} > {script} && chmod 755 {script} && "
        f"mount --bind {script} {shlex.quote(earlier)}/activate",
        user="root",
        title="make the activate script of the earlier home fail (bind mount)",
    )
    try:
        probe = sh(f"{shlex.quote(earlier)}/activate; echo rc=$?", user="root", show=False)
        check("rollback", "the activate script of the earlier home fails", "7", probe.fields().get("rc"))
        r = minix("apply", "failing service, failing rollback")
    finally:
        sh(f"umount {shlex.quote(earlier)}/activate", user="root", title="restore the activate script")
    check("rollback", "the apply exits 3", 3, r.rc)
    check(
        "rollback",
        "it prints the failed-rollback line",
        True,
        any(line.startswith("failed: tried to roll back, but that also failed (exit 7).") for line in r.lines),
    )
    check(
        "rollback",
        "the line says that the next apply switches again",
        True,
        any("The next minix apply switches again." in line for line in r.lines),
    )
    mark = sh(f"test -e {UNSETTLED} && echo yes || echo no", show=False).out.strip()
    check("rollback", "the mark home-manager.unsettled exists", "yes", mark)
    s = state()
    check("rollback", "the profile holds the new home (the rollback did not move it)", str(OUTPUTS["bad"]), s["gen"])
    check("rollback", "no commit", start["commits"], s["commits"])
    # The next apply builds the home that the profile already holds. Without the mark it skips the switch.
    again = minix("apply", "failing service again, after the failed rollback")
    text = "\n".join(again.lines)
    check("rollback", "the next apply does not say that the home is the same", False, "only files changed" in text)
    check("rollback", "the next apply does not say nothing to change", False, "nothing to change" in text)
    check(
        "rollback",
        "the next apply switches and finds the failed unit",
        True,
        "problem: failed units: api.service" in text,
    )
    check("rollback", "the next apply exits 3", 3, again.rc)
    still = sh(f"test -e {UNSETTLED} && echo yes || echo no", show=False).out.strip()
    check("rollback", "the mark stays while no good rollback happened", "yes", still)
    # A good home settles the profile and clears the mark.
    sh("cd ~/minix && git checkout -q HEAD -- .", show=False)  # the failed apply staged the edit, so name HEAD
    fixed = minix("apply", "the saved home again")
    check("rollback", "the saved home applies with exit 0", 0, fixed.rc)
    check("rollback", "it says that the home runs", True, any(line.startswith("applied:") for line in fixed.lines))
    gone = sh(f"test -e {UNSETTLED} && echo yes || echo no", show=False).out.strip()
    check("rollback", "the mark is gone", "no", gone)
    end = state()
    check("rollback", "the saved home runs and answers", "200", end["http"])
    check("rollback", "no failed unit is left", "0", end["failed"])
    check("rollback", "the failing service is not active", True, end["api"] != "active")
    check("rollback", "the tree is clean", "", end["dirty"])
    check("rollback", "no commit", start["commits"], end["commits"])


def case_basix() -> None:
    sh("rm -rf ~/bx && mkdir ~/bx && cd ~/bx && git init -q && touch system.py", show=False)
    r = minix("apply", "Basix workspace", where="~/bx")
    check("basix", "the command stops with exit 1", 1, r.rc)
    check(
        "basix",
        "the lines name basix apply",
        [
            "~/bx is a Basix workspace. minix does not change it.",
            "To change the machine, run: basix apply",
            "To change your own home, go to ~/minix and run: minix apply",
        ],
        r.lines,
    )


def case_input(demo_text: str, demo_dir: Path) -> None:
    sh("rm -rf ~/demo-input", show=False)
    tar = subprocess.run(["tar", "-C", str(demo_dir), "-cf", "-", "."], capture_output=True)
    done = subprocess.run(
        ["ssh", *SSHOPT, f"{USER}@127.0.0.1", "mkdir -p ~/demo-input && tar -C ~/demo-input -xf -"],
        input=tar.stdout,
        capture_output=True,
    )
    check("input", "the demo input is copied to the VM", 0, done.returncode)
    made = minix("init --dir ~/withinput", "init with input", where="~")
    check("input", "init makes the workspace", 0, made.rc)
    put("withinput/home.py", demo_text)
    before = state("~/withinput")
    r = minix("apply", "Input on Home Manager", where="~/withinput")
    check("input", "the apply exits 0", 0, r.rc)
    saved = r.lines[-1] if r.lines else ""
    check(
        "input",
        "the lines end with the save of the stub, the lock, the compiled file, and the entry",
        "saved: flake.lock, flake.nix, home.json, home.py",
        saved,
    )
    log_text = sh("cat ~/withinput/.git/minix/last.log", show=False).out
    check("input", "apply built the input packages", True, ".#input-packages" in log_text)
    stub = sh("cat ~/withinput/flake.nix", show=False).out
    check("input", "the stub holds the input", True, 'inputs.demo.url = "path:/home/learner/demo-input";' in stub)
    check("input", "the input follows nixpkgs", True, 'inputs.demo.inputs.nixpkgs.follows = "nixpkgs";' in stub)
    check(
        "input",
        "the input follows Home Manager of minix",
        True,
        'inputs.demo.inputs.home-manager.follows = "minix/home-manager";' in stub,
    )
    lock = sh("cat ~/withinput/flake.lock", show=False).out
    check("input", "the lock holds the input", True, '"demo"' in lock and '"narHash"' in lock)
    classes = sh("ls ~/withinput/.basix/pynix", show=False).out.split()
    check("input", "the input package pynix_demo exists", ["pynix_demo"], classes)
    greeting = sh("cat ~/.config/demo/greeting", show=False).out
    check("input", "the module of the input wrote its file with the typed values", "hi x2\n", greeting)
    s = state("~/withinput")
    check("input", "the apply saved a commit", str(int(before["commits"]) + 1), s["commits"])
    check("input", "the tree is clean and .basix stays out of git", "", s["dirty"])
    again = minix("apply", "Input, second apply", where="~/withinput")
    check("input", "a second apply prints nothing to change", True, "nothing to change" in again.lines)
    log_again = sh("cat ~/withinput/.git/minix/last.log", show=False).out
    check("input", "a second apply builds the input packages again (010-D13)", True, ".#input-packages" in log_again)


def case_gitinput(text: str, repo_dir: Path) -> None:
    """011 P3, finding 2: the folder of a `git+file` input is gone. The VM has no network, so the names must come from the store."""
    sh("rm -rf ~/demo-git ~/demo-git.away ~/gitinput", show=False)
    tar = subprocess.run(
        ["tar", "-C", str(repo_dir), "-cf", "-", "."], capture_output=True
    )  # with .git: the same revision
    done = subprocess.run(
        ["ssh", *SSHOPT, f"{USER}@127.0.0.1", "mkdir -p ~/demo-git && tar -C ~/demo-git -xf -"],
        input=tar.stdout,
        capture_output=True,
    )
    check("gitinput", "the git folder of the demo input is copied to the VM", 0, done.returncode)
    made = minix("init --dir ~/gitinput", "init with git input", where="~")
    check("gitinput", "init makes the workspace", 0, made.rc)
    put("gitinput/home.py", text)
    where = "~/gitinput"
    r = minix("apply", "Input from a git+file folder", where=where)
    check("gitinput", "the first apply exits 0", 0, r.rc)
    stub = sh(f"cat {where}/flake.nix", show=False).out
    follows = [
        'inputs.demo.url = "git+file:///home/learner/demo-git";',
        'inputs.demo.inputs.nixpkgs.follows = "nixpkgs";',
        'inputs.demo.inputs.home-manager.follows = "minix/home-manager";',
    ]
    for line in follows:
        check("gitinput", f"the stub holds: {line}", True, line in stub)
    lock = json.loads(sh(f"cat {where}/flake.lock", show=False).out)
    locked = lock["nodes"].get("demo", {}).get("locked", {})
    check(
        "gitinput",
        "the lock holds the input as a git tree with a hash",
        ("git", True),
        (locked.get("type"), "narHash" in locked),
    )
    before = state(where)
    sums = f"cd {where} && sha256sum flake.nix flake.lock"
    sums_before = sh(sums, show=False).out
    sh("mv ~/demo-git ~/demo-git.away", show=False)
    gone = sh("test -e ~/demo-git && echo yes || echo no", show=False).out.strip()
    check("gitinput", "the input folder is moved away", "no", gone)
    again = minix("apply", "git input, folder gone", where=where)
    check("gitinput", "the apply with the folder gone exits 0", 0, again.rc)
    check("gitinput", "it prints nothing to change", True, "nothing to change" in again.lines)
    stub_after = sh(f"cat {where}/flake.nix", show=False).out
    for line in follows:
        check("gitinput", f"the stub keeps: {line}", True, line in stub_after)
    check("gitinput", "the stub is the same", stub, stub_after)
    check("gitinput", "flake.nix and flake.lock keep their hashes", sums_before, sh(sums, show=False).out)
    after = state(where)
    check("gitinput", "no new commit", before["commits"], after["commits"])
    check("gitinput", "the tree is clean", "", after["dirty"])


def ssh_options() -> list[str]:
    """The ssh options of the test channel.

    `-F /dev/null`: read no ssh config file. Under Testee the store files show as owned by `nobody`, and ssh
    refuses the system file that the host config includes ("Bad owner or permissions"). Every option is given here.
    """
    return [
        "-F", "/dev/null", "-p", PORT, "-i", str(KEY), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
    ]  # fmt: skip


# --- the run ---------------------------------------------------------------------------------------------------
def main() -> int:
    global RAW, SSHOPT
    parser = argparse.ArgumentParser(description="The VM check opt:minix-apply.")
    parser.add_argument("--cases", default=",".join(GROUPS), help="comma list of: " + ", ".join(GROUPS))
    parser.add_argument("--keep-vm", action="store_true", help="debug: leave the VM running at the end")
    parser.add_argument(
        "--prepare-only", action="store_true", help="debug: build the package, the homes, and the VM only"
    )
    args = parser.parse_args()
    groups = args.cases.split(",")
    WORK.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    RAW = open(LOGS / "apply_vm.log", "w")
    if not (WORK / "key").exists():
        host(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(WORK / "key"), "-C", "minix-apply"])
    SSHOPT = ssh_options()
    started = time.time()
    if subprocess.run(["ssh", *SSHOPT, "root@127.0.0.1", "true"], capture_output=True).returncode == 0:
        raise SystemExit(f"port {PORT} already answers: a VM from an earlier run is still alive")
    log(f"== host {HOST}; groups {groups}; work {WORK}; logs {LOGS}")
    source = prepare_source()
    minix_pkg = build_minix(source)
    share = json.loads((minix_pkg / "share/minix/source.json").read_text())
    log(f"== minix {minix_pkg}; source {share}")
    outputs, paths, nixpkgs = prepare_homes(minix_pkg)
    OUTPUTS.update(outputs)
    vm_dir = build_vm(minix_pkg, nixpkgs)
    log(f"== prepared in {time.time() - started:.0f} s; {len(paths)} store paths to copy")
    runner = next((vm_dir / "bin").glob("run-*-vm"))
    if args.prepare_only:
        return 0
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
        wait_ssh()
        log(f"== VM up after {time.time() - started:.0f} s")
        ssh_env = {**os.environ, "NIX_SSHOPTS": " ".join(SSHOPT)}
        t = time.time()
        prep = WORK / "prep" / "input"
        # A source that the host fetched itself has no signature, so the VM takes it only with `--no-check-sigs`.
        done = subprocess.run(
            ["nix", *FEATURES, "flake", "archive", "--no-check-sigs", "--to", "ssh-ng://root@127.0.0.1", "--no-write-lock-file", str(prep)],
            env=ssh_env, capture_output=True, text=True,
        )  # fmt: skip
        log(
            f"== nix flake archive (the input sources): rc={done.returncode}, {time.time() - t:.0f} s\n{done.stderr[-300:]}"
        )
        if done.returncode != 0:
            raise SystemExit("nix flake archive failed: the VM has no input sources, so every case would fail offline")
        done = subprocess.run(
            ["nix", *FEATURES, "flake", "archive", "--no-check-sigs", "--to", "ssh-ng://root@127.0.0.1", "--no-write-lock-file", str(WORK / "prep" / "gitinput")],
            env=ssh_env, capture_output=True, text=True,
        )  # fmt: skip
        log(f"== nix flake archive (the git input sources): rc={done.returncode}\n{done.stderr[-300:]}")
        if done.returncode != 0:
            raise SystemExit(
                "nix flake archive failed: the VM has no git input sources, so every case would fail offline"
            )
        t = time.time()
        done = subprocess.run(
            ["nix", *FEATURES, "copy", "--no-check-sigs", "--to", "ssh-ng://root@127.0.0.1", *sorted(paths)],
            env=ssh_env, capture_output=True, text=True,
        )  # fmt: skip
        log(f"== nix copy of {len(paths)} paths: rc={done.returncode}, {time.time() - t:.0f} s\n{done.stderr[-300:]}")
        if done.returncode != 0:
            raise SystemExit("nix copy failed")
        RESULTS["times"]["prepare and stage"] = round(time.time() - started, 1)
        demo_dir = ROOT / "tests/fixtures/inputs/demo"
        cases = {
            "env": case_env,
            "init": case_init,
            "apply": case_apply,
            "editor": case_editor,
            "account": case_account,
            "fail": case_fail,
            "undo": case_undo,
            "rollback": case_rollback,
            "basix": case_basix,
            "input": lambda: case_input(DEMO_HOME.replace("@URL@", f"/home/{USER}/demo-input"), demo_dir),
            "gitinput": lambda: case_gitinput(
                GIT_DEMO_HOME.replace("@URL@", f"/home/{USER}/demo-git"), WORK / "prep" / "demo-git"
            ),
        }
        for group in GROUPS:
            if group not in groups:
                continue
            log(f"\n== case {group}")
            try:
                cases[group]()
            except Exception as error:  # a crashed case must not stop the session
                import traceback

                check(group, f"the case crashed: {type(error).__name__}", "no crash", str(error)[:300], ok=False)
                log(traceback.format_exc())
    finally:
        if vm is not None and not args.keep_vm:
            subprocess.run(["ssh", *SSHOPT, "root@127.0.0.1", "poweroff"], capture_output=True)
            try:
                vm.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(vm.pid, signal.SIGTERM)
    failed = [c for c in RESULTS["checks"] if not c["ok"]]
    RESULTS["total_s"] = round(time.time() - started, 1)
    (LOGS / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n")
    log(
        f"\n{len(RESULTS['checks']) - len(failed)} of {len(RESULTS['checks'])} checks ok, {len(failed)} failed, {RESULTS['total_s']} s"
    )
    for item in failed:
        log(
            f"FAIL [{item['case']}] {item['check']}: expected {item['expected']!r}, actual {str(item['actual'])[:200]!r}"
        )
    log("PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
