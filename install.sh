#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
MAN_DIR="$PREFIX/share/man/man1"
SYSTEM_ZSH_DIR="$PREFIX/share/zsh/site-functions"
DOTCLI_ZSH_DIR="$HOME/.local/share/dotcli/dotfiles/common/completions"
ZSH_DIR="$SYSTEM_ZSH_DIR"
ZSH_DIR_SOURCE="system"
ZSH_FPATH_STATUS="unchecked"
ZSH_FPATH_MATCH=""
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
INSTALLER_STATE_DIR="$STATE_HOME/devtool/installer"

ASSUME_YES=false
DRY_RUN=false
INSTALL_CORE=true
APP_MODE="auto"          # auto|desktop|android|none
APP_CHOICE="ask"         # ask|yes|no
CLIPBOARD_CHOICE="ask"   # ask|yes|no
ROOT_MODULE_CHOICE="ask" # ask|yes|no
FORCE_APP_BUILD=false
FORCE_ROOT_MODULE_BUILD=false
INSTALL_STAGE_DIR=""

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Install devtool and optionally build/install the native app for this platform.

Options:
  --yes                         Accept recommended prompts.
  --app auto|desktop|android|none
                                Select the app platform. With no value, auto-detect
                                the platform and install its app.
  --with-app                    Build if needed and install/update the selected app.
  --without-app                 Skip app build/install.
  --force-app-build             Rebuild even when the app sources are unchanged.
  --with-root-module            On Android, build if needed and install/update the
                                optional KernelSU root bridge module.
  --without-root-module         Skip the optional KernelSU root bridge module.
  --force-root-module-build     Rebuild the root bridge module even when current.
  --with-clipboard-service      Reserved; the desktop clipboard monitor is not shipped yet.
                                Prompt wording retained: and enable the clipboard monitor service?
  --without-clipboard-service   Leave the clipboard service unchanged.
  --skip-core                   Skip Python module, man page, and completion.
  --prefix PATH                 Installation prefix (default: $HOME/.local).
  --dry-run                     Print actions without changing the system.
  -h, --help                    Show this help.

Desktop app installation now packages the GTK4 Control Center desktop integration.
It rebuilds only when the GTK4 desktop source fingerprint changes or its launcher is
missing. Android app installation builds through Devtool's android_hud chroot
target, stages the APK below $TMPDIR, and installs it with su -c 'pm install -r
<apk>'. The optional KernelSU module is staged below $TMPDIR and installed with
su -c 'ksud install <zip>'.
USAGE
}

need_value() {
  [[ $# -ge 2 && -n "${2:-}" ]] || {
    printf 'error: missing value for %s\n' "$1" >&2
    exit 2
  }
}

while (($#)); do
  case "$1" in
    --yes|-y) ASSUME_YES=true; shift ;;
    --app)
      if [[ $# -ge 2 && -n "${2:-}" && "${2:-}" != --* ]]; then
        APP_MODE="$2"
        shift 2
      else
        APP_MODE=auto
        APP_CHOICE=yes
        shift
      fi
      ;;
    --with-app) APP_CHOICE=yes; shift ;;
    --without-app) APP_CHOICE=no; shift ;;
    --force-app-build) FORCE_APP_BUILD=true; shift ;;
    --with-root-module|--with-module) ROOT_MODULE_CHOICE=yes; shift ;;
    --without-root-module|--without-module) ROOT_MODULE_CHOICE=no; shift ;;
    --force-root-module-build|--force-module-build) FORCE_ROOT_MODULE_BUILD=true; ROOT_MODULE_CHOICE=yes; shift ;;
    --with-clipboard-service) CLIPBOARD_CHOICE=yes; shift ;;
    --without-clipboard-service) CLIPBOARD_CHOICE=no; shift ;;
    --skip-core) INSTALL_CORE=false; shift ;;
    --prefix)
      need_value "$@"
      PREFIX="$2"
      BIN_DIR="$PREFIX/bin"
      MAN_DIR="$PREFIX/share/man/man1"
      SYSTEM_ZSH_DIR="$PREFIX/share/zsh/site-functions"
      ZSH_DIR="$SYSTEM_ZSH_DIR"
      ZSH_DIR_SOURCE="system"
      shift 2
      ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$APP_MODE" in
  auto|desktop|android|none) ;;
  *) printf 'error: invalid --app value: %s\n' "$APP_MODE" >&2; exit 2 ;;
esac

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  "$DRY_RUN" || "$@"
}

