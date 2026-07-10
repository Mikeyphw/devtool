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


def test_manifest_declares_tasker_and_termux_integration() -> None:
    root = ET.parse(APP / "app" / "src" / "main" / "AndroidManifest.xml").getroot()
    permissions = {node.attrib.get(ANDROID_NS + "name") for node in root.findall("uses-permission")}
    assert "com.termux.permission.RUN_COMMAND" in permissions
    application = root.find("application")
    assert application is not None
    activities = {node.attrib.get(ANDROID_NS + "name") for node in application.findall("activity")}
    receivers = {node.attrib.get(ANDROID_NS + "name") for node in application.findall("receiver")}
    services = {node.attrib.get(ANDROID_NS + "name") for node in application.findall("service")}
    assert ".TaskerPluginActivity" in activities
    assert ".TaskerPluginReceiver" in receivers
    assert ".TermuxResultService" in services


def test_tasker_plugin_returns_termux_results_as_variables() -> None:
    source_dir = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
    protocol = (source_dir / "TaskerProtocol.java").read_text(encoding="utf-8")
    runner = (source_dir / "TermuxCommandRunner.java").read_text(encoding="utf-8")
    result_service = (source_dir / "TermuxResultService.java").read_text(encoding="utf-8")
    assert "com.twofortyfouram.locale.intent.action.FIRE_SETTING" in protocol
    assert "%devtool_exit_code" in protocol
    assert "%devtool_stdout" in protocol
    assert "%devtool_stderr" in protocol
    assert "com.termux.app.RunCommandService" in runner
    assert "com.termux.RUN_COMMAND_PENDING_INTENT" in runner
    assert 'getBundleExtra("result")' in result_service
    assert "TaskerProtocol.signalFinish" in result_service


def test_android_ui_uses_gtk_palette_monet_setting_and_desktop_icon() -> None:
    source_dir = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
    palette = (source_dir / "AppPalette.java").read_text(encoding="utf-8")
    settings = (source_dir / "AppSettings.java").read_text(encoding="utf-8")
    icon = (APP / "app" / "src" / "main" / "res" / "drawable" / "ic_launcher_legacy.xml").read_text(encoding="utf-8")
    for color in ("94, 228, 155", "104, 216, 255", "255, 209, 102", "11, 15, 20"):
        assert color in palette
    assert "system_accent1_200" in palette
    assert 'KEY_MONET = "monet"' in settings
    for color in ("#111827", "#5EE49B", "#68D8FF", "#FFD166"):
        assert color in icon


def test_overlay_receiver_does_not_restart_foreground_service_in_background() -> None:
    source = (APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "OverlayHudService.java").read_text(encoding="utf-8")
    publish = source[source.index("static void publish"):source.index("static void cancel")]
    assert "setEnabled(context, true)" not in publish
    notification = (APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "NotificationController.java").read_text(encoding="utf-8")
    assert '"Show HUD"' in notification


def test_root_gitignore_excludes_generated_android_and_devtool_state() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("**/.gradle/", "**/build/", "*.apk", ".devtool/", "**/android-live-hud.token"):
        assert pattern in ignore


def _java_source(name: str) -> str:
    return (APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / name).read_text(encoding="utf-8")


def test_tasker_receiver_requires_private_pairing_token() -> None:
    auth = _java_source("TaskerAuthorization.java")
    receiver = _java_source("TaskerPluginReceiver.java")
    activity = _java_source("TaskerPluginActivity.java")
    assert "MessageDigest.isEqual" in auth
    assert "isTrustedEditor(getCallingActivity())" in activity
    assert "KEY_AUTH_TOKEN" in activity
    assert "TaskerAuthorization.matches" in receiver


def test_termux_run_command_expands_shell_home_in_workdir() -> None:
    runner = _java_source("TermuxCommandRunner.java")
    paths = _java_source("TermuxPaths.java")
    assert "TermuxPaths.normalizeWorkdir(workdir)" in runner
    assert '"/$HOME/"' in paths
    assert '"${HOME}/"' in paths
    assert 'HOME = "/data/data/com.termux/files/home"' in paths


def test_termux_pending_intent_identity_is_not_reused() -> None:
    runner = _java_source("TermuxCommandRunner.java")
    assert "SecureRandom" in runner
    assert "PendingIntent.FLAG_CANCEL_CURRENT" in runner


def test_tasker_returns_official_error_message_variable() -> None:
    protocol = _java_source("TaskerProtocol.java")
    assert 'vars.putString("%errmsg"' in protocol


