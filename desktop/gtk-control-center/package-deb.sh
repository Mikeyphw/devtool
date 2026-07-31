#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$root/../.." && pwd)
version=${VERSION:-0.5.0}
arch=${ARCH:-all}
out=${OUT_DIR:-"$root/dist"}
source_date_epoch=${SOURCE_DATE_EPOCH:-1784030400}
rm_bin=${RM_BIN:-/bin/rm}
python_bin=${PYTHON:-python3}
install_after_build=false
delete_after_install=false
verify_package=false

usage() {
  cat <<'EOF'
Usage: ./package-deb.sh [options]

Options:
  --install               Install the generated package after building it.
  --delete-after-install  Delete the .deb only after installation succeeds.
  --verify                Verify metadata, payload, dependency set, and checksum.
  -h, --help              Show this help.

Environment: VERSION, ARCH, OUT_DIR, SOURCE_DATE_EPOCH, PYTHON, RM_BIN.
EOF
}

while (($#)); do
  case "$1" in
    --install) install_after_build=true; shift ;;
    --delete-after-install) delete_after_install=true; shift ;;
    --verify) verify_package=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if "$delete_after_install" && ! "$install_after_build"; then
  printf 'error: --delete-after-install requires --install\n' >&2
  exit 2
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/devtool-gtk-control-center-deb.XXXXXX")
trap '"$rm_bin" -rf "$stage"' EXIT
mkdir -p "$out"
deb_path="$out/devtool-control-center_${version}_${arch}.deb"

required_packaging_files=(
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.desktop"
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.metainfo.xml"
  "$root/packaging/io.github.mikeceead.DevtoolControlCenter.svg"
  "$root/packaging/icons/64x64/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/icons/128x128/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/icons/256x256/io.github.mikeceead.DevtoolControlCenter.png"
  "$root/packaging/debian/control"
  "$root/packaging/debian/postinst"
  "$root/packaging/debian/postrm"
)
for required_file in "${required_packaging_files[@]}"; do
  [[ -f "$required_file" ]] || { printf 'error: required GTK packaging asset is missing: %s\n' "$required_file" >&2; exit 2; }
done

control_file="$root/packaging/debian/control"
depends_line=$(grep '^Depends:' "$control_file" || true)
required_gtk_packages=(python3 python3-gi python3-rich gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0)
for package_name in "${required_gtk_packages[@]}"; do
  if [[ ",${depends_line#Depends:}," != *", $package_name,"* && ",${depends_line#Depends:}," != *",$package_name,"* && ",${depends_line#Depends:}," != *", $package_name ("* ]]; then
    printf 'error: Debian control is missing required GTK runtime dependency: %s\n' "$package_name" >&2
    exit 2
  fi
done
for forbidden in qml-module-qtquick2 libqt5quick5 libqt5widgets5 qtbase5-dev qtdeclarative5-dev; do
  if grep -q "$forbidden" "$control_file"; then
    printf 'error: GTK package still references legacy Qt dependency: %s\n' "$forbidden" >&2
    exit 2
  fi
done

printf '== Devtool GTK4 Control Center package ==\n'
printf 'Version: %s\nArchitecture: %s\nOutput: %s\n\n' "$version" "$arch" "$deb_path"

mkdir -p "$stage/DEBIAN" \
         "$stage/usr/bin" \
         "$stage/usr/lib/python3/dist-packages" \
         "$stage/usr/share/applications" \
         "$stage/usr/share/metainfo" \
         "$stage/usr/share/icons/hicolor/scalable/apps" \
         "$stage/usr/share/icons/hicolor/64x64/apps" \
         "$stage/usr/share/icons/hicolor/128x128/apps" \
         "$stage/usr/share/icons/hicolor/256x256/apps"

cp -a "$repo/devtool" "$stage/usr/lib/python3/dist-packages/devtool"
find "$stage/usr/lib/python3/dist-packages/devtool" -type d -name '__pycache__' -prune -exec "$rm_bin" -rf {} +
find "$stage/usr/lib/python3/dist-packages/devtool" -type f -name '*.pyc' -delete