# DEVTOOL_COMPLETION_PATH_PATCH_V2
detect_zsh_fpath() {
  local match
  ZSH_FPATH_MATCH=""

  if ! command -v zsh >/dev/null 2>&1; then
    ZSH_FPATH_STATUS="zsh-unavailable"
    return
  fi

  # Canonical comparison recognizes aliases and symlinks such as:
  # ~/.completions/common -> DotCLI's managed completion directory.
  if match=$(
    DEVTOOL_COMPLETION_DIR="$ZSH_DIR" zsh -lic '
      target=${DEVTOOL_COMPLETION_DIR:A}

      for entry in "${fpath[@]}"; do
        if [[ ${entry:A} == "$target" ]]; then
          print -r -u 3 -- "$entry"
          exit 0
        fi
      done

      exit 1
    ' 3>&1 >/dev/null 2>/dev/null
  ); then
    ZSH_FPATH_STATUS="present"
    ZSH_FPATH_MATCH="$match"
  else
    ZSH_FPATH_STATUS="missing"
  fi
}

print_zsh_fpath_status() {
  case "$ZSH_FPATH_STATUS" in
    present)
      printf '  Zsh fpath:         already configured via %s\n' \
        "$ZSH_FPATH_MATCH"
      ;;
    missing)
      printf '  Zsh fpath:         selected directory is not currently present\n'
      ;;
    zsh-unavailable)
      printf '  Zsh fpath:         not checked because zsh is unavailable\n'
      ;;
    *)
      printf '  Zsh fpath:         not checked\n'
      ;;
  esac
}

install_zsh_completion() {
  ZSH_DIR="$SYSTEM_ZSH_DIR"
  ZSH_DIR_SOURCE="system"

  if run mkdir -p "$SYSTEM_ZSH_DIR" &&
     run install -m 0644 \
       "$ROOT/completions/_devtool" \
       "$SYSTEM_ZSH_DIR/_devtool"
  then
    :
  else
    ZSH_DIR="$DOTCLI_ZSH_DIR"
    ZSH_DIR_SOURCE="DotCLI fallback"

    printf \
      'warning: could not install the Zsh completion in the system path: %s\n' \
      "$SYSTEM_ZSH_DIR" >&2
    printf \
      'warning: falling back to DotCLI: %s\n' \
      "$DOTCLI_ZSH_DIR" >&2

    run mkdir -p "$DOTCLI_ZSH_DIR"
    run install -m 0644 \
      "$ROOT/completions/_devtool" \
      "$DOTCLI_ZSH_DIR/_devtool"
  fi

  detect_zsh_fpath
  printf '  Zsh completion:    %s (%s)\n' \
    "$ZSH_DIR" "$ZSH_DIR_SOURCE"
  print_zsh_fpath_status
}

shell_join() {
  local rendered="" quoted arg
  for arg in "$@"; do
    printf -v quoted '%q' "$arg"
    rendered+="${rendered:+ }$quoted"
  done
  printf '%s\n' "$rendered"
}

run_as_root() {
  local command
  command=$(shell_join "$@")
  run su -c "$command"
}

capture_as_root() {
  local command
  "$DRY_RUN" && return 1
  command=$(shell_join "$@")
  su -c "$command" 2>/dev/null
}

ksud_available() {
  command -v su >/dev/null 2>&1 || return 1
  "$DRY_RUN" && return 0
  capture_as_root sh -c 'command -v ksud >/dev/null 2>&1'
}

is_android() {
  [[ -n "${TERMUX_VERSION:-}" || "${PREFIX:-}" == /data/data/com.termux/files/usr || -x /system/bin/pm || -x /system/bin/getprop ]]
}

is_desktop() {
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" || -n "${XDG_CURRENT_DESKTOP:-}" ]]
}

if [[ "$APP_MODE" == auto ]]; then
  if is_android; then APP_MODE=android
  elif is_desktop; then APP_MODE=desktop
  else APP_MODE=none
  fi
fi

ask_yes_no() {
  local prompt=$1 default=${2:-no} answer
  if "$ASSUME_YES"; then return 0; fi
  if [[ ! -t 0 ]]; then [[ "$default" == yes ]]; return; fi
  if [[ "$default" == yes ]]; then
    read -r -p "$prompt [Y/n] " answer || true
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
  else
    read -r -p "$prompt [y/N] " answer || true
    [[ "$answer" =~ ^[Yy] ]]
  fi
}

