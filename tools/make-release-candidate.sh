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
  --require-native  Fail when either GTK4/libadwaita or Android SDK gates cannot run.
  --desktop         Build and verify the Debian desktop package.
  --android         Build and verify the signed Android release APK.
  --out-dir PATH    Release-candidate output directory.
  -h, --help        Show this help.

The release candidate always includes the Python wheel/sdist, deterministic
root bridge module, deterministic source archive, one release manifest, and
SHA256SUMS/SHA256SUMS.all generated from that manifest. Desktop and Android
assets are added when their flags are requested.
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
if [[ -z ${SOURCE_DATE_EPOCH:-} ]]; then
  SOURCE_DATE_EPOCH=$(git -C "$root" log -1 --format=%ct HEAD 2>/dev/null || true)
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}
fi
export SOURCE_DATE_EPOCH

qualify=(python3 "$root/tools/release-qualify.py" --root "$root" --output "$out_dir/qualification.json" --final)
$core_only && qualify+=(--core-only)
$require_native && qualify+=(--require-desktop --require-android)
"${qualify[@]}"

# Build distributable Python artifacts before the final manifest is sealed.
python3 -m build --outdir "$out_dir" "$root"
"$root/build-root-bridge-module.sh" "$out_dir/devtool-root-bridge-module.zip"

if $build_desktop; then
  OUT_DIR="$out_dir" "$root/desktop/gtk-control-center/package-deb.sh" --verify
fi

if $build_android; then
  "$root/android/devtool-live-hud/build.sh" \
    --app-dir "$root/android/devtool-live-hud" \
    --assemble-task assembleRelease \
    --tracked-apk "$out_dir/devtool-live-hud-release.apk" \
    --verify-signature \
    --skip-system-deps --skip-sdk-install
fi

# Seal all produced release assets into one authoritative manifest/checksum set.
release_manifest=(python3 "$root/tools/build-release.py" --root "$root" --out-dir "$out_dir" --verify-reproducible)
$build_android && release_manifest+=(--require-apk-signature)
"${release_manifest[@]}"
printf 'Release candidate ready: %s\n' "$out_dir"
