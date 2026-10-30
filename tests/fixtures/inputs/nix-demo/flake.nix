{
  description = "A tiny input with one package for the minix checks (the package of an input as the program of a Service).";

  # The managed stub of the workspace follows this input: the checks lock no second nixpkgs.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/e7439b6b14ad3cc35d05608ebca9bce01a25f5f8";

  # The attribute is `daemon`, the name of the package is `daemon-input`, and the main program is `daemon-input-bin`.
  # Nixpkgs holds another package that is named `daemon`. A service must run this one.
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      packages.${system}.daemon = pkgs.runCommand "daemon-input" { meta.mainProgram = "daemon-input-bin"; } ''
        mkdir -p $out/bin
        printf '#!/bin/sh\ntrue\n' > $out/bin/daemon-input-bin
        chmod +x $out/bin/daemon-input-bin
      '';
    };
}
