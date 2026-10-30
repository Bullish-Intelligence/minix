# minix

minix is the home layer of Basix v2. One person writes a `home.py` file in typed Python, on
generated Home Manager classes. minix compiles it to `home.json` and applies it as a standalone
Home Manager configuration. The `minix` command has four verbs: `init`, `apply`, `undo`, and
`update`. minix runs on any Linux host with Nix and a systemd user manager, with or without Basix.
The workspace is `~/minix` by default. If you keep it elsewhere, set `MINIX_WORKSPACE` to its folder. `minix init` makes that folder (and its parents) too.

This repository holds only the empty scaffold. The `minix` command prints its help and its version.

## Run-time contracts with a Basix host

minix emits Home Manager only. Basix emits NixOS only. Neither layer needs the other to build.
At run time, a home may use a thing of a Basix host only when it skips or falls back cleanly without it.

- **Linger.** The Basix core turns linger on for the account. With linger, user services and user timers run with
  no login. Without it, they run only while the person is logged in. When linger is off, `minix apply` prints a
  warning and names `loginctl enable-linger USER`. It still applies the home.
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
