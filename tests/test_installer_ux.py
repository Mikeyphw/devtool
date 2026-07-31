from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_installer_offers_platform_app_module_and_clipboard_workflows() -> None:
    script = ROOT / "install.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    help_text = subprocess.run(
        [str(script), "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    for flag in (
        "--app auto|desktop|android|none",
        "--with-app",
        "--without-app",
        "--force-app-build",
        "--with-root-module",
        "--without-root-module",
        "--force-root-module-build",
        "--with-clipboard-service",
        "--without-clipboard-service",
    ):
        assert flag in help_text

    source = script.read_text(encoding="utf-8")
    assert "Build if needed and install/update the ${APP_MODE} app?" in source
    assert "optional KernelSU root bridge module?" in source
    assert "and enable the clipboard monitor service?" in source
    assert "run_as_root pm install -r \"$staged_apk\"" in source
    assert "run_as_root ksud install \"$module_zip\"" in source
    assert "python3 -m devtool" in source
    assert "--target android_hud" in source
    assert "--chroot \"$chroot_name\"" in source
    assert "--delete-after-install" in source


def test_android_and_module_install_are_staged_below_tmpdir() -> None:
    script = ROOT / "install.sh"
    env = dict(os.environ)
    env.update(
        {
            "TERMUX_VERSION": "test",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TMPDIR": "/tmp/devtool-installer-test",
        }
    )
    result = subprocess.run(
        [
            str(script),
            "--dry-run",
            "--skip-core",
            "--app",
            "android",
            "--with-app",
            "--with-root-module",
            "--force-app-build",
            "--force-root-module-build",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    output = result.stdout
    assert "/tmp/devtool-installer-test/devtool-installer.dry-run/devtool-live-hud-debug.apk" in output
    assert "/tmp/devtool-installer-test/devtool-installer.dry-run/devtool_root_bridge_module.zip" in output
    assert "su -c pm\\ install\\ -r" in output
    assert "su -c ksud\\ install" in output
    assert "Building Android companion through Devtool chroot target android_hud" in output
    assert "/devtool_root_bridge_module.zip" not in output.replace(
        "/tmp/devtool-installer-test/devtool-installer.dry-run/devtool_root_bridge_module.zip",
        "",
    )


def test_repo_config_declares_chroot_android_hud_target() -> None:
    data = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    assert data["project"]["kind"] == "python"
    assert data["execution"]["chroot_name"] == "androidos"
    target = data["targets"]["android_hud"]
    assert target["type"] == "android"
    assert target["root"] == "android/devtool-live-hud"
    assert target["execution_environment"] == "chroot"
    assert target["chroot_name"] == "androidos"
    assert target["android"]["prefer_native_termux"] is False
    assert target["tasks"]["build"] == ["assembleDebug"]
    assert target["gradle"]["max_workers"] == 1


def test_android_build_exports_stable_trackable_apk() -> None:
    build = ROOT / "android" / "devtool-live-hud" / "build.sh"
    source = build.read_text(encoding="utf-8")
    assert "--tracked-apk" in source
    assert 'TRACKED_APK_PATH="$APP_DIR/devtool-live-hud-debug.apk"' in source
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!android/devtool-live-hud/devtool-live-hud-debug.apk" in ignore


def test_desktop_package_install_deletes_deb_only_after_success() -> None:
    script = ROOT / "desktop" / "gtk-control-center" / "package-deb.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    source = script.read_text(encoding="utf-8")
    install_pos = source.index('"${install_cmd[@]}"')
    delete_pos = source.index('"$rm_bin" -f -- "$deb_path"')
    assert install_pos < delete_pos
