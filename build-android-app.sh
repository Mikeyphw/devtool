#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR=""
OUTPUT_NAME="devtool-android-hud"

usage() {
  cat <<EOF
Usage: build-android-app.sh [-o OUTPUT_DIR] [-n OUTPUT_NAME]

Build the devtool Android HUD APK through Devtool's managed artifact pipeline.

Options:
  -o OUTPUT_DIR  Managed artifact destination (default: .devtool/build-artifacts).
  -n OUTPUT_NAME APK filename base.
  -h             Show this help.
EOF
}

while getopts ":o:n:h" opt; do
  case "$opt" in
    o) OUTPUT_DIR="$OPTARG" ;;
    n) OUTPUT_NAME="$OPTARG" ;;
    h) usage; exit 0 ;;
    :) echo "Option -$OPTARG requires an argument." >&2; usage >&2; exit 2 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
if (($#)); then
  echo "Unexpected arguments: $*" >&2
  usage >&2
  exit 2
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$SCRIPT_DIR/.devtool/build-artifacts"
fi

echo "==> $SCRIPT_DIR (android_hud)"
RUN_ID="android-hud-wrapper-$(date +%s)-$$"
DEVTOOL_RUN_ID="$RUN_ID" devtool -r "$SCRIPT_DIR" --target android_hud build \
  --output-dir "$OUTPUT_DIR" --output-name "$OUTPUT_NAME" --artifact-collision unique --verify-artifacts

APK=$(python3 - "$SCRIPT_DIR" "$RUN_ID" <<'PY'
from pathlib import Path
import json
import sys
root = Path(sys.argv[1]).resolve()
run_id = sys.argv[2]
path = root / ".devtool" / "artifacts" / "registry.json"
if not path.is_file():
    raise SystemExit(1)
data = json.loads(path.read_text(encoding="utf-8"))
for item in data.get("items", []):
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if item.get("valid") is False or str(item.get("type", "")).lower() != "apk":
        continue
    if str(item.get("run_id") or "") != run_id:
        continue
    if metadata.get("target") not in {None, "", "android_hud"}:
        continue
    candidate = Path(str(item.get("path") or ""))
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_file():
        print(candidate.resolve())
        break
else:
    raise SystemExit(1)
PY
) || { echo "No verified APK was registered after the build." >&2; exit 1; }
printf 'APK built and verified at %s\n' "$APK"
