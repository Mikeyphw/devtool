#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
SKIP_PIP=false
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: ./uninstall.sh [options]

Options:
  --prefix PATH   Installation prefix to clean (default: $HOME/.local).
  --skip-pip      Do not invoke pip uninstall for the Python package.
  --dry-run       Print removals without changing the system.
  -h, --help      Show this help.
USAGE
}

need_value() {
  [[ $# -ge 2 && -n "${2:-}" ]] || { printf 'error: missing value for %s\n' "$1" >&2; exit 2; }
}

while (($#)); do
  case "$1" in
    --prefix) need_value "$@"; PREFIX="$2"; shift 2 ;;
    --skip-pip) SKIP_PIP=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

MAN_DIR="$PREFIX/share/man/man1"
SYSTEM_ZSH_DIR="$PREFIX/share/zsh/site-functions"
DOTCLI_ZSH_DIR="$HOME/.local/share/dotcli/dotfiles/common/completions"
BIN_DIR="$PREFIX/bin"

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  "$DRY_RUN" || "$@"
}

if ! "$SKIP_PIP"; then
  if "$DRY_RUN"; then
    printf '+ python3 -m pip uninstall -y devtool\n'
  else
    python3 -m pip uninstall -y devtool || true
  fi
fi

run rm -f \
  "$MAN_DIR/devtool.1" \
  "$SYSTEM_ZSH_DIR/_devtool" \
  "$DOTCLI_ZSH_DIR/_devtool" \
  "$BIN_DIR/devtool-live-hud" \
  "$BIN_DIR/devtool-control-center" \
  "$BIN_DIR/devtool-control-center-hud" \
  "$BIN_DIR/devtool-gtk-control-center" \
  "$BIN_DIR/devtool-gtk-hud"

echo "Uninstalled devtool from prefix: $PREFIX"
