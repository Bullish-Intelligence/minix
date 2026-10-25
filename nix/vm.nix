# TEST ONLY (lane p5-int). The VM of `scripts/apply_vm.py`: one NixOS machine with one learner account.
#
#   nix build --file nix/vm.nix --argstr nixpkgs STORE_PATH --argstr minix STORE_PATH --argstr testKey 'ssh-ed25519 ...'
#
# The plain host (`host = "plain"`, the default) is a NixOS machine with no Basix: Nix with flakes off by default, a
# systemd user manager, and no lingering. The VM has no substituter and no network, so every store path that a case
# needs comes from the host (`apply_vm.py` copies them in). The learner has no key and no `sudo`.
#
# The Basix host (`host = "basix"`) extends the Basix v2 machine that `basixFlake` names. minix must not depend on the
# Basix flake (L105), so the flake reference comes from the environment of the run (`BASIX_FLAKE`) and no default exists.
# The attribute `basixAttr` (default `basix`) of its `nixosConfigurations` is the machine. The machine must be a Basix v2
# workspace with `user="learner"`, `disk=None`, and a root file system of its own (the VM module replaces it). The Basix
# core sets flakes and lingering on, so `apply_vm.py` expects both on this host (010-D16, `BSX-393`).
{ nixpkgs
, minix
, testKey
, system ? "x86_64-linux"
, host ? "plain"
, basixFlake ? null
, basixAttr ? "basix"
, memory ? 6144
}:
let
  # The machine under test: the learner, the packages, and the test channel.
  machine = { config, lib, pkgs, ... }: {
    users.users.learner = {
      isNormalUser = true;
      initialPassword = "minix-test";
    };
    environment.systemPackages = [ (builtins.storePath minix) pkgs.git pkgs.curl pkgs.util-linux ];
    # No lingering for the learner: the first cases need a machine that stops user services at logout.

    # The test channel: a second SSH server, so that nothing here changes the services of the machine.
    users.users.sshd = { isSystemUser = true; group = "sshd"; };
    users.groups.sshd = { };
    environment.etc."testlink/learner.keys".text = testKey + "\n";
    environment.etc."testlink/root.keys".text = testKey + "\n";
    networking.firewall.allowedTCPPorts = [ 2222 ];
    # A session for the learner, so that logind starts the user manager as a login does.
    security.pam.services.sshd.startSession = true;
    systemd.services.testlink = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      path = [ pkgs.openssh ];
      preStart = "mkdir -p /var/lib/testlink; [ -f /var/lib/testlink/host_key ] || ssh-keygen -q -t ed25519 -N '' -f /var/lib/testlink/host_key";
      serviceConfig.ExecStart = "${pkgs.openssh}/bin/sshd -D -e -p 2222 -o HostKey=/var/lib/testlink/host_key -o PermitRootLogin=prohibit-password -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -f /dev/null -o UsePAM=yes -o StrictModes=no -o PerSourcePenalties=no -o AuthorizedKeysFile=/etc/testlink/%u.keys";
    };
  };

  # The VM wrapper: it runs the machine under QEMU with its own store image and no substituter.
  vm = { config, lib, pkgs, modulesPath, ... }: {
    imports = [
      (modulesPath + "/virtualisation/qemu-vm.nix")
      (modulesPath + "/profiles/qemu-guest.nix")
    ];
    boot.loader.systemd-boot.enable = lib.mkForce false;
    boot.loader.grub.enable = false;
    system.build.installBootLoader = lib.mkOverride 90 "${pkgs.coreutils}/bin/true";
    boot.kernelParams = [ "console=ttyS0" ];
    boot.initrd.availableKernelModules = [ "virtio_blk" "virtio_pci" "virtio_net" ];
    virtualisation = {
      graphics = false;
      memorySize = memory;
      cores = 4;
      diskSize = 16384;
      writableStoreUseTmpfs = false;
      # The VM has its own store (an image of the system closure), so a collection in the VM cannot reach the host.
      mountHostNixStore = false;
      useNixStoreImage = true;
      writableStore = true;
    };
    nix.settings.substituters = lib.mkForce [ ];
    networking.hostName = lib.mkDefault "minixbox";
    system.stateVersion = lib.mkDefault "26.05";
  };

  plain = (import (nixpkgs + "/nixos/lib/eval-config.nix") {
    inherit system;
    modules = [ machine vm ];
  });

  basix = (builtins.getFlake basixFlake).nixosConfigurations.${basixAttr}.extendModules {
    modules = [ machine vm ];
  };
in
if host == "plain" then plain.config.system.build.vm
else if host == "basix" then
  (if basixFlake == null then throw "host = \"basix\" needs basixFlake (the BASIX_FLAKE of the run)" else basix.config.system.build.vm)
else throw "host must be \"plain\" or \"basix\", found ${host}"
