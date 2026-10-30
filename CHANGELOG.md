# Changelog

## v0.5.1

```text
minix 0.5.1 (pynix pynix-v0.7.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.5.0. Commits since then: 4.
- Version 0.5.1
- Warn when the home and the system pin a Python input at different revisions
- Pin pynix-v0.7.0
- Test the helper NameError and failing import text
```

## v0.5.0

```text
minix 0.5.0 (pynix pynix-v0.6.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.4.2. Commits since then: 9.
- Version 0.5.0
- Document the python field of Input
- Build the Python code of an input into the input packages
- Pin pynix-v0.6.0
- Test that the programs error names every program
- Name the file of an error in a helper module
- Refuse an Input that discovery did not read from home.py
- Clear NIX_PYTHONPATH in the compiler wrapper
- Check the input packages again at evaluation
```

## v0.4.2

```text
minix 0.4.2 (pynix pynix-v0.5.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.4.1. Commits since then: 9.
- Version 0.4.2
- Fix the VM checks: copy the lock sources without a signature check
- Let a Native program omit file=
- Run the package of an input or a pin, not the nixpkgs package of the same name
- Let minix update, apply, and undo run in a git worktree
- Pin pynix-v0.5.0
- Document Native, the unit fields, sockets, and the second piece of a unit
- Prove that a second piece of a unit merges, and that Native runs
- Let Service and Step run a Native package, and add three unit fields
```

## v0.4.1

```text
minix 0.4.1 (pynix pynix-v0.4.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.4.0. Commits since then: 3.
- Version 0.4.1
- Pin pynix-v0.4.0
- Let ~/ in args name a path in the home
```

## v0.4.0

```text
minix 0.4.0 (pynix pynix-v0.3.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.3.2. Commits since then: 6.
- Version 0.4.0
- Add Zsh: the home owns zsh completion
- Add Timer: a Service can start on a calendar
- README: list the run-time contracts with a Basix host
- Add typed unit fields and Step to Service
- Emit one Home Manager value for each user unit
```

## v0.3.2

```text
minix 0.3.2 (pynix pynix-v0.3.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.3.0. Commits since then: 8.
- Version 0.3.2
- Let minix init make the folder of MINIX_WORKSPACE
- Release v0.3.1: provenance and changelog
- Version 0.3.1
- Let MINIX_WORKSPACE name the default workspace folder
- Test the stub fallback with a hash that no failed fetch leaves in the store
- Install the Zellij of the pyllij input through TerminalWorkspace
- README: say that pull requests are welcome and review is not promised
```

## v0.3.1

```text
minix 0.3.1 (pynix pynix-v0.3.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Previous: v0.3.0. Commits since then: 5.
- Version 0.3.1
- Let MINIX_WORKSPACE name the default workspace folder
- Test the stub fallback with a hash that no failed fetch leaves in the store
- Install the Zellij of the pyllij input through TerminalWorkspace
- README: say that pull requests are welcome and review is not promised
```

## v0.3.0

```text
minix 0.3.0 (pynix pynix-v0.3.0; nixpkgs e7439b6, 2026-10-08; Home Manager fae6e9e)
Initial release.
```
