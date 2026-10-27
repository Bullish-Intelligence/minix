# AGENTS.md — project instructions

This repository is a Python package. `AGENTS.md` holds its contributor
instructions, and `CLAUDE.md` is a symlink to this file.

## Checks

- Run Python commands in the `devenv` shell.
- `testee verify` runs the tasks declared in `devenv.nix`.
- `testee verify --profile quick` runs `check:lint` and `check:format`.
- `testee verify test:full` runs the test suite.
- If formatting fails, run `ruff format src tests`, then rerun the checks.
- Keep `uv.lock` and `devenv.lock` in Git. The machine supplies the `testee` command.
  This project pins no Testee input and sets no `testee.*` option.

## Project configuration

- `pyproject.toml` declares the package and its development dependencies.
- `devenv.nix` declares the shell and the tasks that Testee runs.
- `flake.nix` builds the package with uv2nix from `uv.lock`.
- Zellij comes from the `pyllij` flake input, pinned by revision in `flake.nix`. Do not set another Zellij package.
  To move Zellij, change pyllij, then bump the `pyllij` input and `ZELLIJ_VERSION` in `scripts/check_nix.py`.
- For tool workflows, use the skills linked under `.agents/skills/`.
