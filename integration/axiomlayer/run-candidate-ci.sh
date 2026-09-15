#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: run-candidate-ci.sh TARGET BUILD_MODE NIX_SYSTEM SOURCE" >&2
  exit 2
fi

target=$1
build_mode=$2
nix_system=$3
source_directory=$4
control_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

case "$target:$build_mode:$nix_system" in
  darwin-aarch64:nix-native:aarch64-darwin | \
    darwin-x86_64:nix-native:x86_64-darwin | \
    linux-aarch64:nix-native:aarch64-linux | \
    linux-x86_64:nix-native:x86_64-linux | \
    windows-aarch64:go-native:unsupported | \
    windows-x86_64:go-native:unsupported) ;;
  *)
    echo "unsupported candidate lane: $target:$build_mode:$nix_system" >&2
    exit 1
    ;;
esac

if [[ ! -d "$source_directory" || -L "$source_directory" ]]; then
  echo "candidate source is missing or linked: $source_directory" >&2
  exit 1
fi
source_directory=$(cd "$source_directory" && pwd -P)
if [[ -e "$source_directory/integration/axiomlayer" ]]; then
  echo "candidate source contains the integration control plane" >&2
  exit 1
fi

go_binary=$(command -v go)
go_root=$("$go_binary" env GOROOT)
if [[ $("$go_binary" env GOVERSION) != go1.26.8 ]]; then
  echo "Go toolchain is not go1.26.8" >&2
  exit 1
fi
go_platform=$("$go_binary" env GOOS):$("$go_binary" env GOARCH)
case "$go_platform:$target" in
  darwin:arm64:darwin-aarch64 | darwin:amd64:darwin-x86_64 | \
    linux:arm64:linux-aarch64 | linux:amd64:linux-x86_64 | \
    windows:arm64:windows-aarch64 | windows:amd64:windows-x86_64) ;;
  *)
    echo "candidate target does not match Go platform: $go_platform:$target" >&2
    exit 1
    ;;
esac

temporary_directory=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/axiom-gh-candidate.XXXXXXXX")
temporary_directory=$(cd "$temporary_directory" && pwd -P)
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM
mkdir -p \
  "$temporary_directory/home/.ssh" \
  "$temporary_directory/cache" \
  "$temporary_directory/config" \
  "$temporary_directory/go-cache" \
  "$temporary_directory/go-mod-cache" \
  "$temporary_directory/go-path"

case "$(uname -s)" in
  Darwin) system_path=/usr/bin:/bin:/usr/sbin:/sbin ;;
  Linux) system_path=/usr/local/bin:/usr/bin:/bin ;;
  MINGW* | MSYS* | CYGWIN*) system_path=$(dirname "$go_binary"):/usr/bin:/bin ;;
  *)
    echo "unsupported candidate host: $(uname -s)" >&2
    exit 1
    ;;
esac

clean_go() {
  local -a environment=(
    env -i
    "HOME=$temporary_directory/home"
    "USER=$(id -un)"
    "LOGNAME=$(id -un)"
    "PATH=$(dirname "$go_binary"):$system_path"
    "TMPDIR=$temporary_directory"
    "TEMP=$temporary_directory"
    "TMP=$temporary_directory"
    "XDG_CACHE_HOME=$temporary_directory/cache"
    "XDG_CONFIG_HOME=$temporary_directory/config"
    "GOCACHE=$temporary_directory/go-cache"
    "GOMODCACHE=$temporary_directory/go-mod-cache"
    "GOPATH=$temporary_directory/go-path"
    "GOROOT=$go_root"
    "GOENV=off"
    "GOTOOLCHAIN=local"
    "GOPROXY=https://proxy.golang.org"
    "GOSUMDB=sum.golang.org"
    "SOURCE_DATE_EPOCH=1788449059"
    "TERM=xterm-256color"
    "CI=true"
    "GH_TELEMETRY=false"
  )
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
      environment+=(
        "SYSTEMROOT=${SYSTEMROOT:?SYSTEMROOT is required on Windows}"
        "WINDIR=${WINDIR:?WINDIR is required on Windows}"
        "COMSPEC=${COMSPEC:?COMSPEC is required on Windows}"
        "PATHEXT=${PATHEXT:?PATHEXT is required on Windows}"
      )
      ;;
  esac
  "${environment[@]}" "$@"
}

cd "$source_directory"
clean_go "$go_binary" test ./...

if [[ $build_mode == nix-native ]]; then
  nix_build=$(command -v nix-build)
  nix_binary=$(command -v nix)
  if [[ $("$nix_binary" --version) != "nix (Nix) 2.35.2" ]]; then
    echo "Nix runtime is not 2.35.2" >&2
    exit 1
  fi
  env -i \
    HOME="$temporary_directory/home" \
    USER="$(id -un)" \
    LOGNAME="$(id -un)" \
    PATH="$(dirname "$nix_build"):$system_path" \
    TMPDIR="$temporary_directory" \
    CI=true \
    "$nix_build" "$control_directory/default.nix" \
    --arg src "$source_directory" \
    --argstr system "$nix_system" \
    --option sandbox true \
    --no-out-link
else
  clean_go "$go_binary" run script/build.go bin/gh.exe GH_VERSION=2.100.0
  version=$(clean_go ./bin/gh.exe --version)
  version=${version%%$'\n'*}
  case "$version" in
    "gh version 2.100.0 "*) ;;
    *)
      echo "unexpected candidate version: $version" >&2
      exit 1
      ;;
  esac
fi

printf '%s\n' "candidate=$target source=45437bc7eeeb3359bbfddd1742f79de7652fd3e2 environment=sterile"
