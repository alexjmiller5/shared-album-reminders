self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.shared-album-reminders;
  package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  logPath = "${config.home.homeDirectory}/Library/Logs/shared-album-reminders.log";
in {
  options.services.shared-album-reminders = {
    enable = lib.mkEnableOption "daily shared album reminders";
    library = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/Pictures/Photos Library.photoslibrary";
      description = "Photos library to inspect.";
    };
    dryRun = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Validate installed access and preview without creating tasks.";
    };
    configFile = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.configHome}/shared-album-reminders/config.json";
      description = "Runtime service configuration created with --configure.";
    };
    hour = lib.mkOption {
      type = lib.types.ints.between 0 23;
      default = 9;
      description = "Local hour for the daily check.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = pkgs.stdenv.hostPlatform.isDarwin;
      message = "Shared Album Reminders requires macOS Photos.";
    }];
    home.packages = [ package ];
    launchd.agents.shared-album-reminders = {
      enable = true;
      config = {
        Label = "org.shared-album-reminders.daily";
        ProgramArguments = [ (lib.getExe package) "--library" cfg.library "--config" cfg.configFile ]
          ++ lib.optional cfg.dryRun "--dry-run";
        StartCalendarInterval = [{ Hour = cfg.hour; Minute = 0; }];
        StandardOutPath = logPath;
        StandardErrorPath = logPath;
      };
    };
  };
}
