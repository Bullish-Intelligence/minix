{
  description = "minix: the home layer of Basix v2.";

  inputs = {
    # One pin set for every repository (AGENT-COMMON of project 010). `devenv.yaml` repeats the nixpkgs pin.
    nixpkgs.url = "github:NixOS/nixpkgs/e7439b6b14ad3cc35d05608ebca9bce01a25f5f8";
    # Home Manager follows nixpkgs, so `pynix_hm` pairs with the nixpkgs that minix evaluates with (L126).
    home-manager = {
      url = "github:nix-community/home-manager/fae6e9e42c3b762ab47635cddcfaf6f52374a61b";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # flake-parts assembles the outputs (009-D1). The learner stub stays a plain flake.
    flake-parts = {
      url = "github:hercules-ci/flake-parts/024633cd702b10285db5cb19b40ad48d2399ba60";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };
    # The typed libraries, the decoder, and the engine. The tag is the future public URL (010-D8): the owner's git
    # config maps it to the local cache. `flake = false`: a consumer reads `"${pynix}/src"`.
    pynix = {
      url = "git+https://github.com/Bullish-Intelligence/pynix?ref=refs/tags/pynix-v0.6.0";
      flake = false;
    };
    # The Zellij of the home: pyllij is the only source of that package (`packages.<system>.zellij`). minix installs
    # exactly the Zellij that pyllij defines and measures (`lib/home.nix`, option `minix.zellijPackage`). To move Zellij,
    # change pyllij, then bump this tag. The tag is the future public URL, like `pynix`. pyllij's own inputs follow the
    # pins of this flake, so minix locks no second copy of them (pyllij's Zellij does not use them).
    pyllij = {
      url = "git+https://github.com/Bullish-Intelligence/pyllij?ref=refs/tags/v0.2.0";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.pyproject-build-systems.follows = "pyproject-build-systems";
    };
    # The uv2nix inputs are declared here once.
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix/ddc400a6883b042646d0940e65971bb7f7e96107";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix/7aba73a3d85454f1118f8cf9de97979b4d26bcac";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs/a17819c85163988e063806ffc4f6d2716bc8ead8";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    inputs@{ self, nixpkgs, flake-parts, pyproject-nix, uv2nix, pyproject-build-systems, pynix, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      # Only an attribute that a command asks for is evaluated, so the second system costs nothing until it is used.
      systems = [ "x86_64-linux" "aarch64-linux" ];

      flake = {
        # `lib.home`, `lib.homeConfiguration`, and `lib.inputPackages`: see `lib/home.nix`.
        lib = import ./lib/home.nix { inherit self inputs; };
      };

      perSystem =
        { pkgs, system, ... }:
        let
          inherit (nixpkgs) lib;
          project = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project;

          # The compiler: Python 3.13 and Pydantic from nixpkgs (008-Q7), the `pynix` source, and `minix_config`.
          compiler = import ./nix/compiler.nix {
            inherit pkgs pynix;
            source = builtins.path {
              path = ./src/minix_config;
              name = "minix_config";
              filter = path: type: baseNameOf path != "__pycache__";
            };
          };

          # The `minix` command: the uv2nix application of `uv.lock`, with `pynix` on its path.
          workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
          lockedOverlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
          minixSource = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./src
              ./pyproject.toml
              ./README.md
            ];
          };
          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python313; }).overrideScope (
              lib.composeManyExtensions [
                pyproject-build-systems.overlays.wheel
                lockedOverlay
                (final: prev: {
                  minix = prev.minix.overrideAttrs (old: {
                    src = minixSource;
                    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
                      hatchling = [ ];
                    };
                    meta = (old.meta or { }) // {
                      description = project.description;
                      mainProgram = "minix";
                    };
                  });
                })
              ]
            );
          venv = pythonSet.mkVirtualEnv "minix-env" workspace.deps.default;
          inherit (pkgs.callPackages pyproject-nix.build.util { }) mkApplication;
          application = mkApplication {
            inherit venv;
            package = pythonSet.minix;
          };
          # The command needs `pynix_engine` and `pynix_native` at run time. `uv.lock` does not list `pynix` (010-D8),
          # so the wrapper puts the `src` folder of the locked `pynix` on `PYTHONPATH`.
          # `share/minix/source.json` names the source of this flake (`rev`, `narHash`, ...). `share/minix/flake.lock` is
          # the lock of this flake. `minix init` joins them into the lock of a new workspace, with no network (G9).
          # The wrapper sets `MINIX_SHARE`.
          sourceInfo = builtins.toJSON {
            rev = self.rev or null;
            narHash = self.narHash or null;
            lastModified = self.lastModified or null;
            revCount = self.revCount or null;
          };
          minix = pkgs.runCommand "minix-${project.version}"
            {
              nativeBuildInputs = [ pkgs.makeWrapper ];
              meta = (application.meta or { }) // {
                mainProgram = "minix";
              };
            }
            ''
              mkdir -p "$out/bin" "$out/share/minix"
              echo '${sourceInfo}' > "$out/share/minix/source.json"
              cp ${./flake.lock} "$out/share/minix/flake.lock"
              makeWrapper ${application}/bin/minix "$out/bin/minix" \
                --prefix PYTHONPATH : ${pynix}/src --set MINIX_SHARE "$out/share/minix"
            '';
        in
        {
          packages = {
            inherit compiler minix;
            default = minix;
          };

          apps.default = {
            type = "app";
            program = "${minix}/bin/minix";
            meta.description = project.description;
          };

          checks.minix = pkgs.runCommand "minix-check" { nativeBuildInputs = [ minix ]; } ''
            export HOME=$TMPDIR
            minix --help > /dev/null
            minix --version | grep -q '^minix '
            touch $out
          '';
        };
    };
}