def test_android_frontend_has_visual_run_builder_history_and_shared_run_identity() -> None:
    source_dir = APP / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud"
    main = (source_dir / "MainActivity.java").read_text(encoding="utf-8")
    builder = (source_dir / "DevtoolCommandBuilder.java").read_text(encoding="utf-8")
    store = (source_dir / "RunStore.java").read_text(encoding="utf-8")
    runner = (source_dir / "TermuxCommandRunner.java").read_text(encoding="utf-8")
    for page in ("Overview", "Run", "History", "Settings"):
        assert page in main
    for operation in ("apply-overlay", "apply-overlays", "validate", "build", "test", "status"):
        assert operation in builder
    assert "RunStore.started" in runner
    assert "DEVTOOL_RUN_ID" in runner
    assert "DEVTOOL_ANDROID_HUD_TOKEN" in runner
    assert "MAX_RUNS" in store
    assert "Copy output" in main


def test_android_frontend_can_paste_and_visually_import_shell_commands() -> None:
    main = _java_source("MainActivity.java")
    importer = _java_source("DevtoolCommandImporter.java")
    settings = _java_source("AppSettings.java")
    assert "Paste and import command" in main
    assert "Paste command from clipboard" in main
    assert "Paste command from clipboard" in _java_source("TaskerPluginActivity.java")
    assert "DevtoolCommandImporter.parse" in main
    config = _java_source("DevtoolRunConfig.java")
    assert "DEVTOOL_HUD_SINGLETON=kill" in config
    assert 'token.equals("cd")' in importer
    assert 'hudMode.equals("desktop-window")' in importer
    assert "extraGlobalArguments" in settings
    assert "extraOperationArguments" in settings


def test_android_frontend_uses_dynamic_tiles_bottom_navigation_and_one_tap_token_pairing() -> None:
    main = _java_source("MainActivity.java")
    ui = _java_source("UiKit.java")
    token = _java_source("BridgeTokenManager.java")
    runner = _java_source("TermuxCommandRunner.java")
    result = _java_source("TermuxResultService.java")
    assert "buildBottomNavigation" in main
    assert "Termux bridge token" in main
    assert 'notifications ? "Manage" : "Grant access"' in main
    assert "Install token" in token
    assert "Update token" in token
    assert "DEVTOOL_BRIDGE_TOKEN_OK" in token
    assert "runBridgeOperation" in runner
    assert "BridgeTokenManager.handleResult" in result
    assert "LinearLayout tile(" in ui


def test_optional_root_bridge_module_and_app_backend_are_bundled() -> None:
    module = ROOT / "root-module" / "devtool-bridge"
    assert (module / "module.prop").is_file()
    assert (module / "skip_mount").is_file()
    assert (module / "service.sh").is_file()
    assert (module / "bin" / "devtool-root-bridge").is_file()
    module_prop = (module / "module.prop").read_text(encoding="utf-8")
    assert "id=devtool_bridge" in module_prop
    assert "Zygisk" in module_prop
    assert not (module / "zygisk").exists()

    source = _java_source("RootBridgeManager.java")
    main = _java_source("MainActivity.java")
    assert 'new ProcessBuilder("su", "-c", shell)' in source
    assert "Root bridge only accepts generated devtool" in source
    assert "DEVTOOL_ROOT_MODULE_MISSING" in source
    assert "cancel(Context context, String runId)" in source
    assert "Export module ZIP" in source
    assert "Devtool Root Bridge" in main
    assert "Use optional root bridge" in main
    assert "R.raw.devtool_root_bridge_module" in main
    bridge_script = (module / "bin" / "devtool-root-bridge").read_text(encoding="utf-8")
    assert '"enabled":%s' in bridge_script
    assert "KEY_ROOT_BRIDGE" in _java_source("TaskerProtocol.java")
    assert "RootBridgeManager.run" in _java_source("TaskerPluginReceiver.java")


def test_bundled_root_module_zip_is_valid_and_non_zygisk() -> None:
    import zipfile

    archive = APP / "app" / "src" / "main" / "res" / "raw" / "devtool_root_bridge_module.zip"
    assert archive.is_file()
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        assert "module.prop" in names
        assert "skip_mount" in names
        assert "service.sh" in names
        assert "bin/devtool-root-bridge" in names
        assert not any(name.startswith("zygisk/") for name in names)
        zf.testzip()
