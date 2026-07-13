#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
version=${VERSION:-0.3.8}
arch=${ARCH:-$(dpkg --print-architecture)}
cache_root=${XDG_CACHE_HOME:-"$HOME/.cache"}
build=${BUILD_DIR:-"$cache_root/devtool-control-center/package-build-${version}-${arch}"}
out=${OUT_DIR:-"$root/dist"}
install_after_build=false
delete_after_install=false
skip_build=false

usage() {
  cat <<'EOF'
Usage: ./package-deb.sh [options]

Options:
  --install               Install the generated package after building it.
  --delete-after-install  Delete the .deb only after installation succeeds.
  --skip-build            Package an existing BUILD_DIR without rebuilding.
  -h, --help              Show this help.

Environment: VERSION, ARCH, BUILD_DIR, OUT_DIR, KEEP_PACKAGE_BUILD.
EOF
}

while (($#)); do
  case "$1" in
    --install) install_after_build=true; shift ;;
    --delete-after-install) delete_after_install=true; shift ;;
    --skip-build) skip_build=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if "$delete_after_install" && ! "$install_after_build"; then
  printf 'error: --delete-after-install requires --install\n' >&2
  exit 2
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/devtool-control-center-deb.XXXXXX")
trap 'rm -rf "$stage"' EXIT

deb_path="$out/devtool-control-center_${version}_${arch}.deb"

required_packaging_files=(
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.desktop"
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.svg"
  "$root/packaging/debian/control"
)
for required_file in "${required_packaging_files[@]}"; do
  [[ -f "$required_file" ]] || { printf 'error: required packaging asset is missing: %s\n' "$required_file" >&2; exit 2; }
done

required_qml_packages=(
  qml6-module-qtqml qml6-module-qtqml-models qml6-module-qtqml-workerscript
  qml6-module-qtquick qml6-module-qtquick-window qml6-module-qtquick-templates
  qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-qtquick-dialogs
)
control_file="$root/packaging/debian/control"
depends_line=$(grep '^Depends:' "$control_file" || true)
for qml_package in "${required_qml_packages[@]}"; do
  if [[ ",${depends_line#Depends:}," != *", $qml_package,"* && ",${depends_line#Depends:}," != *",$qml_package,"* ]]; then
    printf 'error: Debian control is missing required QML runtime dependency: %s\n' "$qml_package" >&2
    exit 2
  fi
done

printf '== Devtool Control Center package ==\n'
printf 'Version: %s\nArchitecture: %s\nBuild: %s\nOutput: %s\n\n' "$version" "$arch" "$build" "$deb_path"

if ! "$skip_build"; then
  # A clean out-of-source build prevents stale QML-generated files and CMake
  # CONFIGURE_DEPENDS loops after source-tree upgrades.
  if [[ ${KEEP_PACKAGE_BUILD:-0} != 1 ]]; then rm -rf "$build"; fi
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

dpkg-deb --root-owner-group --build "$stage" "$deb_path"
printf '\nBuilt package: %s\n' "$deb_path"

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
    rm -f -- "$deb_path"
    printf 'Deleted package artifact after successful installation: %s\n' "$deb_path"
  fi
fi