cat > "$stage/usr/bin/devtool-control-center" <<'EOF'
#!/usr/bin/env python3
from devtool.desktop.control_center_cli import main
raise SystemExit(main())
EOF
cat > "$stage/usr/bin/devtool-gtk-control-center" <<'EOF'
#!/usr/bin/env python3
from devtool.desktop.control_center_cli import main
raise SystemExit(main())
EOF
cat > "$stage/usr/bin/devtool-control-center-hud" <<'EOF'
#!/usr/bin/env python3
from devtool.desktop.gtk_hud_cli import main
raise SystemExit(main())
EOF
cat > "$stage/usr/bin/devtool-gtk-hud" <<'EOF'
#!/usr/bin/env python3
from devtool.desktop.gtk_hud_cli import main
raise SystemExit(main())
EOF
chmod 0755 "$stage/usr/bin/devtool-control-center" \
           "$stage/usr/bin/devtool-gtk-control-center" \
           "$stage/usr/bin/devtool-control-center-hud" \
           "$stage/usr/bin/devtool-gtk-hud"

install -m 0644 "$root/packaging/io.github.mikeceead.DevtoolControlCenter.desktop" \
  "$stage/usr/share/applications/io.github.mikeceead.DevtoolControlCenter.desktop"
install -m 0644 "$root/packaging/io.github.mikeceead.DevtoolControlCenter.metainfo.xml" \
  "$stage/usr/share/metainfo/io.github.mikeceead.DevtoolControlCenter.metainfo.xml"
install -m 0644 "$root/packaging/io.github.mikeceead.DevtoolControlCenter.svg" \
  "$stage/usr/share/icons/hicolor/scalable/apps/io.github.mikeceead.DevtoolControlCenter.svg"
for size in 64 128 256; do
  install -m 0644 "$root/packaging/icons/${size}x${size}/io.github.mikeceead.DevtoolControlCenter.png" \
    "$stage/usr/share/icons/hicolor/${size}x${size}/apps/io.github.mikeceead.DevtoolControlCenter.png"
done

sed \
  -e "s/^Version:.*/Version: $version/" \
  -e "s/^Architecture:.*/Architecture: $arch/" \
  "$control_file" > "$stage/DEBIAN/control"
install -m 0755 "$root/packaging/debian/postinst" "$stage/DEBIAN/postinst"
install -m 0755 "$root/packaging/debian/postrm" "$stage/DEBIAN/postrm"

"$python_bin" -m compileall -q "$stage/usr/lib/python3/dist-packages/devtool"
find "$stage" -print0 | xargs -0 touch -h -d "@$source_date_epoch"
dpkg-deb --root-owner-group --uniform-compression -Zxz --build "$stage" "$deb_path"
sha256sum "$deb_path" > "$deb_path.sha256"
printf '\nBuilt package: %s\nChecksum: %s.sha256\n' "$deb_path" "$deb_path"

if "$verify_package"; then
  dpkg-deb --info "$deb_path" >/dev/null
  contents=$(dpkg-deb --contents "$deb_path")
  [[ "$contents" == *"./usr/bin/devtool-control-center"* ]] || { echo "error: GTK Control Center executable missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/bin/devtool-control-center-hud"* ]] || { echo "error: GTK HUD executable missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/lib/python3/dist-packages/devtool/desktop/gtk_control_center.py"* ]] || { echo "error: GTK Control Center module missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/lib/python3/dist-packages/devtool/desktop/gtk_hud.py"* ]] || { echo "error: GTK HUD module missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/share/applications/io.github.mikeceead.DevtoolControlCenter.desktop"* ]] || { echo "error: desktop entry missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/share/metainfo/io.github.mikeceead.DevtoolControlCenter.metainfo.xml"* ]] || { echo "error: metainfo missing from package" >&2; exit 1; }
  [[ "$contents" == *"./usr/share/icons/hicolor/128x128/apps/io.github.mikeceead.DevtoolControlCenter.png"* ]] || { echo "error: raster icon missing from package" >&2; exit 1; }
  if dpkg-deb -f "$deb_path" Depends | grep -Eq 'qml-module|libqt5|qtbase5|qtdeclarative'; then
    echo "error: GTK package unexpectedly depends on Qt/QML" >&2
    exit 1
  fi
  package_version=$(dpkg-deb -f "$deb_path" Version)
  [[ "$package_version" == "$version" ]] || { echo "error: package version mismatch: $package_version" >&2; exit 1; }
  sha256sum -c "$deb_path.sha256"
  printf 'GTK package verification passed.\n'
fi

if "$install_after_build"; then
  if command -v apt-get >/dev/null 2>&1; then
    install_cmd=(apt-get install --reinstall -y "$deb_path")
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
  printf 'Installed Devtool GTK4 Control Center successfully.\n'
  if "$delete_after_install"; then
    "$rm_bin" -f -- "$deb_path"
    printf 'Deleted package artifact after successful installation: %s\n' "$deb_path"
  fi
fi
