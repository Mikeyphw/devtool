#!/usr/bin/env bash
set -euo pipefail

[[ $# -ge 1 ]] || { echo "usage: run-headless-smoke.sh EXECUTABLE [ARG...]" >&2; exit 2; }
runtime=$(mktemp -d "${TMPDIR:-/tmp}/devtool-qt-runtime.XXXXXX")
trap 'rm -rf "$runtime"' EXIT
chmod 700 "$runtime"
export XDG_RUNTIME_DIR="$runtime"
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
export QT_QUICK_BACKEND=${QT_QUICK_BACKEND:-software}
export QSG_RHI_BACKEND=${QSG_RHI_BACKEND:-software}
export QT_LOGGING_RULES=${QT_LOGGING_RULES:-"qt.qml.import.warning=false"}
exec timeout 15 "$@"
