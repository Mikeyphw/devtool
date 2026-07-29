#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../build-android-lib.sh"

DEFAULT_OUTPUT_NAME="devtool-android-hud"
parse_output_dir "$@"
build_android_target "$SCRIPT_DIR" "android_hud" "$OUTPUT_DIR"
