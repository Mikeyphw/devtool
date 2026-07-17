#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
version=${VERSION:-0.4.0}
arch=${ARCH:-$(dpkg --print-architecture)}
cache_root=${XDG_CACHE_HOME:-"$HOME/.cache"}
build=${BUILD_DIR:-"$cache_root/devtool-control-center/package-build-${version}-${arch}"}
out=${OUT_DIR:-"$root/dist"}
install_after_build=false
delete_after_install=false
skip_build=false
verify_package=false
source_date_epoch=${SOURCE_DATE_EPOCH:-1784030400}
rm_bin=${RM_BIN:-/bin/rm}

usage() {
  cat <<'EOF'
Usage: ./package-deb.sh [options]

Options:
  --install               Install the generated package after building it.
  --delete-after-install  Delete the .deb only after installation succeeds.
  --skip-build            Package an existing BUILD_DIR without rebuilding.
  --verify                Verify metadata, payload, and checksum after packaging.
  -h, --help              Show this help.

Environment: VERSION, ARCH, BUILD_DIR, OUT_DIR, KEEP_PACKAGE_BUILD, SOURCE_DATE_EPOCH.
EOF
}

while (($#)); do
  case "$1" in
    --install) install_after_build=true; shift ;;
    --delete-after-install) delete_after_install=true; shift ;;
    --skip-build) skip_build=true; shift ;;
    --verify) verify_package=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if "$delete_after_install" && ! "$install_after_build"; then
  printf 'error: --delete-after-install requires --install\n' >&2
  exit 2
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/devtool-control-center-deb.XXXXXX")
trap '"$rm_bin" -rf "$stage"' EXIT

deb_path="$out/devtool-control-center_${version}_${arch}.deb"

required_packaging_files=(
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.desktop"
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.svg"
  "$root/packaging/icons/64x64/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/icons/128x128/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/icons/256x256/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/debian/control"
  "$root/packaging/debian/postinst"
  "$root/packaging/debian/postrm"
)
for required_file in "${required_packaging_files[@]}"; do
  [[ -f "$required_file" ]] || { printf 'error: required packaging asset is missing: %s\n' "$required_file" >&2; exit 2; }
done

required_qml_packages=(
  qml6-module-qtqml qml6-module-qtqml-models qml6-module-qtqml-workerscript
  qml6-module-qtquick qml6-module-qtquick-window qml6-module-qtquick-templates
  qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-dialogs
)
required_build_packages=(
  cmake ninja-build g++ qt6-base-dev qt6-declarative-dev
)
control_file="$root/packaging/debian/control"
depends_line=$(grep '^Depends:' "$control_file" || true)
for qml_package in "${required_qml_packages[@]}"; do
  if [[ ",${depends_line#Depends:}," != *", $qml_package,"* && ",${depends_line#Depends:}," != *",$qml_package,"* ]]; then
    printf 'error: Debian control is missing required QML runtime dependency: %s\n' "$qml_package" >&2
    exit 2
  fi
done

if ! "$skip_build" && command -v dpkg-query >/dev/null 2>&1; then
  missing_build_packages=()
  for build_package in "${required_build_packages[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "$build_package" 2>/dev/null | grep -q '^ii '; then
      missing_build_packages+=("$build_package")
    fi
  done
  if ((${#missing_build_packages[@]})); then
    printf 'error: missing required build package(s): %s\n' "${missing_build_packages[*]}" >&2
    printf 'Install them with:\n  sudo apt install %s\n' "${missing_build_packages[*]}" >&2
    exit 2
  fi
fi

printf '== Devtool Control Center package ==\n'
printf 'Version: %s\nArchitecture: %s\nBuild: %s\nOutput: %s\n\n' "$version" "$arch" "$build" "$deb_path"

if ! "$skip_build"; then
  # A clean out-of-source build prevents stale QML-generated files and CMake
  # CONFIGURE_DEPENDS loops after source-tree upgrades.
  if [[ ${KEEP_PACKAGE_BUILD:-0} != 1 ]]; then "$rm_bin" -rf "$build"; fi
  BUILD_DIR="$build" "$root/build.sh"
elif [[ ! -f "$build/cmake_install.cmake" ]]; then
  printf 'error: --skip-build requested but BUILD_DIR is not configured: %s\n' "$build" >&2
  exit 2
fi

DESTDIR="$stage" cmake --install "$build" --prefix /usr
mkdir -p "$stage/DEBIAN" "$out"
sed \
  -e "s/^Version:.*/Version: $version/" \
  -e "s/^Architecture:.*/Architecture: $arch/" \
  "$control_file" > "$stage/DEBIAN/control"
install -m 0755 "$root/packaging/debian/postinst" "$stage/DEBIAN/postinst"
install -m 0755 "$root/packaging/debian/postrm" "$stage/DEBIAN/postrm"

# Normalize the package tree so identical inputs and SOURCE_DATE_EPOCH produce
# byte-for-byte identical Debian archives.
find "$stage" -print0 | xargs -0 touch -h -d "@$source_date_epoch"
dpkg-deb --root-owner-group --uniform-compression -Zxz --build "$stage" "$deb_path"
sha256sum "$deb_path" > "$deb_path.sha256"
printf '
Built package: %s
Checksum: %s.sha256
' "$deb_path" "$deb_path"

if "$verify_package"; then
  dpkg-deb --info "$deb_path" >/dev/null
  contents=$(dpkg-deb --contents "$deb_path")
  [[ "$contents" == *"./usr/bin/devtool-control-center"* ]] || { echo "error: desktop executable missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/bin/devtool-control-center-hud"* ]] || { echo "error: HUD executable missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/share/applications/io.github.mikeceead.DevtoolControlCenter.desktop"* ]] || { echo "error: desktop entry missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/share/icons/hicolor/128x128/apps/io.github.mikeceead.DevtoolControlCenter.png"* ]] || { echo "error: raster icon missing from package" >&2; exit 1; }
  package_version=$(dpkg-deb -f "$deb_path" Version)
  [[ "$package_version" == "$version" ]] || { echo "error: package version mismatch: $package_version" >&2; exit 1; }
  sha256sum -c "$deb_path.sha256"
  printf 'Package verification passed.
'
fi

if "$install_after_build"; then
  if command -v apt-get >/dev/null 2>&1; then
    install_cmd=(apt-get install -y "$deb_path")
  elif command -v dpkg >/dev/null 2>&1; then
    install_cmd=(dpkg -i "$deb_path")
  else
    printf 'error: neither apt-get nor dpkg is available\n' >&2
    exit 2
  fi
  if ((EUID != 0)); then
    command -v sudo >/dev/null 2>&1 || { printf 'error: sudo is required to install the package\n' >&2; exit 2; }
    install_cmd=(sudo "${install_cmd[@]}")
  fi

  printf 'Installing package…\n'
  "${install_cmd[@]}"
  printf 'Installed Devtool Control Center successfully.\n'

  if "$delete_after_install"; then
    "$rm_bin" -f -- "$deb_path"
    printf 'Deleted package artifact after successful installation: %s\n' "$deb_path"
  fi
fi
