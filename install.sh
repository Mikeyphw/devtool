#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
MAN_DIR="$PREFIX/share/man/man1"
ZSH_DIR="$PREFIX/share/zsh/site-functions"

mkdir -p "$BIN_DIR" "$MAN_DIR" "$ZSH_DIR"

python3 -m pip install --user -e .

install -m 0644 share/man/man1/devtool.1 "$MAN_DIR/devtool.1"
install -m 0644 completions/_devtool "$ZSH_DIR/_devtool"

if command -v mandb >/dev/null 2>&1; then
  mandb -q "$PREFIX/share/man" || true
fi

cat <<EOF
Installed:
  python module via pip --user
  man page -> $MAN_DIR/devtool.1
  zsh completion -> $ZSH_DIR/_devtool

Make sure these are available in your shell:
  export PATH="$HOME/.local/bin:$PATH"
  fpath=("$HOME/.local/share/zsh/site-functions" \$fpath)
  autoload -Uz compinit && compinit

If you use fzf, the completion script will use it when available.
EOF
