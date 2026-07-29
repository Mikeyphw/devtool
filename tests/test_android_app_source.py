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
    assert "LinkedHashMap<String, HudUpdate> currentRuns" in source
    assert "Map<String, HudUpdate> pendingUpdates" in source
    assert "currentRuns.put(update.runId, update)" in source
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
    assert "TERMUX_PREFIX + \"/bin/zsh\"" in runner
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
    assert ".setCustomContentView(compact)" in notification
    assert 'new ActionSpec("Cancel"' in notification


def test_root_gitignore_excludes_generated_android_and_devtool_state() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("**/.gradle/", "**/build/", ".devtool/", "**/android-live-hud.token"):
        assert pattern in ignore
    assert "*.apk" in ignore
    assert "!android/devtool-live-hud/devtool-live-hud-debug.apk" in ignore


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


def test_termux_run_command_does_not_precreate_public_storage_log_directory() -> None:
    runner = _java_source("TermuxCommandRunner.java")
    assert ".logs/build-apk" not in runner
    assert "/storage/emulated" not in runner
    assert "shell.append(command)" in runner


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
    for operation in ("apply-overlay", "apply-overlays", "validate", "build", "test", "lint", "status"):
        assert operation in builder
    assert "RunStore.started" in runner
    assert "DEVTOOL_RUN_ID" in runner
    assert "DEVTOOL_ANDROID_HUD_TOKEN" in runner
    assert "DEVTOOL_ANDROID_HUD_COMPONENT" in runner
    assert "BridgeReceiver.class.getName()" in runner
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
    assert "DEVTOOL_HUD_SINGLETON=kill" not in config
    assert 'token.equals("cd")' in importer
    assert 'globalExtras.add("--hud-mode")' in importer
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
    assert "android-live-hud.package" in token
    assert "android-live-hud.component" in token
    assert "BridgeReceiver.class.getName()" in token
    assert "runBridgeOperation" in runner
    assert "BridgeTokenManager.handleResult" in result
    assert "LinearLayout tile(" in ui


def test_android_sources_do_not_reference_autonotification() -> None:
    source_dir = APP / "app" / "src" / "main" / "java"
    for path in source_dir.rglob("*.java"):
        text = path.read_text(encoding="utf-8")
        assert "AutoNotification" not in text
        assert "com.joaomgcd" not in text
        assert "fromHtml" not in text
        assert "Html." not in text


def test_optional_root_bridge_module_and_app_backend_are_bundled() -> None:
    module = ROOT / "root-module" / "devtool-bridge"
    assert (module / "module.prop").is_file()
    assert (module / "skip_mount").is_file()
    assert (module / "bin" / "devtool-root-bridge").is_file()
    module_prop = (module / "module.prop").read_text(encoding="utf-8")
    assert "id=devtool_bridge" in module_prop
    assert "Zygisk" in module_prop
    assert not (module / "zygisk").exists()

    source = _java_source("RootBridgeManager.java")
    main = _java_source("MainActivity.java")
    assert "/data/adb/modules/devtool_bridge/bin/devtool-root-bridge" in source
    assert "/data/adb/modules_update/devtool_bridge/bin/devtool-root-bridge" in source
    assert "Root bridge only accepts generated devtool" in source
    assert "DEVTOOL_ROOT_MODULE_MISSING" in source
    assert "cancel(Context context, String runId)" in source
    assert "Export module ZIP" in source
    assert "Devtool Root Bridge" in main
    assert "Use optional root bridge" in main
    assert "R.raw.devtool_root_bridge_module" in main
    bridge_script = (module / "bin" / "devtool-root-bridge").read_text(encoding="utf-8")
    assert '"enabled":%s' in bridge_script
    assert "bash" not in bridge_script
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
        assert "bin/devtool-root-bridge" in names
        assert "service.sh" not in names
        assert "post-fs-data.sh" not in names
        assert not any(name.startswith("zygisk/") for name in names)
        zf.testzip()


def test_android_hud_uses_dynamic_operation_segments_and_live_issue_counts() -> None:
    hud_update = _java_source("HudUpdate.java")
    overlay = _java_source("OverlayHudService.java")
    notification = _java_source("NotificationController.java")
    run_store = _java_source("RunStore.java")
    assert 'intent.getStringExtra("steps_json")' in hud_update
    assert "import org.json.JSONException;" in hud_update
    assert "catch (JSONException | RuntimeException ignored)" in hud_update
    assert "pipelineProgress()" in hud_update
    assert "displayProgress()" in hud_update
    assert "for (HudUpdate.Step step : update.steps)" in overlay
    assert 'append(update.errors).append(" errors · ")' in overlay
    assert "update.steps.size()" in notification
    assert "RemoteViews compact" in notification
    assert ".setCustomBigContentView(expanded)" in notification
    assert "displayProgress()" in notification
    assert "displayProgress()" in run_store


def test_chroot_run_uses_termux_visible_workdir() -> None:
    main = _java_source("MainActivity.java")
    assert 'if (config.useChroot)' in main
    assert 'configured.startsWith("/home/")' in main
    assert 'configured.startsWith("/root/")' in main
    assert 'return "~/";' in main


