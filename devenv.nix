# minix: Python project configuration.
#
# Testee runs the task names below. It runs `setup:sync` once before the others.
{ pkgs, ... }:

let
  # The `pynix` source of the lock (010-D8). The Nix packages read `"${pynix}/src"`. The virtual environment reads the
  # same path through a `pynix.pth` file, so pytest, ty, and ruff see the same `pynix` as Nix, from one pin.
  # `uv.lock` does not list `pynix`: a git source would put an URL of the owner into that file.
  lock = builtins.fromJSON (builtins.readFile ./flake.lock);
  pynix = builtins.fetchTree lock.nodes.pynix.locked;

  # Files that no check reads: records, notes, and the release files. A change to only these reuses a verdict (Testee).
  unread = [ "CHANGELOG.md" "provenance/**" "AGENTS.md" "CLAUDE.md" ".copyroom-local.json" ];
  # The two VM scripts are run by the `opt:*` tasks only. The required checks do not read them.
  vmScripts = [ "scripts/apply_vm.py" "scripts/update_vm.py" ];

  # Write `pynix.pth` into the site-packages folder of the virtual environment. Run it after each `uv sync`.
  wirePynix = ''
    venv="''${VIRTUAL_ENV:-''${DEVENV_STATE:-.devenv/state}/venv}"
    if [ -x "$venv/bin/python" ]; then
      site="$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
      mkdir -p "$site"
      printf '%s\n' "${pynix}/src" > "$site/pynix.pth"
    fi
  '';
in
{
  # Python 3.13: the interpreter of the Nix package and of the compiler (008-Q7).
  languages.python = {
    enable = true;
    package = pkgs.python313;
    venv.enable = true;
    uv.enable = true;
  };

  enterShell = wirePynix;

  # Every task declares `input.testee`, so Testee can reuse a verdict when a commit changes none of its inputs.
  # After an edit of this table, run `testee doctor`: it must show reuse on for every task.
  tasks = {
    # Fail when uv.lock is stale, then fill the virtual environment from it and wire in `pynix`.
    # `uv sync` reads the lock, the project file, and the readme (`pyproject.toml` names it). The `pynix` link reads
    # `flake.lock` (see the `let` block).
    "setup:sync".input.testee.reads = [ "uv.lock" "pyproject.toml" "README.md" ".gitignore" "flake.lock" ];
    "setup:sync".exec = ''
      uv lock --check
      uv sync --locked
      ${wirePynix}
    '';

    # `check_public.py` reads every tracked file, so this task ignores no tracked file.
    "check:lint".input.testee.ignores = [ ".copyroom-local.json" ];
    "check:lint".exec = ''
      set -o pipefail
      uv run --no-sync python -I scripts/check_public.py .
      # A stale `uv.lock` fails here, before the Nix package build does.
      uv lock --check
      out="''${TESTEE_RUN_DIR:-.devenv/state}"
      mkdir -p "$out"
      uv run --no-sync ruff check src tests --output-format json | tee "$out/check:lint.ruff.json"
    '';

    "check:format".input.testee.ignores = unread ++ vmScripts ++ [ "scripts/check_nix.py" "scripts/release.sh" ];
    "check:format".exec = ''
      uv run --no-sync ruff format --check src tests
    '';

    "check:types".input.testee.ignores = unread ++ vmScripts ++ [ "scripts/check_nix.py" "scripts/release.sh" ];
    "check:types".exec = ''
      uv run --no-sync ty check src tests
    '';

    # The tests that run no Nix (seconds).
    # `tests/test_release.py` runs `scripts/release.sh`, so this task reads that script.
    "test:quick".input.testee.ignores = unread ++ vmScripts ++ [ "scripts/check_nix.py" ];
    "test:quick".exec = ''
      set -o pipefail
      uv run --no-sync pytest tests
    '';

    # All tests, then the Nix checks of `scripts/check_nix.py`: the reference drvPaths, the guard lines, the pair check,
    # and one `Input` on Home Manager. The Nix evaluation takes minutes, so the limit is explicit.
    "test:full".input.testee.timeout_s = 1800;
    "test:full".input.testee.ignores = unread ++ vmScripts;
    "test:full".exec = ''
      set -o pipefail
      out="''${TESTEE_RUN_DIR:-.devenv/state}"
      mkdir -p "$out"
      uv run --no-sync pytest -q tests --junitxml="$out/test:full.junit.xml"
      uv run --no-sync python scripts/check_nix.py
    '';

    # Opt-in (needs /dev/kvm; never counts for green): the real `minix` command in a NixOS VM with no network. The VM
    # boots in about a minute, and the cases take about four more (303 s measured, 2026-10-25, with the `rollback` and `gitinput` cases). The limit is about twice that, for a cold store.
    # `MINIX_VM_HOST=basix` with `BASIX_FLAKE=<flake reference>` runs the same cases on a Basix machine.
    # Inputs: the script, the files that it copies into the scratch package (`SPEC_FILES`), and the demo input.
    "opt:minix-apply".input.testee = {
      timeout_s = 600;
      reads = [
        "scripts/apply_vm.py" "src/**" "lib/**" "nix/**" "flake.nix" "flake.lock" "pyproject.toml" "uv.lock" "README.md"
        "tests/fixtures/inputs/**"
      ];
    };
    "opt:minix-apply".exec = ''
      set -o pipefail
      export MINIX_VM_WORK="''${MINIX_VM_WORK:-''${XDG_CACHE_HOME:-$HOME/.cache}/minix/minix-apply}"
      export MINIX_VM_LOGS="''${MINIX_VM_LOGS:-$HOME/.local/state/basix/minix-apply}"
      uv run --no-sync python scripts/apply_vm.py
    '';

    # Opt-in (needs /dev/kvm; never counts for green): `minix update` from one scratch minix release to the next, served
    # through `insteadOf` rules from scratch repositories, in a NixOS VM with no network (G8, 010-D31). The task makes two
    # scratch releases with `scripts/release.sh --skip-status`. It sets its own Git identity. It makes no real
    # tag, no push, and no write to the collections. A run with a new tree takes about 220 s and a repeated run about 195 s (the VM is up at about 110 s); the limit is about twice that.
    # Inputs: both scripts (`update_vm.py` imports `apply_vm.py`), the release job and `check_nix.py` that it runs, and the
    # files of the checkout that the scratch release copies and commits.
    "opt:minix-update".input.testee = {
      timeout_s = 450;
      reads = [
        "scripts/**" "src/**" "lib/**" "nix/**" "flake.nix" "flake.lock" "pyproject.toml" "uv.lock" "README.md"
        "CHANGELOG.md" "provenance/**" "tests/fixtures/**"
      ];
    };
    "opt:minix-update".exec = ''
      set -o pipefail
      export MINIX_VM_WORK="''${MINIX_VM_WORK:-''${XDG_CACHE_HOME:-$HOME/.cache}/minix/minix-update}"
      export MINIX_VM_LOGS="''${MINIX_VM_LOGS:-$HOME/.local/state/basix/minix-update}"
      uv run --no-sync python scripts/update_vm.py
    '';

    # Devman base group entry points. Both wait for setup, then run the checks in order.
    "base:check" = {
      after = [ "setup:sync" ];
      exec = ''
        set -e
        devenv --no-tui tasks run --show-output check:lint
        devenv --no-tui tasks run --show-output check:format
        devenv --no-tui tasks run --show-output check:types
      '';
    };
    "base:test" = {
      after = [ "setup:sync" ];
      exec = ''
        set -e
        devenv --no-tui tasks run --show-output test:full
      '';
    };
  };
}
