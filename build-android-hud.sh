#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/android/devtool-live-hud/build.sh" \
  --app-dir "$ROOT/android/devtool-live-hud" \
  "$@"
