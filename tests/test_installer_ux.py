from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_installer_offers_platform_app_and_clipboard_service_workflows() -> None:
    script = ROOT / "install.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    help_text = subprocess.run([str(script), "--help"], check=True, text=True, stdout=subprocess.PIPE).stdout
    for flag in (
        "--app auto|desktop|android|none",
        "--with-app",
        "--without-app",
        "--force-app-build",
        "--with-clipboard-service",
        "--without-clipboard-service",
    ):
        assert flag in help_text
    source = script.read_text(encoding="utf-8")
    assert "Build if needed and install the ${APP_MODE} app?" in source
    assert "Install and enable the clipboard monitor service?" in source or "and enable the clipboard monitor service?" in source
    assert "Update and enable the clipboard monitor service?" not in source or "verb=Update" in source
    assert "sudo pm install -r" in source
    assert "--delete-after-install" in source


def test_android_build_exports_stable_trackable_apk() -> None:
    build = ROOT / "android" / "devtool-live-hud" / "build.sh"
    source = build.read_text(encoding="utf-8")
    assert "--tracked-apk" in source
    assert 'TRACKED_APK_PATH="$APP_DIR/devtool-live-hud-debug.apk"' in source
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!android/devtool-live-hud/devtool-live-hud-debug.apk" in ignore


def test_desktop_package_install_deletes_deb_only_after_success() -> None:
    script = ROOT / "desktop" / "devtool-control-center" / "package-deb.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    source = script.read_text(encoding="utf-8")
    install_pos = source.index('"${install_cmd[@]}"')
    delete_pos = source.index('rm -f -- "$deb_path"')
    assert install_pos < delete_pos