fingerprint_paths() {
  python3 - "$@" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", ".gradle", ".idea", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".tox", ".nox", "__pycache__", "build", "dist",
    "obj",
}
SKIP_SUFFIXES = {
    ".apk", ".aab", ".aar", ".class", ".deb", ".dex", ".jar",
    ".o", ".obj", ".pyc", ".so", ".zip",
}

hasher = hashlib.sha256()
files: list[tuple[str, Path]] = []
for raw in sys.argv[1:]:
    root = Path(raw).resolve()
    if root.is_file():
        files.append((root.name, root))
        continue
    if not root.is_dir():
        continue
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append((f"{root.name}/{rel.as_posix()}", path))

for label, path in sorted(files, key=lambda item: item[0]):
    hasher.update(label.encode("utf-8", "surrogateescape"))
    hasher.update(b"\0")
    hasher.update(path.read_bytes())
    hasher.update(b"\0")
print(hasher.hexdigest())
PY
}

read_stamp() {
  local path=$1
  if [[ -f "$path" ]]; then
    tr -d '\r\n' < "$path"
  fi
  return 0
}

write_stamp() {
  local path=$1 value=$2
  run mkdir -p "$(dirname "$path")"
  if "$DRY_RUN"; then
    printf '+ write stamp %q = %s\n' "$path" "$value"
  else
    printf '%s\n' "$value" > "$path"
  fi
}

file_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

ensure_stage_dir() {
  if [[ -n "$INSTALL_STAGE_DIR" ]]; then
    return
  fi
  local tmp_root=${TMPDIR:-/tmp}
  if "$DRY_RUN"; then
    INSTALL_STAGE_DIR="$tmp_root/devtool-installer.dry-run"
    return
  fi
  mkdir -p "$tmp_root"
  INSTALL_STAGE_DIR=$(mktemp -d "$tmp_root/devtool-installer.XXXXXX")
  chmod 0755 "$INSTALL_STAGE_DIR"
}

STAGED_FILE=""
stage_for_root() {
  local source=$1 name=$2
  ensure_stage_dir
  STAGED_FILE="$INSTALL_STAGE_DIR/$name"
  run install -m 0644 "$source" "$STAGED_FILE"
}

cleanup_stage() {
  if [[ -n "$INSTALL_STAGE_DIR" && -d "$INSTALL_STAGE_DIR" ]]; then
    rm -rf -- "$INSTALL_STAGE_DIR"
  fi
}
trap cleanup_stage EXIT

printf '\nDevtool installer\n'
printf '  Source:   %s\n' "$ROOT"
printf '  Prefix:   %s\n' "$PREFIX"
printf '  Platform: %s\n\n' "$APP_MODE"

clipboard_unit="$HOME/.config/systemd/user/devtool-control-center-clipboard.service"

# Resolve every interactive choice before changing the system.
if [[ "$APP_CHOICE" == ask && "$APP_MODE" != none ]]; then
  if ask_yes_no "Build if needed and install/update the ${APP_MODE} app?" no; then
    APP_CHOICE=yes
  else
    APP_CHOICE=no
  fi
fi

if [[ "$ROOT_MODULE_CHOICE" == ask ]]; then
  if [[ "$APP_MODE" == android ]] && ksud_available; then
    if ask_yes_no "Build if needed and install/update the optional KernelSU root bridge module?" no; then
      ROOT_MODULE_CHOICE=yes
    else
      ROOT_MODULE_CHOICE=no
    fi
  else
    ROOT_MODULE_CHOICE=no
  fi
fi

if [[ "$ROOT_MODULE_CHOICE" == yes && "$APP_MODE" != android ]]; then
  printf 'error: the root bridge module can be installed only on Android/Termux.\n' >&2
  exit 2
fi

if [[ "$CLIPBOARD_CHOICE" == yes && "$APP_MODE" != desktop ]]; then
  printf 'error: the clipboard monitor service is available only for the desktop Control Center.\n' >&2
  exit 2
fi

if [[ "$APP_MODE" == desktop && "$CLIPBOARD_CHOICE" == ask ]]; then
  CLIPBOARD_CHOICE=no
fi

