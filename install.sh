#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
MAN_DIR="$PREFIX/share/man/man1"
ZSH_DIR="$PREFIX/share/zsh/site-functions"

ASSUME_YES=false
DRY_RUN=false
INSTALL_CORE=true
APP_MODE="auto"          # auto|desktop|android|none
APP_CHOICE="ask"         # ask|yes|no
CLIPBOARD_CHOICE="ask"   # ask|yes|no
FORCE_APP_BUILD=false

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Install devtool and optionally build/install the native app for this platform.

Options:
  --yes                         Accept recommended prompts.
  --app auto|desktop|android|none
                                Select the app platform. With no value, auto-detect
                                the platform and install its app.
  --with-app                    Build if needed and install the selected app.
  --without-app                 Skip app build/install.
  --force-app-build             Rebuild even when the installed app is current.
  --with-clipboard-service      Install or update the desktop clipboard service.
  --without-clipboard-service   Leave the clipboard service unchanged.
  --skip-core                   Skip Python module, man page, and completion.
  --prefix PATH                 Installation prefix (default: $HOME/.local).
  --dry-run                     Print actions without changing the system.
  -h, --help                    Show this help.

Desktop app installation builds a Debian package, installs it, and removes the
package artifact after a successful install. Android installation builds the
tracked APK when needed and installs it with: sudo pm install -r <apk>.
EOF
}

need_value() {
  [[ $# -ge 2 && -n "${2:-}" ]] || { printf 'error: missing value for %s\n' "$1" >&2; exit 2; }
}

while (($#)); do
  case "$1" in
    --yes|-y) ASSUME_YES=true; shift ;;
    --app)
      # Bare --app means: auto-detect the platform and install its app.
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
    --with-clipboard-service) CLIPBOARD_CHOICE=yes; shift ;;
    --without-clipboard-service) CLIPBOARD_CHOICE=no; shift ;;
    --skip-core) INSTALL_CORE=false; shift ;;
    --prefix) need_value "$@"; PREFIX="$2"; BIN_DIR="$PREFIX/bin"; MAN_DIR="$PREFIX/share/man/man1"; ZSH_DIR="$PREFIX/share/zsh/site-functions"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$APP_MODE" in auto|desktop|android|none) ;; *) printf 'error: invalid --app value: %s\n' "$APP_MODE" >&2; exit 2 ;; esac