def test_android_notification_has_live_summary_actions_and_stale_update_gate() -> None:
    notification = _java_source("NotificationController.java")
    receiver = _java_source("BridgeReceiver.java")
    gate = _java_source("BridgeUpdateGate.java")
    action = _java_source("ActionReceiver.java")
    hud_update = _java_source("HudUpdate.java")
    assert '"Open log"' in notification
    assert '"Copy summary"' in notification
    assert "expandedText()" in notification
    assert "displayProgress()" in notification
    assert "BridgeUpdateGate.accept" in receiver
    assert 'intent.getLongExtra("sequence"' in gate
    assert "sequence < lastSequence" in gate
    assert "sequence == lastSequence && updatedAt <= lastUpdated" in gate
    assert "ACTION_COPY_SUMMARY" in action
    assert 'return "Devtool · " + phaseLabel()' in hud_update
    assert "if (succeeded()) return 100" in hud_update


def test_android_clipboard_paste_is_guarded_against_first_access_crashes() -> None:
    reader = _java_source("ClipboardTextReader.java")
    main = _java_source("MainActivity.java")
    tasker = _java_source("TaskerPluginActivity.java")
    assert "getPrimaryClip();" in reader
    assert "catch (SecurityException" in reader
    assert "catch (RuntimeException" in reader
    assert "ClipboardTextReader.read(this)" in main
    assert "ClipboardTextReader.read(this)" in tasker
    assert "coerceToText(this)" not in main
    assert "coerceToText(this)" not in tasker


def test_android_app_version_bumped_for_logs_and_repositories() -> None:
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "versionCode 31" in build
    assert "versionName '1.17.0'" in build



def test_android_notification_uses_exact_local_counters_and_overall_progress() -> None:
    notification = _java_source("NotificationController.java")
    update = _java_source("HudUpdate.java")
    assert "update.notificationCompactStatus()" in notification
    assert "update.displayProgress()" in notification
    assert "Notification.InboxStyle" in notification  # multi-run group summary only
    assert 'new ActionSpec("Cancel"' in notification
    assert 'new ActionSpec("Retry"' in notification
    assert 'current + "/" + total + " " + noun' in update
    assert 'return "Running lint"' in update
    assert '"Workspace " + Math.max(1, workspaceIndex) + " of " + workspaceTotal' in update
    assert '"Tests · "' in update
    assert '"Health · "' in update


def test_app_launched_termux_runs_have_bridge_ack_watchdog_logs_and_foreground_mode() -> None:
    runner = _java_source("TermuxCommandRunner.java")
    main = _java_source("MainActivity.java")
    store = _java_source("RunStore.java")
    builder = _java_source("DevtoolCommandBuilder.java")
    assert "PROGRESS_WATCHDOG_MS" in runner
    assert '"Termux shell started; waiting for devtool progress"' in runner
    assert "RunStore.appendDiagnostic" in runner
    assert "openTermux(context)" in runner
    assert "Open Termux in foreground while running" in main
    assert "Execution log" in main
    assert "launchLog" in store
    assert 'parts.add("android-bridge")' in builder


def test_termux_result_delivery_is_recorded_for_background_diagnostics() -> None:
    result = _java_source("TermuxResultService.java")
    assert "RunCommand result delivered" in result
    assert "RunStore.appendDiagnostic" in result


def test_android_builder_exposes_project_environment_and_selective_tests() -> None:
    builder = _java_source("DevtoolCommandBuilder.java")
    config = _java_source("DevtoolRunConfig.java")
    settings = _java_source("AppSettings.java")
    main = _java_source("MainActivity.java")
    for token in ("--project-type", "--execution-environment", "--test-profile", "--select", "--tag", "--exclude-tag", "--instrumented", "--list-tests"):
        assert token in builder
    for field in ("projectType", "executionEnvironment", "testProfile", "testSelectors", "instrumented"):
        assert field in config
        assert field in settings
    assert 'sectionTitle("3 · Test selection")' in main
    assert "Discover tests" in main
    assert "Show exact command" in main
    assert "Generated devtool command preview" in main


def test_android_notification_and_app_preserve_gradle_task_totals() -> None:
    update = _java_source("HudUpdate.java")
    notification = _java_source("NotificationController.java")
    main = _java_source("MainActivity.java")
    record = _java_source("RunRecord.java")
    store = _java_source("RunStore.java")

    assert 'intent.getIntExtra("current", -1)' in update
    assert 'intent.getIntExtra("total", -1)' in update
    assert 'intent.getStringExtra("unit_label")' in update
    assert 'return current + "/" + total' in update
    assert 'update.current + "/" + update.total' in notification
    assert 'record.current + "/" + record.total + " " + unit + " · " + record.progress + "%"' in main
    assert 'value.put("current", current)' in record
    assert 'value.put("total", total)' in record
    assert 'value.put("unit_label", unitLabel)' in record
    assert 'record.current = update.current' in store
    assert 'record.total = update.total' in store
    assert 'record.unitLabel = update.unitLabel' in store
