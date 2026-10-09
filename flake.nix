{
  description = "Shared album reminders with a daily macOS job";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    soma = {
      url = "github:alexjmiller5/soma/1950bdb22ad8965bcee776ef4051f1665b590aeb";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, soma }:
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
            runtimeInputs = [ pkgs.python313 ];
            text = ''
              export PYTHONPATH="${soma.packages.${system}.default}/${pkgs.python313.sitePackages}"
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
            nativeBuildInputs = [ (pkgs.python313.withPackages (p: [ p.pytest ])) ];
          } ''
            export PYTHONDONTWRITEBYTECODE=1
            export PYTHONPATH="${soma.packages.${system}.default}/${pkgs.python313.sitePackages}"
            pytest -p no:cacheprovider ${self}/tests -q
            touch "$out"
          '';
        });

      devShells = eachSystem (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in { default = pkgs.mkShell { packages = [ pkgs.uv pkgs.just pkgs.ruff ]; }; });
    };
}
