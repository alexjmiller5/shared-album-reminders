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
        ProgramArguments = [ (lib.getExe package) "--library" cfg.library ];
        StartCalendarInterval = [{ Hour = cfg.hour; Minute = 0; }];
        StandardOutPath = logPath;
        StandardErrorPath = logPath;
      };
    };
  };
}
