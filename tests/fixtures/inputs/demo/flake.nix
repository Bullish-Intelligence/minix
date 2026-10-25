{
  description = "A tiny Home Manager module for the minix checks (the input of one Input on Home Manager).";

  # Both inputs are followed by the managed stub of the workspace: the checks lock no second copy.
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/e7439b6b14ad3cc35d05608ebca9bce01a25f5f8";
    home-manager = {
      url = "github:nix-community/home-manager/fae6e9e42c3b762ab47635cddcfaf6f52374a61b";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  # A module that the flake exports as a function (`import ./module.nix`), as example-tool does: the module system then
  # reports `<unknown-file>` as the declaration, and the generator reads the position of the option instead.
  outputs = { self, ... }: {
    homeManagerModules.demo = import ./module.nix;
  };
}
