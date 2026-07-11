#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
version=${VERSION:-0.3.7}
arch=${ARCH:-$(dpkg --print-architecture)}
cache_root=${XDG_CACHE_HOME:-"$HOME/.cache"}
build=${BUILD_DIR:-"$cache_root/devtool-control-center/package-build-${version}-${arch}"}
out=${OUT_DIR:-"$root/dist"}
stage=$(mktemp -d "${TMPDIR:-/tmp}/devtool-control-center-deb.XXXXXX")
trap 'rm -rf "$stage"' EXIT

required_packaging_files=(
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.desktop"
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.svg"
  "$root/packaging/debian/control"
)
for required_file in "${required_packaging_files[@]}"; do
  if [[ ! -f "$required_file" ]]; then
    printf 'error: required packaging asset is missing: %s\n' "$required_file" >&2
    exit 2
  fi
done

required_qml_packages=(
  qml6-module-qtqml
  qml6-module-qtqml-models
  qml6-module-qtqml-workerscript
  qml6-module-qtquick
  qml6-module-qtquick-window
  qml6-module-qtquick-templates
  qml6-module-qtquick-controls
  qml6-module-qtquick-layouts
  qml6-module-qtquick-dialogs
)
control_file="$root/packaging/debian/control"
depends_line=$(grep '^Depends:' "$control_file" || true)
for qml_package in "${required_qml_packages[@]}"; do
  if [[ ",${depends_line#Depends:}," != *", $qml_package,"* && ",${depends_line#Depends:}," != *",$qml_package,"* ]]; then
    printf 'error: Debian control is missing required QML runtime dependency: %s\n' "$qml_package" >&2
    exit 2
  fi
done

# Packaging uses its own clean out-of-source build. Keeping generated Qt/QML
# files outside the source tree avoids CMake CONFIGURE_DEPENDS feedback loops,
# and a clean cache prevents stale source-directory metadata after upgrades.
if [[ ${KEEP_PACKAGE_BUILD:-0} != 1 ]]; then
  rm -rf "$build"
fi
BUILD_DIR="$build" "$root/build.sh"

DESTDIR="$stage" cmake --install "$build" --prefix /usr
mkdir -p "$stage/DEBIAN" "$out"
sed \
  -e "s/^Version:.*/Version: $version/" \
  -e "s/^Architecture:.*/Architecture: $arch/" \
  "$root/packaging/debian/control" > "$stage/DEBIAN/control"

dpkg-deb --root-owner-group --build \
  "$stage" \
  "$out/devtool-control-center_${version}_${arch}.deb"
printf 'Built: %s\n' "$out/devtool-control-center_${version}_${arch}.deb"