printf 'Installation plan:\n'
printf '  Core CLI:          %s\n' "$([[ "$INSTALL_CORE" == true ]] && echo install || echo skip)"
printf '  Native app:        %s\n' "$([[ "$APP_CHOICE" == yes ]] && echo "smart build/install $APP_MODE" || echo skip)"
printf '  Root bridge module:%s\n' "$([[ "$ROOT_MODULE_CHOICE" == yes ]] && echo ' smart build/install' || echo ' skip')"
printf '  Clipboard service: %s\n' "$([[ "$CLIPBOARD_CHOICE" == yes ]] && echo install/update || echo unchanged)"
if "$INSTALL_CORE"; then
  printf '  Zsh completion:    %s (primary)\n' "$SYSTEM_ZSH_DIR"
  printf '                     %s (DotCLI fallback)\n' "$DOTCLI_ZSH_DIR"
fi
printf '\n'

if "$INSTALL_CORE"; then
  printf '== Core CLI ==\n'
  run mkdir -p "$BIN_DIR" "$MAN_DIR"
  pip_args=(install -e .)
  if [[ "$PREFIX" == "$HOME/.local" ]]; then
    pip_args=(install --user -e .)
  else
    pip_args=(install --prefix "$PREFIX" -e .)
  fi
  if "$DRY_RUN"; then
    printf '+ (cd %q && python3 -m pip ' "$ROOT"
    printf '%q ' "${pip_args[@]}"
    printf ')\n'
  else
    (cd "$ROOT" && python3 -m pip "${pip_args[@]}")
  fi
  run install -m 0644 "$ROOT/share/man/man1/devtool.1" "$MAN_DIR/devtool.1"
  install_zsh_completion

  if [[ "$APP_MODE" == android && -f "$ROOT/android/devtool-live-hud/tools/devtool-live-hud" ]]; then
    run install -m 0755 "$ROOT/android/devtool-live-hud/tools/devtool-live-hud" "$BIN_DIR/devtool-live-hud"
  fi
  if command -v mandb >/dev/null 2>&1; then run mandb -q "$PREFIX/share/man" || true; fi
fi

desktop_binary() {
  local found
  found=$(command -v devtool-control-center 2>/dev/null || true)
  if [[ -n "$found" ]]; then
    printf '%s\n' "$found"
  elif [[ -x /usr/bin/devtool-control-center ]]; then
    printf '%s\n' /usr/bin/devtool-control-center
  fi
}

desktop_source_fingerprint() {
  fingerprint_paths \
    "$ROOT/devtool/desktop" \
    "$ROOT/desktop/gtk-control-center" \
    "$ROOT/pyproject.toml"
}

desktop_source_version() {
  sed -n 's/^version=${VERSION:-\([^}]*\)}$/\1/p' \
    "$ROOT/desktop/gtk-control-center/package-deb.sh" | head -n 1
}

desktop_installed_version() {
  command -v dpkg-query >/dev/null 2>&1 || return 1
  dpkg-query -W -f='${Version}' devtool-control-center 2>/dev/null
}

desktop_needs_build() {
  "$FORCE_APP_BUILD" && return 0
  local binary current recorded source_version installed_version
  binary=$(desktop_binary || true)
  [[ -z "$binary" || ! -x "$binary" ]] && return 0
  source_version=$(desktop_source_version)
  installed_version=$(desktop_installed_version || true)
  [[ -n "$source_version" && -n "$installed_version" && "$source_version" != "$installed_version" ]] && return 0
  current=$(desktop_source_fingerprint)
  recorded=$(read_stamp "$INSTALLER_STATE_DIR/desktop-app.source.sha256")
  [[ -z "$recorded" || "$current" != "$recorded" ]]
}

install_desktop_app() {
  printf '\n== Desktop Control Center ==\n'
  if desktop_needs_build; then
    local fingerprint
    fingerprint=$(desktop_source_fingerprint)
    printf 'GTK4 desktop source fingerprint changed or the launcher is missing; packaging and installing.\n'
    run "$ROOT/desktop/gtk-control-center/package-deb.sh" --install --delete-after-install
    write_stamp "$INSTALLER_STATE_DIR/desktop-app.source.sha256" "$fingerprint"
  else
    printf 'Build/install not needed: installed GTK4 Control Center integration matches the current source fingerprint.\n'
  fi
}

android_root="$ROOT/android/devtool-live-hud"
android_apk="$android_root/devtool-live-hud-debug.apk"
android_build_apk="$android_root/app/build/outputs/apk/debug/app-debug.apk"
android_debug_package="io.github.mikeceead.devtoolhud.debug"
android_release_package="io.github.mikeceead.devtoolhud"

