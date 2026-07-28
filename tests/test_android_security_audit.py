from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"


def read(name: str) -> str:
    return (APP / name).read_text()


def test_root_bridge_commands_reject_shell_control_syntax():
    source = read("RootBridgeManager.java")
    assert "containsShellControlSyntax(String command)" in source
    assert "!containsShellControlSyntax(command)" in source
    assert "c == ';'" in source
    assert "c == '`'" in source
    assert "c == '<' || c == '>'" in source
    assert "c == '|' || c == '&'" in source
    assert "command.charAt(i + 1) == '('" in source
    assert "(?:devtool|devtool-androidos)(?: |$)" in source


def test_root_bridge_helper_commands_have_timeouts():
    source = read("RootBridgeManager.java")
    assert "rootCommand(String shell, long timeoutSeconds)" in source
    assert "process.waitFor(100L, TimeUnit.MILLISECONDS)" in source
    assert "process.destroyForcibly()" in source
    assert "return new CommandResult(124" in source
    assert "CONTROL_COMMAND_TIMEOUT_SECONDS" in source


def test_android_locale_sensitive_case_changes_use_root_locale():
    for filename in [
        "BridgeUpdateGate.java",
        "DevtoolRunConfig.java",
        "MainActivity.java",
        "RootBridgeManager.java",
    ]:
        source = read(filename)
        assert ".toLowerCase()" not in source
        assert ".toUpperCase()" not in source
        assert "Locale.ROOT" in source


def test_overlay_service_does_not_keep_strong_static_service_reference():
    source = read("OverlayHudService.java")
    assert "WeakReference<OverlayHudService>" in source
    assert "private static volatile OverlayHudService instance" not in source
    assert "activeInstance()" in source


def test_exported_bridge_and_tasker_receivers_are_token_and_action_hardened():
    bridge = read("BridgeReceiver.java")
    tasker = read("TaskerPluginReceiver.java")
    assert "BridgeSecrets.accepts(context" in bridge
    assert "isExpectedBridgeAction(context, action)" in bridge
    assert "Rejected bridge update without a run id" in bridge
    assert "MAX_TOAST_LENGTH" in bridge
    assert "TaskerAuthorization.matches(context" in tasker
    assert "intent.setExtrasClassLoader(TaskerPluginReceiver.class.getClassLoader())" in tasker
    assert "Tasker action bundle could not be read" in tasker
