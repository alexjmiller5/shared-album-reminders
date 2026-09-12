{
  description = "Shared album reminders with a daily macOS job";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    life-data = {
      url = "github:alexjmiller5/life-data";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, life-data }:
    let
      eachSystem = nixpkgs.lib.genAttrs [
        "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux"
      ];
    in {
      packages = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.writeShellApplication {
            name = "shared-album-reminders";
            runtimeInputs = [ pkgs.python3 life-data.packages.${system}.default ];
            text = ''
              exec python3 ${./scripts/shared_album_reminders.py} "$@"
            '';
          };
        });

      homeModules = rec {
        shared-album-reminders = import ./nix/home-module.nix self;
        default = shared-album-reminders;
      };

      checks = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          tests = pkgs.runCommand "shared-album-reminders-tests" {
            nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pytest ])) ];
          } ''
            export PYTHONDONTWRITEBYTECODE=1
            pytest -p no:cacheprovider ${self}/tests/test_reminders.py -q
            touch "$out"
          '';
        });

      devShells = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in { default = pkgs.mkShell { packages = [ pkgs.uv pkgs.just pkgs.ruff ]; }; });
    };
}