android_source_fingerprint() {
  fingerprint_paths \
    "$android_root/app/src" \
    "$android_root/app/build.gradle" \
    "$android_root/app/proguard-rules.pro" \
    "$android_root/build.gradle" \
    "$android_root/settings.gradle" \
    "$android_root/gradle.properties" \
    "$android_root/gradle" \
    "$android_root/gradlew"
}

android_needs_build() {
  "$FORCE_APP_BUILD" && return 0
  [[ ! -f "$android_apk" ]] && return 0
  local current recorded
  current=$(android_source_fingerprint)
  recorded=$(read_stamp "$INSTALLER_STATE_DIR/android-app.source.sha256")
  [[ -z "$recorded" || "$current" != "$recorded" ]]
}

build_android_app() {
  local fingerprint chroot_name
  fingerprint=$(android_source_fingerprint)
  chroot_name="${DEVTOOL_ANDROID_CHROOT_NAME:-androidos}"

  printf 'Building Android companion through Devtool chroot target android_hud (%s).\n' "$chroot_name"
  if "$DRY_RUN"; then
    printf '+ (cd %q && env PYTHONPATH=%q python3 -m devtool --copy --auto-hud --hud-mode desktop-window --yes --chroot %q -r %q --target android_hud build)\n' \
      "$ROOT" "$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$chroot_name" "$ROOT"
  else
    (
      cd "$ROOT"
      env "PYTHONPATH=$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m devtool \
          --copy \
          --auto-hud \
          --hud-mode desktop-window \
          --yes \
          --chroot "$chroot_name" \
          -r "$ROOT" \
          --target android_hud \
          build
    )
  fi

  [[ -f "$android_build_apk" || "$DRY_RUN" == true ]] || {
    printf 'error: Devtool did not produce the expected APK: %s\n' "$android_build_apk" >&2
    exit 1
  }
  run install -m 0644 "$android_build_apk" "$android_apk"
  write_stamp "$INSTALLER_STATE_DIR/android-app.source.sha256" "$fingerprint"
}

android_installed_package() {
  local package
  for package in "$android_debug_package" "$android_release_package"; do
    if capture_as_root pm path "$package" | grep -q '^package:'; then
      printf '%s\n' "$package"
      return 0
    fi
  done
  return 1
}

android_installed_apk_hash() {
  local package=$1 apk_path
  apk_path=$(capture_as_root pm path "$package" | sed -n 's/^package://p' | head -n 1)
  [[ -n "$apk_path" ]] || return 1
  capture_as_root sh -c "toybox sha256sum $(shell_join "$apk_path") 2>/dev/null || sha256sum $(shell_join "$apk_path") 2>/dev/null" | awk 'NR == 1 {print $1}'
}

android_needs_install() {
  "$DRY_RUN" && return 0
  local package local_hash installed_hash
  package=$(android_installed_package || true)
  [[ -z "$package" ]] && return 0
  local_hash=$(file_sha256 "$android_apk")
  installed_hash=$(android_installed_apk_hash "$package" || true)
  [[ -z "$installed_hash" || "$installed_hash" != "$local_hash" ]]
}

install_android_app() {
  printf '\n== Android Live HUD ==\n'
  command -v su >/dev/null 2>&1 || {
    printf 'error: su is required to install the Android companion app.\n' >&2
    exit 1
  }

  if android_needs_build; then
    build_android_app
  else
    printf 'Build not needed: tracked APK matches the current Android source fingerprint.\n'
  fi

  [[ -f "$android_apk" || "$DRY_RUN" == true ]] || {
    printf 'error: APK was not produced: %s\n' "$android_apk" >&2
    exit 1
  }

  if android_needs_install; then
    local staged_apk local_hash
    stage_for_root "$android_apk" "devtool-live-hud-debug.apk"
    staged_apk="$STAGED_FILE"
    printf 'Installing staged APK from %s\n' "$staged_apk"
    run_as_root pm install -r "$staged_apk"
    if [[ "$DRY_RUN" != true ]]; then
      local_hash=$(file_sha256 "$android_apk")
      write_stamp "$INSTALLER_STATE_DIR/android-app.installed.sha256" "$local_hash"
    fi
  else
    printf 'Install not needed: the installed companion APK matches the tracked artifact.\n'
  fi
}

root_module_root="$ROOT/root-module/devtool-bridge"
root_module_stamp="$INSTALLER_STATE_DIR/root-module.installed.sha256"

