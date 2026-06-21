#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
MAN_DIR="$PREFIX/share/man/man1"
ZSH_DIR="$PREFIX/share/zsh/site-functions"

python3 -m pip uninstall -y repo-devtool || true
rm -f "$MAN_DIR/devtool.1"
rm -f "$ZSH_DIR/_devtool"

echo "Uninstalled repo-devtool."