run() {
  printf '+ '; printf '%q ' "$@"; printf '\n'
  "$DRY_RUN" || "$@"
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

printf '\nDevtool installer\n'
printf '  Source:   %s\n' "$ROOT"
printf '  Prefix:   %s\n' "$PREFIX"
printf '  Platform: %s\n\n' "$APP_MODE"

clipboard_unit="$HOME/.config/systemd/user/devtool-control-center-clipboard.service"

# Resolve every interactive choice before changing the system so the user sees
# one coherent installation plan rather than prompts appearing mid-install.
if [[ "$APP_CHOICE" == ask && "$APP_MODE" != none ]]; then
  if ask_yes_no "Build if needed and install the ${APP_MODE} app?" no; then APP_CHOICE=yes; else APP_CHOICE=no; fi
fi
if [[ "$CLIPBOARD_CHOICE" == yes && "$APP_MODE" != desktop ]]; then
  printf 'error: the clipboard monitor service is available only for the desktop Control Center.\n' >&2
  exit 2
fi
if [[ "$APP_MODE" == desktop && "$CLIPBOARD_CHOICE" == ask ]]; then
  verb=Install
  clipboard_default=no
  if [[ -f "$clipboard_unit" ]]; then verb=Update; clipboard_default=yes; fi
  if ask_yes_no "$verb and enable the clipboard monitor service?" "$clipboard_default"; then CLIPBOARD_CHOICE=yes; else CLIPBOARD_CHOICE=no; fi
fi

printf 'Installation plan:\n'
printf '  Core CLI:          %s\n' "$([[ "$INSTALL_CORE" == true ]] && echo install || echo skip)"
printf '  Native app:        %s\n' "$([[ "$APP_CHOICE" == yes ]] && echo "build/install $APP_MODE" || echo skip)"
printf '  Clipboard service: %s\n\n' "$([[ "$CLIPBOARD_CHOICE" == yes ]] && echo install/update || echo unchanged)"

if "$INSTALL_CORE"; then
  printf '== Core CLI ==\n'
  run mkdir -p "$BIN_DIR" "$MAN_DIR" "$ZSH_DIR"
  pip_args=(install -e .)
  if [[ "$PREFIX" == "$HOME/.local" ]]; then pip_args=(install --user -e .)
  else pip_args=(install --prefix "$PREFIX" -e .)
  fi
  if "$DRY_RUN"; then
    printf '+ (cd %q && python3 -m pip ' "$ROOT"
    printf '%q ' "${pip_args[@]}"
    printf ')\n'
  else
    (cd "$ROOT" && python3 -m pip "${pip_args[@]}")
  fi
  run install -m 0644 "$ROOT/share/man/man1/devtool.1" "$MAN_DIR/devtool.1"
  run install -m 0644 "$ROOT/completions/_devtool" "$ZSH_DIR/_devtool"

  if [[ "$APP_MODE" == android && -f "$ROOT/android/devtool-live-hud/tools/devtool-live-hud" ]]; then
    run install -m 0755 "$ROOT/android/devtool-live-hud/tools/devtool-live-hud" "$BIN_DIR/devtool-live-hud"
  fi
  if command -v mandb >/dev/null 2>&1; then run mandb -q "$PREFIX/share/man" || true; fi
fi

desktop_binary() {
  local found
  found=$(command -v devtool-control-center 2>/dev/null || true)
  if [[ -n "$found" ]]; then printf '%s\n' "$found"
  elif [[ -x /usr/bin/devtool-control-center ]]; then printf '%s\n' /usr/bin/devtool-control-center
  fi
}

desktop_needs_install() {
  "$FORCE_APP_BUILD" && return 0
  local binary
  binary=$(desktop_binary || true)
  [[ -z "$binary" || ! -x "$binary" ]] && return 0
  find "$ROOT/desktop/devtool-control-center" \
    -path '*/build' -prune -o -path '*/dist' -prune -o \
    -type f \( -name '*.cpp' -o -name '*.h' -o -name '*.qml' -o -name 'CMakeLists.txt' -o -path '*/packaging/*' \) \
    -newer "$binary" -print -quit | grep -q .
}

install_desktop_app() {
  printf '\n== Desktop Control Center ==\n'
  if desktop_needs_install; then
    run "$ROOT/desktop/devtool-control-center/package-deb.sh" --install --delete-after-install
  else
    printf 'Build not needed: the installed Control Center is newer than the source tree.\n'
  fi
}

android_apk="$ROOT/android/devtool-live-hud/devtool-live-hud-debug.apk"
android_needs_build() {
  [[ ! -f "$android_apk" ]] && return 0
  find "$ROOT/android/devtool-live-hud" \
    -path '*/build' -prune -o -path '*/.gradle' -prune -o \
    -type f \( -name '*.java' -o -name '*.kt' -o -name '*.xml' -o -name '*.gradle' -o -name '*.properties' \) \
    -newer "$android_apk" -print -quit | grep -q .
}

install_android_app() {
  printf '\n== Android Live HUD ==\n'
  if android_needs_build; then
    printf 'Tracked APK is missing or older than the Android sources; rebuilding.\n'
    run "$ROOT/android/devtool-live-hud/build.sh" \
      --app-dir "$ROOT/android/devtool-live-hud" \
      --tracked-apk "$android_apk"
  else
    printf 'Build not needed: %s is current.\n' "$android_apk"
  fi
  [[ -f "$android_apk" || "$DRY_RUN" == true ]] || { printf 'error: APK was not produced: %s\n' "$android_apk" >&2; exit 1; }
  run sudo pm install -r "$android_apk"
}

if [[ "$APP_CHOICE" == yes ]]; then
  case "$APP_MODE" in
    desktop) install_desktop_app ;;
    android) install_android_app ;;
    none) printf 'No supported app platform was detected; skipping app installation.\n' ;;
  esac
fi

clipboard_binary="$(command -v devtool-control-center 2>/dev/null || true)"
[[ -n "$clipboard_binary" ]] || clipboard_binary=/usr/bin/devtool-control-center

install_clipboard_service() {
  local action=Install
  if [[ ! -x "$clipboard_binary" && "$DRY_RUN" != true ]]; then
    printf 'error: devtool-control-center is not installed; install the desktop app before enabling its clipboard service.\n' >&2
    exit 2
  fi
  [[ -f "$clipboard_unit" ]] && action=Update
  printf '\n== Clipboard monitor service ==\n'
  printf '%s user service: %s\n' "$action" "$clipboard_unit"
  run mkdir -p "$(dirname "$clipboard_unit")"
  if "$DRY_RUN"; then
    printf '+ write %q\n' "$clipboard_unit"
  else
    cat > "$clipboard_unit" <<EOF
[Unit]
Description=Devtool Control Center clipboard monitor
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart="$clipboard_binary" --clipboard-monitor
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF
  fi
  if command -v systemctl >/dev/null 2>&1; then
    run systemctl --user daemon-reload
    run systemctl --user enable --now devtool-control-center-clipboard.service
  else
    printf 'warning: systemctl is unavailable; the service file was written but not started.\n' >&2
  fi
}

if [[ "$APP_MODE" == desktop && "$CLIPBOARD_CHOICE" == yes ]]; then
  install_clipboard_service
fi

printf '\nInstallation complete.\n'
printf '  CLI: %s/devtool\n' "$BIN_DIR"
if [[ "$APP_MODE" == android && -f "$android_apk" ]]; then printf '  Tracked APK: %s\n' "$android_apk"; fi
printf '\nShell setup (when not already configured):\n'
printf '  export PATH="$HOME/.local/bin:$PATH"\n'
printf '  fpath=("$HOME/.local/share/zsh/site-functions" $fpath)\n'
printf '  autoload -Uz compinit && compinit\n'
