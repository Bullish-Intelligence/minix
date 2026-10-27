# `lib.home`: the compiled JSON of `home.py` becomes `homeConfigurations.<user>` (standalone Home Manager).
# The module list is the base (user, home folder, state version, no manual, the session hook), then one module per
# fragment, then the native modules, the modules of the inputs, and `extraModules`.
#
# Three checks stop the evaluation with one plain line:
#   - the guard (009-D45): `home.py` or a file that it read changed after the last compile (`guard.nix`);
#   - the pair check (009-D28, L126): `pynix_hm` was generated from another Home Manager or nixpkgs (`pair.nix`);
#   - an input module that the stub does not carry yet.
#
# The decoder is the file of the locked `pynix` (`flake = false`): minix keeps no copy of it.
{ self, inputs }:
let
  nlib = inputs.nixpkgs.lib;
  pynix = inputs.pynix;
  guard = import ./guard.nix { lib = nlib; };
  pair = import ./pair.nix { lib = nlib; };
  decodeFragment = import "${pynix}/src/decoder.nix";

  defaultSystem = "x86_64-linux";
  pkgsFor = system: inputs.nixpkgs.legacyPackages.${system};

  # The library paths of the editor: ty reads `minix_config` and `pynix` from them (the component `Neovim`).
  pythonPath = "${self}/src:${pynix}/src";

  pairProblem = pair.check {
    recorded = pair.readRecord "${pynix}/src/pynix_hm/_generation.py";
    locked = {
      home-manager = inputs.home-manager.rev or null;
      nixpkgs = inputs.nixpkgs.rev or null;
      hm-nixpkgs = inputs.home-manager.inputs.nixpkgs.rev or null;
    };
  };

  # The base of every home. Python and native homes share it, so the difference is only what the learner sets.
  # Every value that a home may change is `mkDefault` (009-D39). The user name stays plain: it is the key of the home.
  base = user: system: { config, lib, ... }: {
    options.minix = {
      pythonPath = lib.mkOption {
        type = lib.types.str;
        internal = true;
        readOnly = true;
        description = "The search path of Python for the editor client of ty: minix_config and pynix.";
      };
      package = lib.mkOption {
        type = lib.types.nullOr lib.types.package;
        internal = true;
        default = self.packages.${system}.minix;
        defaultText = lib.literalMD "`packages.minix` of the flake of minix, built from its lock";
        description = "The `minix` command of the home. Its source is the pinned lock of this flake. null leaves it out.";
      };
      zellijPackage = lib.mkOption {
        type = lib.types.package;
        internal = true;
        default = inputs.pyllij.packages.${system}.zellij;
        defaultText = lib.literalMD "`packages.zellij` of the input `pyllij` of the flake of minix";
        description = "The Zellij of the component TerminalWorkspace. pyllij defines and measures it; minix installs it unchanged.";
      };
    };
    config = {
      minix.pythonPath = pythonPath;
      # The command of the home (L113, 010-D21): the home holds the `minix` of its own lock. The user profile comes
      # before `/run/current-system/sw` on `PATH`, so after the first `minix apply` this command takes over from the
      # launcher of the machine.
      home.packages = lib.optional (config.minix.package != null) config.minix.package;
      home.username = user;
      home.homeDirectory = lib.mkDefault "/home/${user}";
      # `minix init` writes `state_version` into home.py. This default serves a home.py that has none.
      home.stateVersion = lib.mkDefault "26.11";
      manual = { manpages.enable = lib.mkDefault false; html.enable = lib.mkDefault false; json.enable = lib.mkDefault false; };
      systemd.user.startServices = lib.mkDefault "sd-switch";
      # The session hook (G15, 009-D29). Home Manager writes the session variables (`EDITOR` of `Neovim(default_editor=True)`)
      # to `hm-session-vars.sh`, and only its `bash` module makes a login shell load that file. minix owns the hook, so
      # a program that sets a variable works after the next login on any host. A home that sets `programs.bash.enable`
      # itself, for example to `false`, wins over this default.
      programs.bash.enable = lib.mkDefault true;
    };
  };

  homeConfiguration = { user, modules, system ? defaultSystem, extraSpecialArgs ? { } }:
    if pairProblem != null then throw pairProblem else
    inputs.home-manager.lib.homeManagerConfiguration {
      pkgs = pkgsFor system;
      inherit extraSpecialArgs;
      modules = [ (base user system) ] ++ modules;
    };

  # The packages of the extra inputs, as typed Python classes (`pynix_NAME`, 009-D47). `extraInputs` is what the stub
  # passes. The `nixpkgs-NAME` inputs are the pins of `NixpkgsPin("NAME", commit)`. They have no options, so they get no
  # classes. They get the package list `pynix_nixpkgs_NAME` instead, so `NAME.pkgs.PACKAGE` finds its packages
  # (009-D53, 009-D54).
  inputPackages = { extraInputs, modules ? { }, system ? defaultSystem }:
    let
      pins = nlib.mapAttrs' (n: v: nlib.nameValuePair (nlib.removePrefix "nixpkgs-" n) v)
        (nlib.filterAttrs (n: _: nlib.hasPrefix "nixpkgs-" n) extraInputs);
    in
    import "${pynix}/nix/input-packages.nix" ({
      pkgs = pkgsFor system;
      layer = "home-manager";
      inputs = nlib.filterAttrs (n: _: !(nlib.hasPrefix "nixpkgs-" n)) extraInputs;
      inherit modules;
      nixpkgs = inputs.nixpkgs;
      homeManager = inputs.home-manager;
      src = "${pynix}/src";
      specialArgs.inputs = { nixpkgs = inputs.nixpkgs; minix = self; } // extraInputs;
    }
    # Only an entry with a `NixpkgsPin(...)` passes the argument, so a pynix that predates it still serves the other entries.
    // nlib.optionalAttrs (pins != { }) { nixpkgsInputs = pins; });

  home =
    { compiled        # a path: the file `home.json` that `minix_config.compile` writes
    , source          # the file `home.py` of the workspace (a path), for the guard
    , root ? null     # the root for `Native` and `PathRef` references; the folder of `source` when null
    , system ? null   # the system of the host; the stub passes it (`minix init` records it, 009-D29)
    , extraModules ? [ ]
    , extraInputs ? { }  # the flake inputs of the stub, but `self`, `minix`, and `nixpkgs`
    }:
    let
      workspace = builtins.dirOf source;
      nativeRoot = if root == null then workspace else root;
      data = builtins.fromJSON (builtins.readFile compiled);
      theSystem = if system != null then system else data.system or defaultSystem;
      decode = fragment: decodeFragment { inherit fragment nativeRoot; };

      problem = guard { inherit data source; name = "home.py"; command = "minix apply"; };

      # A native module of the workspace folder (009-D40): the file must stay below the folder.
      place = n:
        if nlib.hasPrefix "/" n.file || nlib.elem ".." (nlib.splitString "/" n.file)
        then throw "${n.source}: the native module `${n.file}` must be below the workspace folder"
        else if !builtins.pathExists (workspace + "/${n.file}")
        then throw "${n.source}: the native module `${n.file}` does not exist (is it in the workspace folder, and added to version control?)"
        else workspace + "/${n.file}";

      # `Input(..., modules=[...])` imports `homeManagerModules.MODULE` of that input (SG42c). The module comes with the
      # same wrapper as a native module, so the order of the modules is the order of a native import file.
      inputModule = i: m:
        let set = extraInputs.${i.name}.homeManagerModules or { };
        in
        if !(extraInputs ? ${i.name})
        then throw "${i.source}: the input `${i.name}` is not in flake.nix yet: run `minix apply`"
        else if !(set ? ${m})
        then throw "${i.source}: the input `${i.name}` has no homeManagerModules.${m}. It has: ${nlib.concatStringsSep ", " (builtins.attrNames set)}."
        else { _file = i.source; imports = [ set.${m} ]; };
      inputModules = nlib.concatMap (i: map (inputModule i) (i.modules or [ ])) (nlib.filter (i: i.flake or true) (data.inputs or [ ]));
    in
    if problem != null then throw problem else
    {
      homeConfigurations.${data.user} = homeConfiguration {
        user = data.user;
        system = theSystem;
        modules = map decode data.fragments
          ++ map (n: { _file = n.source; imports = [ (place n) ]; }) (data.modules or [ ])
          ++ inputModules
          ++ extraModules;
        # SG32 (G41): `home` is the compiled entry; a native module reads `home.values.NAME`.
        # `inputs` holds the nixpkgs of the layer, minix, and the extra inputs.
        extraSpecialArgs = { inputs = { nixpkgs = inputs.nixpkgs; minix = self; } // extraInputs; home = data; };
      };
    };
in
{
  inherit home homeConfiguration inputPackages;
  # The pair check as a function, for the checks of the repository.
  pairCheck = pair.check;
  readPairRecord = pair.readRecord;
}