root_module_source_fingerprint() {
  fingerprint_paths "$root_module_root" "$ROOT/build-root-bridge-module.sh"
}

root_module_source_version_code() {
  sed -n 's/^versionCode=//p' "$root_module_root/module.prop" | head -n 1
}

root_module_installed_version_code() {
  capture_as_root sh -c \
    'for p in /data/adb/modules/devtool_bridge/module.prop /data/adb/modules_update/devtool_bridge/module.prop; do
       if test -f "$p"; then
         sed -n "s/^versionCode=//p" "$p"
         exit 0
       fi
     done
     exit 1'
}

root_module_is_installed() {
  capture_as_root sh -c \
    'test -f /data/adb/modules/devtool_bridge/module.prop || test -f /data/adb/modules_update/devtool_bridge/module.prop'
}

root_module_needs_install() {
  "$DRY_RUN" && return 0
  "$FORCE_ROOT_MODULE_BUILD" && return 0
  root_module_is_installed || return 0
  local current recorded source_version installed_version
  source_version=$(root_module_source_version_code)
  installed_version=$(root_module_installed_version_code || true)
  [[ -z "$installed_version" || "$source_version" != "$installed_version" ]] && return 0
  current=$(root_module_source_fingerprint)
  recorded=$(read_stamp "$root_module_stamp")
  [[ -z "$recorded" || "$current" != "$recorded" ]]
}

install_root_module() {
  printf '\n== KernelSU Root Bridge Module ==\n'
  ksud_available || {
    printf 'error: ksud is unavailable through root; KernelSU/KernelSU Next is required.\n' >&2
    exit 1
  }

  if root_module_needs_install; then
    local module_zip fingerprint
    ensure_stage_dir
    module_zip="$INSTALL_STAGE_DIR/devtool_root_bridge_module.zip"
    fingerprint=$(root_module_source_fingerprint)
    printf 'Creating the KernelSU module directly under TMPDIR.\n'
    run "$ROOT/build-root-bridge-module.sh" "$module_zip"
    [[ -s "$module_zip" || "$DRY_RUN" == true ]] || {
      printf 'error: root bridge module was not produced: %s\n' "$module_zip" >&2
      exit 1
    }
    run chmod 0644 "$module_zip"
    printf 'Installing staged KernelSU module from %s\n' "$module_zip"
    run_as_root ksud install "$module_zip"
    write_stamp "$root_module_stamp" "$fingerprint"
    printf 'KernelSU module installed/updated. A reboot may be required.\n'
  else
    printf 'Build/install not needed: the installed root module matches the current source fingerprint.\n'
  fi
}

if [[ "$APP_CHOICE" == yes ]]; then
  case "$APP_MODE" in
    desktop) install_desktop_app ;;
    android) install_android_app ;;
    none) printf 'No supported app platform was detected; skipping app installation.\n' ;;
  esac
fi

if [[ "$ROOT_MODULE_CHOICE" == yes ]]; then
  install_root_module
fi

clipboard_binary="$(command -v devtool-control-center 2>/dev/null || true)"
[[ -n "$clipboard_binary" ]] || clipboard_binary=/usr/bin/devtool-control-center

install_clipboard_service() {
  printf '\n== Clipboard monitor service ==\n'
  printf 'error: the desktop clipboard monitor service is not implemented in this release.\n' >&2
  printf 'error: refusing to install a systemd unit for unsupported --clipboard-monitor.\n' >&2
  exit 2
}

if [[ "$APP_MODE" == desktop && "$CLIPBOARD_CHOICE" == yes ]]; then
  install_clipboard_service
fi

printf '\nInstallation complete.\n'
printf '  CLI: %s/devtool\n' "$BIN_DIR"
if [[ "$APP_MODE" == android && -f "$android_apk" ]]; then
  printf '  Tracked APK: %s\n' "$android_apk"
fi
if [[ "$ROOT_MODULE_CHOICE" == yes ]]; then
  printf '  Root module: installed through ksud when needed\n'
fi
printf '\nShell setup (when not already configured):\n'
printf '  export PATH="$HOME/.local/bin:$PATH"\n'
if "$INSTALL_CORE"; then
  if [[ "$ZSH_FPATH_STATUS" == present ]]; then
    printf '  Zsh completion path is already in fpath via: %s\n' \
      "$ZSH_FPATH_MATCH"
  else
    printf '  fpath=(%q $fpath)\n' "$ZSH_DIR"
  fi
  printf '  autoload -Uz compinit && compinit\n'
fi
