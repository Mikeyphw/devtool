#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
python_bin=${PYTHON:-python3}

"$python_bin" -m compileall -q "$root/../../devtool/desktop"
printf 'GTK4 Control Center is Python/PyGObject based; no native build step is required.\n'
printf 'Smoke test with: devtool control-center --backend gtk4 --once\n'
