#!/usr/bin/env bash
set -euo pipefail
# Compatibility launcher only. Gate truth is owned by one canonical EXO run in
# `devtool oci gate1`; this script intentionally maintains no lifecycle state.
PROFILE="${1:-${DEVTOOL_OCI03_SMOKE_PROFILE:-}}"
CONTAINER="${2:-${DEVTOOL_OCI03_SMOKE_CONTAINER:-}}"
PYTHON_BIN="${DEVTOOL_PYTHON:-python3}"
REPO_ROOT="${DEVTOOL_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
args=( -m devtool oci gate1 )
if [ -n "$PROFILE" ]; then args+=( "$PROFILE" ); fi
if [ -n "$CONTAINER" ]; then args+=( --container "$CONTAINER" ); fi
exec "$PYTHON_BIN" "${args[@]}"
