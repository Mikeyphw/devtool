from __future__ import annotations

from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android" / "devtool-live-hud"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def test_android_app_source_is_bundled() -> None:
    assert (APP / "app" / "build.gradle").is_file()
    assert (APP / "build.sh").is_file()
    assert (ROOT / "build-android-hud.sh").is_file()


def test_manifest_declares_opt_in_overlay_service() -> None:
    root = ET.parse(APP / "app" / "src" / "main" / "AndroidManifest.xml").getroot()
    permissions = {node.attrib.get(ANDROID_NS + "name") for node in root.findall("uses-permission")}
    assert "android.permission.SYSTEM_ALERT_WINDOW" in permissions
    assert "android.permission.FOREGROUND_SERVICE" in permissions
    assert "android.permission.FOREGROUND_SERVICE_SPECIAL_USE" in permissions
    service = root.find("application/service")
    assert service is not None
    assert service.attrib[ANDROID_NS + "name"] == ".OverlayHudService"
    assert service.attrib[ANDROID_NS + "foregroundServiceType"] == "specialUse"


def test_overlay_service_uses_application_overlay_and_multi_run_cards() -> None:
    source = (APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "OverlayHudService.java").read_text(encoding="utf-8")
    assert "TYPE_APPLICATION_OVERLAY" in source
    assert "LinkedHashMap<String, HudUpdate> runs" in source
    assert "TERMINAL_LINGER_MS" in source
    assert "FOREGROUND_SERVICE_TYPE_SPECIAL_USE" in source


def test_build_script_syntax_and_override_flags() -> None:
    script = APP / "build.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    help_text = subprocess.run([str(script), "--help"], check=True, text=True, stdout=subprocess.PIPE).stdout
    for flag in (
        "--app-dir", "--sdk-dir", "--cmdline-zip", "--cmdline-url",
        "--java-home", "--platform", "--build-tools", "--apt-packages",
        "--clean-task", "--assemble-task", "--gradle-transforms-dir",
        "--apk-path", "--skip-system-deps", "--skip-sdk-install",
    ):
        assert flag in help_text
