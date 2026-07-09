#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
SRC="$ROOT/root-module/devtool-bridge"
OUT="${1:-$ROOT/devtool_root_bridge_module.zip}"
command -v zip >/dev/null 2>&1 || { echo "zip is required" >&2; exit 1; }
rm -f "$OUT"
(
  cd "$SRC"
  zip -qr "$OUT" . -x '*.DS_Store' '__MACOSX/*'
)
echo "$OUT"
