#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
apk="$root/devtool-live-hud-debug.apk"
if [[ ! -f "$apk" ]]; then
  "$root/build.sh" --app-dir "$root" --tracked-apk "$apk"
fi
sudo pm install -r "$apk"
