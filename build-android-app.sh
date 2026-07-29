#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=""
OUTPUT_NAME="devtool-android-hud"

usage() {
  cat <<EOF
Usage: build-android-app.sh [-o OUTPUT_DIR] [-n OUTPUT_NAME]

Build the devtool Android HUD APK with devtool build.

Options:
  -o OUTPUT_DIR  Copy the APK to OUTPUT_DIR.
  -n OUTPUT_NAME APK filename without the .apk suffix.
  -h             Show this help.
EOF
}

while getopts ":o:n:h" opt; do
  case "$opt" in
    o)
      OUTPUT_DIR="$OPTARG"
      ;;
    n)
      OUTPUT_NAME="$OPTARG"
      ;;
    h)
      usage
      exit 0
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      usage >&2
      exit 2
      ;;
    \?)
      echo "Unknown option: -$OPTARG" >&2
      usage >&2
      exit 2
      ;;
  esac
done

shift $((OPTIND - 1))
if (($#)); then
  echo "Unexpected arguments: $*" >&2
  usage >&2
  exit 2
fi

echo "==> $SCRIPT_DIR (android_hud)"
devtool -r "$SCRIPT_DIR" --target android_hud build

APK="$SCRIPT_DIR/android/devtool-live-hud/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "$APK" ]]; then
  echo "APK not found after build: $APK" >&2
  exit 1
fi

if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
  cp "$APK" "$OUTPUT_DIR/$OUTPUT_NAME.apk"
  echo "APK copied to $OUTPUT_DIR/$OUTPUT_NAME.apk"
else
  echo "APK built at $APK"
fi
