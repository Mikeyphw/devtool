#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")/.." && pwd)
out_dir=${OUT_DIR:-"$root/dist/release-candidate"}
core_only=false
require_native=false
build_desktop=false
build_android=false

usage() {
  cat <<'USAGE'
Usage: tools/make-release-candidate.sh [options]

Options:
  --core-only       Run only portable qualification gates.
  --require-native  Fail when either Qt 6 or Android SDK gates cannot run.
  --desktop         Build and verify the Debian desktop package.
  --android         Build and verify the signed Android release APK.
  --out-dir PATH    Release-candidate output directory.
  -h, --help        Show this help.

Android release builds require DEVTOOL_RELEASE_STORE_FILE,
DEVTOOL_RELEASE_STORE_PASSWORD, DEVTOOL_RELEASE_KEY_ALIAS, and
DEVTOOL_RELEASE_KEY_PASSWORD.
USAGE
}

while (($#)); do
  case "$1" in
    --core-only) core_only=true; shift ;;
    --require-native) require_native=true; shift ;;
    --desktop) build_desktop=true; shift ;;
    --android) build_android=true; shift ;;
    --out-dir) [[ $# -ge 2 ]] || { echo "Missing value for --out-dir" >&2; exit 2; }; out_dir=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$out_dir"
qualify=(python3 "$root/tools/release-qualify.py" --root "$root" --output "$out_dir/qualification.json")
$core_only && qualify+=(--core-only)
$require_native && qualify+=(--require-qt --require-android)
"${qualify[@]}"

SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1784030400} \
  python3 "$root/tools/build-release.py" --root "$root" --out-dir "$out_dir" --verify-reproducible

if $build_desktop; then
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1784030400} OUT_DIR="$out_dir" \
    "$root/desktop/devtool-control-center/package-deb.sh" --verify
fi

if $build_android; then
  "$root/android/devtool-live-hud/build.sh" \
    --app-dir "$root/android/devtool-live-hud" \
    --assemble-task assembleRelease \
    --tracked-apk "$out_dir/devtool-live-hud-release.apk" \
    --verify-signature \
    --skip-system-deps --skip-sdk-install
fi

(
  cd "$out_dir"
  find . -maxdepth 1 -type f ! -name 'SHA256SUMS*' -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS.all
)
printf 'Release candidate ready: %s\n' "$out_dir"
