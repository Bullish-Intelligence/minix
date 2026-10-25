# The pair check (009-D28, L126): the generated library `pynix_hm` must come from the Home Manager and the nixpkgs
# that minix evaluates with. The package records both revisions in `SOURCE` of its `_generation.py`.
#
# `check` returns null when the pair matches, or one plain line that names both pairs.
{ lib }:
rec {
  # The revisions in `SOURCE`: the lines `"home-manager": "REV",` and `"nixpkgs": "REV",`. The file is Python text,
  # so the function matches the lines and never runs the file.
  readRecord = file:
    let
      lines = lib.splitString "\n" (builtins.readFile file);
      pick = key:
        let
          hits = lib.concatMap
            (l:
              let m = builtins.match "[ ]*\"${key}\": \"([0-9a-f]{40})\",?" l;
              in lib.optional (m != null) (builtins.head m))
            lines;
        in
        if hits == [ ] then null else builtins.head hits;
    in
    { home-manager = pick "home-manager"; nixpkgs = pick "nixpkgs"; };

  short = rev: if rev == null then "unknown" else builtins.substring 0 12 rev;

  # `locked.nixpkgs` is the nixpkgs input. `locked.hm-nixpkgs` is the nixpkgs that Home Manager follows.
  check = { recorded, locked }:
    if recorded.home-manager == locked.home-manager && recorded.nixpkgs == locked.nixpkgs && recorded.nixpkgs == locked.hm-nixpkgs
    then null
    else
      "pynix_hm was generated from Home Manager ${short recorded.home-manager} and nixpkgs ${short recorded.nixpkgs}. "
      + "The lock holds Home Manager ${short locked.home-manager}, nixpkgs ${short locked.nixpkgs}, "
      + "and Home Manager follows nixpkgs ${short locked.hm-nixpkgs}. "
      + "Use a pynix release that matches the lock, or move the lock back.";
}
