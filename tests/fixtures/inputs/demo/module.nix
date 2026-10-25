{ config, lib, ... }:
let
  cfg = config.programs.demo;
in
{
  options.programs.demo = {
    enable = lib.mkEnableOption "the demo program";
    greeting = lib.mkOption {
      type = lib.types.str;
      default = "hello";
      description = "The text of the greeting.";
    };
    count = lib.mkOption {
      type = lib.types.ints.positive;
      default = 1;
      description = "How many times the greeting repeats.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.file.".config/demo/greeting".text = "${cfg.greeting} x${toString cfg.count}\n";
  };
}
