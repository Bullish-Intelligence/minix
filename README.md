# minix

minix is the home layer of Basix v2. One person writes a `home.py` file in typed Python, on
generated Home Manager classes. minix compiles it to `home.json` and applies it as a standalone
Home Manager configuration. The `minix` command has four verbs: `init`, `apply`, `undo`, and
`update`. minix runs on any Linux host with Nix and a systemd user manager, with or without Basix.
The workspace is `~/minix` by default. If you keep it elsewhere, set `MINIX_WORKSPACE` to its folder. `minix init` makes that folder (and its parents) too.

A workspace can be a git linked worktree, for example a gitman workspace. `apply`, `undo`, and `update` work in it.
Each worktree keeps its own log and build links in its git folder. All worktrees of one repository share one lock,
because they change one home. A save commit goes to the HEAD of the worktree that runs the command.

This repository holds only the empty scaffold. The `minix` command prints its help and its version.

## Run-time contracts with a Basix host

minix emits Home Manager only. Basix emits NixOS only. Neither layer needs the other to build.
At run time, a home may use a thing of a Basix host only when it skips or falls back cleanly without it.

- **Linger.** The Basix core turns linger on for the account. With linger, user services and user timers run with
  no login. Without it, they run only while the person is logged in. When linger is off, `minix apply` prints a
  warning and names `loginctl enable-linger USER`. It still applies the home.
- **Login shell.** `Zsh()` sets up zsh and its completion in the home. The system sets the login shell, not
  minix. `minix apply` prints a warning when the login shell is not zsh. A Basix host also runs a system
  `compinit`, unless `enableGlobalCompInit` is off in `system.py`.
- **Defaults in `/etc/xdg`.** The Basix desktop profile puts a default Hyprland file and a default Noctalia file
  there. A file in your home wins. `Hyprland` loads `/etc/xdg/hypr/hyprland.lua` when it exists, then your settings.
  Without it, `Hyprland` loads the base look of minix. `Noctalia` writes only the settings that you set.
- **Firewall rules for user ports.** A home service may listen on a port. Only the system layer opens the
  firewall for it: write a port in `system.py`. minix opens no port. Without that rule, only the machine itself
  reaches the port. On a host with no firewall, any host may reach it.

A home unit uses a system thing only behind a condition or a fallback. A failed `only_if_exists` condition skips the
unit. It does not fail the unit.

```python
home = Home(
    user="learner",
    state_version="26.11",
    services=[
        Service(
            "report",
            program=pkgs.python3,
            args="report.py",
            only_if_exists=["/run/postgresql"],  # a system folder: the unit is skipped without it
        ),
    ],
)
```

`after` and `wants` work for user units only. The user manager does not see system units. To wait for a system thing,
use a condition or a `pre_start` step that waits with a bound.

## More unit settings

`Service` has three more settings. Each writes one systemd key.

- `description="Text"` writes `Description=`. The default is `minix service NAME`. Write one line.
- `requires=["db"]` writes `Requires=`, like `wants`. A required unit that fails to start stops this service from
  starting. `requires` does not set the order, so add the same names to `after`.
- `environment_file="~/.config/app/env"` writes `EnvironmentFile=`. A leading `-` means the service also starts when the
  file is missing.

A socket unit goes in `options=[...]`. A `Service` orders itself after it with `after` and `requires`:

```python
from pynix_hm.systemd.user import User, User_Automounts_Value

home = Home(
    user="learner",
    services=[Service("daemon", program=pkgs.python3, args="serve.py", after=["daemon.socket"], requires=["daemon.socket"])],
    options=[User(sockets={"daemon": User_Automounts_Value(Socket={"ListenStream": "%t/daemon.sock"})})],
)
```

For a key that `Service` has no setting for, write a second piece for the same unit name in `options=[...]`. Home Manager
joins the two pieces. For example, `User(services={"daemon": User_Services_Value(Unit_X_SwitchMethod="keep-old")})` adds
`X-SwitchMethod=keep-old` to the `[Unit]` section of `daemon`. A key that both pieces set is an error.

## A package from a Nix file

`program` takes a package of the package list (`pkgs.NAME`) or a `Native` package. A `Native` package comes from a Nix file
in the workspace. `Service` and `Step` need `file=` with it, because minix cannot see the main program of the package.

```python
Service("daemon", program=Native("pkg.nix", ["daemon"]), file="daemon")
```

The file `pkg.nix` gives the package:

```nix
{ pkgs, ... }: { daemon = pkgs.writeShellScriptBin "daemon" "true"; }
```

## Develop

```bash
devenv shell          # enter the project environment
uv sync               # fill the virtual environment
testee verify         # run every check from the repository root
```

Run `testee verify` outside `devenv shell`. The `setup:sync` task runs `uv lock --check`.

## Build

```bash
nix build .#minix
./result/bin/minix --help
```

The flake builds the package from `uv.lock` with uv2nix.

## Zellij

`TerminalWorkspace` installs the Zellij of the `pyllij` flake input (`packages.<system>.zellij`), not the Zellij of
nixpkgs. pyllij defines and measures that package, so minix installs exactly the Zellij that pyllij tests against.
`lib/home.nix` fills the option `minix.zellijPackage` from the input, and the capability reads that option. To move
Zellij, change the package in pyllij first. Then bump the `pyllij` tag in `flake.nix` and run `nix flake update pyllij`.
The Nix check `zellij` in `scripts/check_nix.py` and `tests/test_zellij_source.py` guard the pin.

## Release

```bash
devenv shell -- scripts/release.sh v0.1.0-rc.1
```

The job needs a GREEN `testee status` for the commit at HEAD. It checks the locks and the pair of `pynix_hm` with
Home Manager and nixpkgs, writes `provenance/` and `CHANGELOG.md`, makes one release commit, and makes the annotated
tag. It never pushes. It prints the two push commands. The release commit needs a GREEN `testee verify` before you
push it.

## Contributing

Pull requests are welcome, but the author does not promise a review.

## Author

Bullish-Intelligence <242310653+Bullish-Intelligence@users.noreply.github.com>
