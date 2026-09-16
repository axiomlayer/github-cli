{ system ? builtins.currentSystem
, src ? ../..
}:

let
  nixpkgs = builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/c3eea5b2156db11c7eeeada3dc737711255b253e.tar.gz";
    sha256 = "sha256-vdhpDJ3Lr24lkZ+fDCjmBRRtw9/vcSzkqGJOJyF5h2U=";
  };
  pkgs = import nixpkgs { inherit system; };
in
pkgs.buildGoModule {
  pname = "github-cli-axiomlayer-integration";
  version = "2.100.0";
  inherit src;

  vendorHash = "sha256-ZqUs2BnasF3QBX0I2Sxh2A/CnO61Vy6gRn1hkf0n9AY=";
  nativeBuildInputs = [ pkgs.makeWrapper ];
  strictDeps = true;

  buildPhase = ''
    runHook preBuild
    SOURCE_DATE_EPOCH=1788449059 \
      make GH_VERSION=2.100.0 GO_LDFLAGS="-s -w" bin/gh
    runHook postBuild
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export HOME="$TMPDIR/axiomlayer-home"
    export XDG_CONFIG_HOME="$TMPDIR/axiomlayer-xdg-config"
    export XDG_CACHE_HOME="$TMPDIR/axiomlayer-xdg-cache"
    export TERM=xterm-256color
    mkdir -p "$HOME/.ssh" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
    unset PAGER GH_PAGER NO_COLOR CLICOLOR GH_ACCESSIBLE_PROMPTER
    # The workflow runs the complete suite with the declared Go toolchain on
    # every native runner. Keep a small sandboxed check here so Nix itself also
    # proves test execution without depending on host utilities.
    go test ./internal/build ./internal/safepaths
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm755 bin/gh "$out/bin/gh"
    wrapProgram "$out/bin/gh" --set-default GH_TELEMETRY false
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    version="$($out/bin/gh --version | head -n 1)"
    case "$version" in
      "gh version 2.100.0 "*) ;;
      *) echo "unexpected version: $version" >&2; exit 1 ;;
    esac
  '';
}
